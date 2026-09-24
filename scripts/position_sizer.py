#!/usr/bin/env python3
"""
仓位管理器（Position Sizer）
=============================

与 v5.3.30（选股+择时）解耦，专门管"买多少、组合怎么配"。

核心概念：
- 分析层（v5.3.30）：选股 + 择时，每只票独立分析 → 输出信号强度
- 仓位层（本模块）：根据信号强度 + 当前组合 + 行业 + 相关性 → 输出建议仓位
- 持仓层（portfolio.db）：记录交易历史

输入：标的信息 + 信号强度（1/3, 2/3, 3/3）+ 当前持仓
输出：建议股数 + 建议仓位（%）+ 检查项

用法：
  python3 position_sizer.py size 600000.SH 浦发银行 0.1 10.50
  # 标的 + 名称 + 资金占比 + 当前价 → 建议股数

  python3 position_sizer.py review  # 审查当前持仓（每只票是否触发了减仓/止损/加仓）

  python3 position_sizer.py rules  # 显示所有仓位规则
  python3 position_sizer.py set R001 20  # 修改某条规则的阈值
"""
import config
import sqlite3
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

DB = config.POSITIONS_DB


def get_conn(db=None):
    """db=None → 持仓库；资金域表传 config.CAPITAL_DB"""
    if db is None:
        db = DB
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def get_rules():
    """获取所有激活的仓位规则（资金库）"""
    conn = get_conn(config.CAPITAL_DB)
    cur = conn.execute("SELECT * FROM position_rules WHERE is_active=1 ORDER BY rule_id")
    rules = {r['rule_type']: dict(r) for r in cur.fetchall()}
    conn.close()
    return rules


def get_total_capital():
    """获取总资金（资金库）"""
    conn = get_conn(config.CAPITAL_DB)
    cur = conn.execute("SELECT value FROM meta WHERE key='initial_capital'")
    capital = float(cur.fetchone()['value'])
    conn.close()
    return capital


def get_current_positions():
    """获取当前持仓"""
    conn = get_conn()
    cur = conn.execute("SELECT * FROM positions WHERE active=1")
    positions = [dict(r) for r in cur.fetchall()]
    conn.close()
    return positions


def get_used_capital(positions):
    """已用资金"""
    return sum(p['cost_basis'] for p in positions)


def calc_base_size(signal_strength, rules, strategy_type='SHORT_TERM'):
    """根据信号强度 + 策略类型计算基础仓位"""
    if strategy_type == 'CORE_HOLDING':
        # v5.3.30-CH 底仓型：3 信号 50% / 2 信号 35% / 1 信号 20%
        signal_map = {
            3: 0.50,
            2: 0.35,
            1: 0.20,
        }
        return signal_map.get(signal_strength, 0.0)
    elif strategy_type == 'LONG_TERM':
        # v5.3.30-LT：5 信号 25% / 4 信号 20% / 3 信号 15%
        signal_map = {
            5: 0.25,
            4: 0.20,
            3: 0.15,
        }
        return signal_map.get(signal_strength, 0.0)
    else:
        # v5.3.30 短线：3 信号 17% / 2 信号 13% / 1 信号 7%
        signal_map = {
            3: 0.17,
            2: 0.13,
            1: 0.07,
        }
        return signal_map.get(signal_strength, 0.05)


def calc_industry_exposure(code, industry_map):
    """计算某行业当前持仓占比"""
    industry = industry_map.get(code, 'unknown')
    conn = get_conn()
    cur = conn.execute("SELECT * FROM positions WHERE active=1")
    positions = [dict(r) for r in cur.fetchall()]
    conn.close()
    return industry, sum(p['cost_basis'] for p in positions
                         if industry_map.get(p['code'], 'unknown') == industry)


def calc_correlation_count(code, correlation_map):
    """计算高相关票数"""
    group = correlation_map.get(code, 'unknown')
    conn = get_conn()
    cur = conn.execute("SELECT code FROM positions WHERE active=1")
    held_codes = [r['code'] for r in cur.fetchall()]
    conn.close()
    return group, sum(1 for c in held_codes if correlation_map.get(c, 'unknown') == group)


def size(code, name, signal_strength, current_price, industry='unknown', correlation_group='unknown', strategy_type='SHORT_TERM'):
    """
    计算建议仓位
    strategy_type: 'SHORT_TERM' (v5.3.30 短线) / 'LONG_TERM' (v5.3.30-LT 长线)
    """
    rules = get_rules()
    total_capital = get_total_capital()
    positions = get_current_positions()

    # 按策略类型筛选持仓（长线 / 短线 / 底仓分算）
    ch_positions = [p for p in positions if p.get('strategy_type') == 'CORE_HOLDING']
    lt_positions = [p for p in positions if p.get('strategy_type') == 'LONG_TERM']
    st_positions = [p for p in positions if p.get('strategy_type') not in ('LONG_TERM', 'CORE_HOLDING')]
    if strategy_type == 'CORE_HOLDING':
        target_positions = ch_positions
    elif strategy_type == 'LONG_TERM':
        target_positions = lt_positions
    else:
        target_positions = st_positions

    used = get_used_capital(target_positions)
    used_pct = used / total_capital * 100 if total_capital > 0 else 0

    checks = []

    # 1. 基础仓位（按信号强度 + 策略类型）
    base_pct = calc_base_size(signal_strength, rules, strategy_type) * 100
    final_pct = base_pct
    if strategy_type == 'CORE_HOLDING':
        if signal_strength < 1:
            checks.append(('基础仓位（v5.3.30-CH）', False, f'底仓型需 ≥ 1 信号，当前 {signal_strength}/3 → 不建仓'))
        else:
            checks.append(('基础仓位（v5.3.30-CH）', True, f'底仓型信号 {signal_strength}/3 → {base_pct}%'))
    elif strategy_type == 'LONG_TERM':
        if signal_strength < 3:
            checks.append(('基础仓位（v5.3.30-LT）', False, f'长线需 ≥ 3 信号，当前 {signal_strength}/5 → 不建仓'))
        else:
            checks.append(('基础仓位（v5.3.30-LT）', True, f'长线信号 {signal_strength}/5 → {base_pct}%'))
    else:
        checks.append(('基础仓位（v5.3.30）', True, f'短线信号 {signal_strength}/3 → {base_pct}%'))

    # 2. 总仓位上限（按策略类型）
    if strategy_type == 'CORE_HOLDING':
        max_total = rules.get('CH_MAX_TOTAL', {}).get('threshold_value', 80)
        max_label = '底仓型总仓位'
    elif strategy_type == 'LONG_TERM':
        max_total = rules.get('LT_MAX_TOTAL', {}).get('threshold_value', 50)
        max_label = '长线总仓位'
    else:
        max_total = rules.get('MAX_TOTAL', {}).get('threshold_value', 60)
        max_label = '总仓位上限'
    if used_pct + final_pct > max_total:
        old = final_pct
        final_pct = max(0, max_total - used_pct)
        checks.append((max_label, False, f'当前 {used_pct:.1f}% + 拟 {old:.1f}% > {max_total}% → 削减到 {final_pct:.1f}%'))
    else:
        checks.append((max_label, True, f'当前 {used_pct:.1f}% + 拟 {final_pct:.1f}% ≤ {max_total}%'))

    # 3. 行业集中度（按策略类型）
    if industry != 'unknown':
        if strategy_type == 'CORE_HOLDING':
            max_ind = rules.get('CH_MAX_INDUSTRY', {}).get('threshold_value', 70)
        elif strategy_type == 'LONG_TERM':
            max_ind = rules.get('LT_MAX_INDUSTRY', {}).get('threshold_value', 50)
        else:
            max_ind = rules.get('MAX_INDUSTRY', {}).get('threshold_value', 30)
        ind_name, ind_used = calc_industry_exposure(code, {code: industry})
        new_ind_pct = (ind_used + final_pct * total_capital / 100) / total_capital * 100
        if new_ind_pct > max_ind:
            old = final_pct
            final_pct = min(final_pct, max_ind - ind_used / total_capital * 100)
            checks.append(('行业集中度', False, f'{ind_name} 行业当前 {ind_used/total_capital*100:.1f}% + 拟 {old:.1f}% > {max_ind}% → 削减到 {final_pct:.1f}%'))
        else:
            checks.append(('行业集中度', True, f'{ind_name} 行业当前 {ind_used/total_capital*100:.1f}% + 拟 {final_pct:.1f}% ≤ {max_ind}%'))
    else:
        checks.append(('行业集中度', None, '未指定行业，跳过检查'))

    # 4. 相关性控制
    if correlation_group != 'unknown':
        max_corr = rules.get('MAX_CORRELATION', {}).get('threshold_value', 2)
        group, corr_count = calc_correlation_count(code, {code: correlation_group})
        if corr_count >= max_corr:
            checks.append(('相关性控制', False, f'高相关组 {group} 已持 {corr_count:.0f} 只 ≥ {max_corr:.0f} 只 → 建议跳过'))
        else:
            checks.append(('相关性控制', True, f'高相关组 {group} 已持 {corr_count:.0f} 只 < {max_corr:.0f} 只'))
    else:
        checks.append(('相关性控制', None, '未指定相关组，跳过检查'))

    # 5. 计算建议股数
    target_amount = final_pct / 100 * total_capital
    recommended_shares = int(target_amount / current_price / 100) * 100
    if recommended_shares < 100:
        recommended_shares = 0

    return {
        'code': code,
        'name': name,
        'signal_strength': signal_strength,
        'current_price': current_price,
        'total_capital': total_capital,
        'used_capital': used,
        'used_pct': used_pct,
        'base_pct': base_pct,
        'final_pct': final_pct,
        'target_amount': target_amount,
        'recommended_shares': recommended_shares,
        'strategy_type': strategy_type,
        'checks': checks,
    }


def show_rules():
    """显示所有仓位规则"""
    rules = get_rules()
    print("="*85)
    print("📐 仓位管理规则")
    print("="*85)
    for rid, r in rules.items():
        print(f"\n  [{r['rule_id']}] {r['rule_name']}")
        print(f"    类型: {r['rule_type']}")
        print(f"    阈值: {r['threshold_value']}{'%' if r['threshold_value'] is not None else ''}")
        if r['threshold_text']:
            print(f"    复合: {r['threshold_text']}")
        print(f"    说明: {r['description']}")


def review():
    """审查当前持仓（每只票是否触发了减仓/止损/加仓/长线恶化）"""
    conn = get_conn()
    cur = conn.execute("SELECT * FROM positions WHERE active=1")
    positions = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not positions:
        print("📭 当前无持仓")
        return

    print("="*85)
    print("🔍 持仓审查（v5.3.30 短线 + v5.3.30-LT 长线）")
    print("="*85)

    for p in positions:
        strategy = p.get('strategy_type', 'SHORT_TERM')
        # 查最新 P&L
        conn = get_conn()
        cur = conn.execute("""
            SELECT * FROM daily_pnl WHERE code=? ORDER BY trade_date DESC LIMIT 1
        """, (p['code'],))
        latest = cur.fetchone()
        conn.close()

        print(f"\n  标的: {p['name']} ({p['code']})")
        if strategy == 'LONG_TERM':
            strategy_label = '🟢 长线 v5.3.30-LT'
        elif strategy == 'CORE_HOLDING':
            strategy_label = '🟣 底仓型 v5.3.30-CH'
        else:
            strategy_label = '🟡 短线 v5.3.30'
        print(f"  策略: {strategy_label}")
        print(f"  成本: {p['cost_basis']:,.0f} 元 (成本价 {p['entry_price']:.2f})")
        if latest:
            print(f"  现价: {latest['close_price']:.2f} 元")
            print(f"  浮盈: {latest['unrealized_pnl']:+,.0f} 元 ({latest['return_pct']:+.2f}%)")
            print(f"  P&L 日期: {latest['trade_date']}")

        actions = []

        if strategy == 'CORE_HOLDING':
            # ============ 底仓型检查（v5.3.30-CH）============
            # 1. 永久恶化监测（理论性触发）
            actions.append(('🟣 永久监测', '需查公司基本面：是否退市/破产/被接管'))
            # 2. 分红监测
            actions.append(('🟣 分红监测', '需查 dividend：是否正常分红'))
            # 3. 浮亏无关（基本不卖）
            if latest and latest['return_pct'] < 0:
                actions.append(('🟣 持有（分红票浮亏正常）', f'浮亏 {latest["return_pct"]:+.2f}% 不减仓（等分红 + 复利）'))
            elif latest and latest['return_pct'] > 0:
                actions.append(('🟣 持有（继续累计分红）', f'浮盈 {latest["return_pct"]:+.2f}% 不止盈（让复利）'))
            # 4. 加仓候选（不是定投，是有闲钱买点）
            actions.append(('🟣 加仓：有闲钱买点', '不定时 / 任何价格 / 任何金额（用户自由）'))
            actions.append(('🟣 加仓候选: 下跌加倍', '股息率 > 6% 时建议加倍'))

        if strategy == 'LONG_TERM':
            # ============ 长线检查（v5.3.30-LT）============
            # 1. 红线 1: 浮盈变浮亏（用户底线：绝不让盈利变亏损）
            if latest and latest['unrealized_pnl'] <= 0:
                actions.append(('🔴 立即清仓', f'浮盈已变浮亏 {latest["unrealized_pnl"]:+,.0f} 元（用户红线）'))
            else:
                # 1A. 长线加仓候选（3 类基本面驱动）
                actions.append(('🟢 加仓 1 候选', '业绩超预期：需查单季营收同比 ≥ 20%'))
                actions.append(('🟢 加仓 2 候选', '估值修复：需查 PE 历史分位 < 30%'))
                actions.append(('🟢 加仓 3 候选', '行业景气：行业指数 3 个月持续向上'))
                # 1B. 加回候选（基本面恢复后加回）
                actions.append(('🟢 加回 候选', '基本面恢复：业绩+行业+估值 三者改善'))
                # 1C. v5.3.30 微淼财务硬过滤触发提示
                actions.append(('🟣 微淼过滤', '需查 R067 ROE>20% / R062 毛利率>40% / R063 资产负债率<60% / R060 OCF>0.8 / R064 分红>25% / R065 上市>3年'))
                actions.append(('🟣 R059 触发', '动态股息率 > 10 年期国债 → 加仓信号'))
                actions.append(('🟣 R067 严格', '连续 5 年 ROE > 20%（微淼海选硬指标 · 比 R011 ≥ 15% 更严）'))
                actions.append(('🟣 R068 择时', '深证 A 股 PE < 20（大盘估值窗口 · 系统性风险过滤）'))
                actions.append(('🟢 R070 加仓', '个股 TTM PE < 15（与 R015 互补 · 独立加仓信号）'))
                # 1D. 恶化监测（5 类）→ 减仓候选（不是清仓）
                actions.append(('🟡 恶化监测 1', '业绩证伪：单季营收<0 OR 净利<0 → 减仓 1/3 候选'))
                actions.append(('🟡 恶化监测 2', '行业景气下行：连续30日<MA60 → 减仓 1/3 候选'))
                actions.append(('🟡 恶化监测 3', '估值泡沫：PE>90% 分位 → 减仓 1/3 候选'))
                actions.append(('🟡 恶化监测 4', '护城河消失：份额连续2季下降 → 减仓 1/3 候选'))
                actions.append(('🟡 恶化监测 5', '资金面恶化：主力连续30日流出 → 减仓 1/3 候选'))
                actions.append(('🟠 减仓分批', '1 个信号-1/3 / 2-3 个-2/3 / 5 个-清仓'))
                actions.append(('🟢 加回触发', '基本面恢复（业绩+行业+估值）→ 加回原仓位'))
                # 1E. v5.3.30 微淼卖出条件
                actions.append(('🟠 R061 卖出', '动态股息率 < 10Y 国债 / 3 → 减仓候选'))
                actions.append(('🟠 R066 卖出', '发现明显更优投资机会（红利 ETF > 当前）→ 切换候选'))
                actions.append(('🟠 R069 卖出', '个股 TTM PE > 50（绝对估值高估 · 海天 67 卖 / 茅台 40 持）'))

                # 2. 红线 2: 浮盈回撤 30%
                conn = get_conn()
                cur = conn.execute("""
                    SELECT MAX(unrealized_pnl) FROM daily_pnl WHERE code=?
                """, (p['code'],))
                peak = cur.fetchone()[0] or 0
                conn.close()
                if peak > 0:
                    drawdown = (peak - latest['unrealized_pnl']) / peak * 100
                    if drawdown >= 30.0:
                        actions.append(('🟠 减仓 50%', f'浮盈从峰值 {peak:,.0f} 回撤 {drawdown:.1f}% ≥ 30%'))
                    elif drawdown >= 15.0:
                        actions.append(('🟡 警戒', f'浮盈回撤 {drawdown:.1f}% ≥ 15%'))

                # 3. 分批止盈金字塔
                ret = latest['return_pct']
                if ret >= 200.0:
                    actions.append(('🏆 顶级止盈 清仓', f'浮盈 {ret:+.1f}% ≥ 200%'))
                elif ret >= 100.0:
                    actions.append(('🟢 分批 3 减仓 75%', f'浮盈 {ret:+.1f}% ≥ 100%（只留底仓）'))
                elif ret >= 50.0:
                    actions.append(('🟢 分批 2 减仓 50%', f'浮盈 {ret:+.1f}% ≥ 50%'))
                elif ret >= 20.0:
                    actions.append(('🟢 分批 1 减仓 25%', f'浮盈 {ret:+.1f}% ≥ 20%'))

            # 持仓时长（长线不设时间止损）
            if p.get('t30_days_left') is not None:
                days_held = 30 - p['t30_days_left']
                if days_held < 365:
                    actions.append(('⏰ 长线持有', f'已持 {days_held} 天 / 目标 ≥ 365 天'))
                else:
                    actions.append(('⏰ 长线达标', f'已持 {days_held} 天 / 目标 ≥ 365 天 ✅'))

        else:
            # ============ 短线检查（v5.3.30）============
            # 1. 止损检查
            stop_loss = -5.0
            if latest and latest['return_pct'] <= stop_loss:
                actions.append(('🔴 强平', f'跌幅 {latest["return_pct"]:.2f}% ≤ {stop_loss}%'))

            # 1A. 红线: 浮盈变浮亏（保护利润）
            if latest and latest['unrealized_pnl'] <= 0:
                actions.append(('🔴 立即清仓', f'浮盈变浮亏 {latest["unrealized_pnl"]:+,.0f} 元（保护利润）'))

            # 2. v5.3.30 解法 C：顺势加仓 + 分批止盈
            if latest and latest['return_pct'] >= 5.0:
                # 检查量比/MA5 需要实时数据，先输出"加仓候选"
                actions.append(('🟢 加仓 1 候选', f'浮盈 {latest["return_pct"]:+.2f}% ≥ 5%（量比/MA5 盘中确认）'))
            if latest and latest['return_pct'] >= 10.0:
                actions.append(('🟢 加仓 2 候选', f'浮盈 {latest["return_pct"]:+.2f}% ≥ 10%（量比/MA5 盘中确认）'))

            # 3. 分批止盈金字塔
            if latest:
                ret = latest['return_pct']
                # 浮盈回撤 50% 减仓（R005A）
                conn = get_conn()
                cur = conn.execute("""
                    SELECT MAX(unrealized_pnl) FROM daily_pnl WHERE code=?
                """, (p['code'],))
                peak = cur.fetchone()[0] or 0
                conn.close()
                if peak > 0:
                    drawdown = (peak - latest['unrealized_pnl']) / peak * 100
                    if drawdown >= 50.0:
                        actions.append(('🟠 减仓 50%', f'浮盈从峰值 {peak:,.0f} 回撤 {drawdown:.1f}% ≥ 50%'))

                # 分批止盈
                if ret >= 50.0:
                    actions.append(('🏆 顶级止盈 清仓', f'浮盈 {ret:+.1f}% ≥ 50%'))
                elif ret >= 30.0:
                    actions.append(('🟢 分批 2 减仓 50%', f'浮盈 {ret:+.1f}% ≥ 30%（破 MA5 / 长上影 触发）'))
                elif ret >= 20.0:
                    actions.append(('🟢 分批 1 减仓 25%', f'浮盈 {ret:+.1f}% ≥ 20%（量比衰减 < 1.0 触发）'))

            # 2. 高量阴线减仓（简化判断）
            if latest and latest['return_pct'] < -1.5:
                actions.append(('🟡 减仓 30%', f'单日跌 {latest["return_pct"]:.2f}% 触发高量阴线条件'))

            # 3. 缩量阴跌清仓
            conn = get_conn()
            cur = conn.execute("""
                SELECT close_price FROM daily_pnl
                WHERE code=? ORDER BY trade_date DESC LIMIT 3
            """, (p['code'],))
            recent = [r[0] for r in cur.fetchall()]
            conn.close()
            if len(recent) >= 3 and recent[0] < recent[1] < recent[2]:
                actions.append(('🟠 清仓', f'连续 3 天阴跌 {recent[2]:.2f} → {recent[1]:.2f} → {recent[0]:.2f}'))

            # 4. v5.3.30 加仓 1
            if latest and latest['return_pct'] >= 5.0:
                actions.append(('🟢 加仓 1', f'浮盈 {latest["return_pct"]:.2f}% ≥ 5%（量比/MA5 盘中确认）'))

            # 5. v5.3.30 加仓 2
            if latest and latest['return_pct'] >= 10.0:
                actions.append(('🟢 加仓 2', f'浮盈 {latest["return_pct"]:.2f}% ≥ 10%'))

            # 6. T+30 兜底
            if p.get('t30_days_left') is not None and p['t30_days_left'] <= 0:
                actions.append(('⏰ T+30 兜底', f'已超 {abs(p["t30_days_left"])} 天'))
            elif p.get('t30_days_left') is not None and p['t30_days_left'] <= 5:
                actions.append(('⏰ T+30 临近', f'剩余 {p["t30_days_left"]} 天'))

        if actions:
            print(f"  操作建议:")
            for action, reason in actions:
                print(f"    {action}: {reason}")
        else:
            print(f"  操作建议: ⚪ 持有（无信号）")


def set_rule(rule_id, new_value):
    """修改某条规则的阈值"""
    conn = get_conn()
    conn.execute("""
        UPDATE position_rules SET threshold_value=?, updated_at=?
        WHERE rule_id=?
    """, (float(new_value), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), rule_id))
    conn.commit()
    cur = conn.execute("SELECT * FROM position_rules WHERE rule_id=?", (rule_id,))
    rule = cur.fetchone()
    conn.close()
    if rule:
        print(f"✅ 规则 {rule_id} 已更新: {rule['rule_name']} = {new_value}")
    else:
        print(f"❌ 规则 {rule_id} 不存在")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == 'size':
        # size <code> <name> <signal_strength> <price> [industry] [correlation_group] [strategy_type]
        if len(args) < 4:
            print("用法: python3 position_sizer.py size <code> <name> <signal_strength> <price> [industry] [correlation_group] [strategy_type]")
            print("     strategy_type: SHORT_TERM / LONG_TERM / CORE_HOLDING")
            sys.exit(1)
        code, name, sig, price = args[0], args[1], int(args[2]), float(args[3])
        industry = args[4] if len(args) > 4 else 'unknown'
        corr = args[5] if len(args) > 5 else 'unknown'
        strat = args[6] if len(args) > 6 else 'SHORT_TERM'
        result = size(code, name, sig, price, industry, corr, strat)
        print("="*85)
        print(f"📐 仓位建议: {name} ({code})")
        print("="*85)
        print(f"\n  信号强度: {result['signal_strength']}/3")
        print(f"  当前价: {result['current_price']:.2f} 元")
        print(f"  总资金: {result['total_capital']:,.0f} 元")
        print(f"  已用: {result['used_capital']:,.0f} 元 ({result['used_pct']:.1f}%)")
        print(f"\n  基础仓位: {result['base_pct']:.1f}%")
        print(f"  最终仓位: {result['final_pct']:.1f}%")
        print(f"  建议金额: {result['target_amount']:,.0f} 元")
        print(f"  建议股数: {result['recommended_shares']} 股")
        print(f"\n  检查项:")
        for name_, passed, reason in result['checks']:
            icon = '✅' if passed is True else ('❌' if passed is False else '⏭️')
            print(f"    {icon} {name_}: {reason}")

    elif cmd == 'review':
        review()
    elif cmd == 'rules':
        show_rules()
    elif cmd == 'set':
        if len(args) != 2:
            print("用法: python3 position_sizer.py set <rule_id> <new_value>")
            sys.exit(1)
        set_rule(args[0], args[1])
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)
