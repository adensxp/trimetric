#!/usr/bin/env python3
"""
持仓交易管理器（Portfolio Manager）
====================================

功能：
- 建仓/加仓/减仓/清仓 → 自动写 trades + positions + events
- 每日收盘后更新 P&L 快照
- 查询当前持仓 / 交易历史 / 资金流水
- 计算收益率 / 浮盈

用法：
  python3 portfolio_manager.py show                # 显示当前持仓
  python3 portfolio_manager.py show-history        # 显示交易历史
  python3 portfolio_manager.py show-pnl            # 显示每日 P&L

  # 建仓（首笔）
  python3 portfolio_manager.py open 002475.SZ 立讯精密 55.68 300

  # 加仓
  python3 portfolio_manager.py add 002475.SZ 立讯精密 57.50 100 'v5.3.30 加仓 1 信号'

  # 减仓
  python3 portfolio_manager.py reduce 002475.SZ 立讯精密 56.50 100 'S1 跌破 MA5 减半仓'

  # 清仓
  python3 portfolio_manager.py close 002475.SZ 立讯精密 57.00 '' 'T+30 兜底清仓'

  # 每日收盘后更新 P&L
  python3 portfolio_manager.py update-pnl 2026-09-02 002475.SZ 56.92

  # 元信息
  python3 portfolio_manager.py capital 100000      # 设置本金
  python3 portfolio_manager.py deposit 10000       # 入金
  python3 portfolio_manager.py withdraw 5000      # 出金
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


def open_position(code, name, price, shares, position_pct=None, notes=''):
    """建仓（v5.3.30 自动算手续费）"""
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = datetime.now().strftime('%Y-%m-%d')
    amount = float(price) * int(shares)
    t30 = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    t30_days_left = 30

    if position_pct is None:
        conn_cap = get_conn(config.CAPITAL_DB)
        capital = float(conn_cap.execute("SELECT value FROM meta WHERE key='initial_capital'").fetchone()['value'])
        conn_cap.close()
        position_pct = round(amount / capital * 100, 2)

    # v5.3.30: 自动算手续费
    from fee_calculator import calculate_fee
    fees = calculate_fee(code, 'BUY', amount)
    net_amount = fees['net_amount']  # 实际支付（含费）

    # 写 positions
    cur.execute("""
        INSERT OR REPLACE INTO positions (
            code, name, shares, cost_basis, entry_date, entry_price,
            position_pct, t30_deadline, t30_days_left, add_count, add_total_shares,
            strategy_version, active, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'v5.3.30', 1, ?, ?, ?)
    """, (code, name, shares, amount, today, price, position_pct, t30, t30_days_left, notes, now, now))

    # 写 trades（含手续费字段）
    cur.execute("""
        INSERT INTO trades (trade_date, trade_time, code, name, action, shares, price,
            amount, commission, stamp_tax, transfer_fee, total_fee, net_amount, exchange,
            position_pct, reason, strategy_version, notes)
        VALUES (?, ?, ?, ?, 'BUY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '建仓', 'v5.3.30', ?)
    """, (today, datetime.now().strftime('%H:%M:%S'), code, name,
          shares, price, amount, fees['commission'], fees['stamp_tax'],
          fees['transfer_fee'], fees['total_fee'], net_amount, fees['exchange'],
          position_pct, notes))

    # 写 events
    cur.execute("""
        INSERT INTO events (event_date, event_time, event_type, code, title, description)
        VALUES (?, ?, 'OPEN_POSITION', ?, ?, ?)
    """, (today, datetime.now().strftime('%H:%M:%S'), code,
          f"建仓 {name} {shares}股 @ {price}",
          f"仓位 {position_pct}% · T+30 兜底 {t30} · 手续费 {fees['total_fee']:.2f}元"))

    # 写 cash_flow（净额 = 成交 + 手续费）—— 资金库
    conn_cap = get_conn(config.CAPITAL_DB)
    conn_cap.execute("""
        INSERT INTO cash_flow (flow_date, type, amount, balance_after, description)
        VALUES (?, 'BUY', ?, ?, ?)
    """, (today, -net_amount, None,
          f"建仓 {name} {shares}股 @{price} + 手续费 {fees['total_fee']:.2f}元"))
    conn_cap.commit()

    conn.commit()
    print(f"✅ 建仓成功：{name} ({code})")
    print(f"   持股 {shares} 股 × {price} = {amount:,.0f} 元 ({position_pct}%)")
    print(f"   手续费: {fees['total_fee']:.2f} 元 (佣金{fees['commission']:.2f} + 印花税{fees['stamp_tax']:.2f} + 过户费{fees['transfer_fee']:.2f})")
    print(f"   实际支付: {net_amount:,.2f} 元")
    print(f"   T+30 兜底：{t30} ({t30_days_left} 天) · 策略: v5.3.30")


def add_position(code, price, shares, notes=''):
    """加仓（v5.3.30 自动算手续费）"""
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = datetime.now().strftime('%Y-%m-%d')
    add_amount = float(price) * int(shares)

    # 查当前持仓
    cur.execute("SELECT * FROM positions WHERE code=? AND active=1", (code,))
    pos = cur.fetchone()
    if not pos:
        print(f"❌ 没有 {code} 的持仓，请先建仓")
        return

    # v5.3.30: 自动算手续费
    from fee_calculator import calculate_fee
    fees = calculate_fee(code, 'BUY', add_amount)
    net_amount = fees['net_amount']

    new_shares = pos['shares'] + shares
    new_cost_basis = pos['cost_basis'] + add_amount
    new_entry_price = new_cost_basis / new_shares
    new_add_count = pos['add_count'] + 1
    new_add_total = pos['add_total_shares'] + shares

    # 更新 positions
    cur.execute("""
        UPDATE positions SET
            shares=?, cost_basis=?, entry_price=?, add_count=?, add_total_shares=?,
            updated_at=?
        WHERE code=? AND active=1
    """, (new_shares, new_cost_basis, new_entry_price, new_add_count, new_add_total, now, code))

    # 写 trades（含手续费字段）
    cur.execute("""
        INSERT INTO trades (trade_date, trade_time, code, name, action, shares, price,
            amount, commission, stamp_tax, transfer_fee, total_fee, net_amount, exchange,
            position_pct, reason, strategy_version, notes)
        VALUES (?, ?, ?, ?, 'ADD', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v5.3.30', ?)
    """, (today, datetime.now().strftime('%H:%M:%S'), code, pos['name'],
          shares, price, add_amount, fees['commission'], fees['stamp_tax'],
          fees['transfer_fee'], fees['total_fee'], net_amount, fees['exchange'],
          pos['position_pct'], f"加仓 {new_add_count} 信号", notes))

    # 写 events
    cur.execute("""
        INSERT INTO events (event_date, event_time, event_type, code, title, description)
        VALUES (?, ?, 'ADD_POSITION', ?, ?, ?)
    """, (today, datetime.now().strftime('%H:%M:%S'), code,
          f"加仓 {pos['name']} {shares}股 @ {price}",
          f"加权成本 {new_entry_price:.2f} · 累计加仓 {new_add_count} 次 · 费 {fees['total_fee']:.2f}元"))

    conn_cap = get_conn(config.CAPITAL_DB)
    conn_cap.execute("""
        INSERT INTO cash_flow (flow_date, type, amount, description)
        VALUES (?, 'BUY', ?, ?)
    """, (today, -net_amount, f"加仓 {pos['name']} {shares}股 @{price} + 费 {fees['total_fee']:.2f}元"))
    conn_cap.commit()

    conn.commit()
    print(f"✅ 加仓成功：{pos['name']}")
    print(f"   本次 {shares} 股 @ {price} = {add_amount:,.0f} 元 · 费 {fees['total_fee']:.2f}")
    print(f"   累计 {new_shares} 股 / 加权成本 {new_entry_price:.2f} / 加仓 {new_add_count} 次")


def reduce_position(code, price, shares, notes=''):
    """减仓"""
    conn = get_conn()
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    reduce_amount = float(price) * int(shares)

    cur.execute("SELECT * FROM positions WHERE code=? AND active=1", (code,))
    pos = cur.fetchone()
    if not pos:
        print(f"❌ 没有 {code} 的持仓")
        return

    if shares > pos['shares']:
        print(f"❌ 减仓数 {shares} 大于持仓 {pos['shares']}")
        return

    new_shares = pos['shares'] - shares
    sold_ratio = shares / pos['shares']
    new_cost_basis = pos['cost_basis'] * (1 - sold_ratio)
    sell_pnl_gross = reduce_amount - pos['cost_basis'] * sold_ratio
    sell_pct = round(sold_ratio * 100, 2)
    # bugfix(F821): 卖出费用/实际到账/净盈亏此前从未计算，实际卖出必 NameError
    from fee_calculator import calculate_fee
    fees = calculate_fee(code, 'SELL', reduce_amount)
    net_amount = fees['net_amount']  # 实际到账（扣除手续费）
    sell_pnl_net = sell_pnl_gross - fees['total_fee']

    if new_shares == 0:
        cur.execute("UPDATE positions SET active=0, shares=0, updated_at=? WHERE code=?",
                    (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), code))
        action_label = '清仓'
    else:
        cur.execute("UPDATE positions SET shares=?, cost_basis=?, updated_at=? WHERE code=? AND active=1",
                    (new_shares, new_cost_basis, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), code))
        action_label = '减仓'

    cur.execute("""
        INSERT INTO trades (trade_date, trade_time, code, name, action, shares, price,
            amount, commission, stamp_tax, transfer_fee, total_fee, net_amount, exchange,
            position_pct, reason, strategy_version, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'v5.3.30', ?)
    """, (today, datetime.now().strftime('%H:%M:%S'), code, pos['name'],
          'SELL' if new_shares == 0 else 'REDUCE', shares, price, reduce_amount,
          fees['commission'], fees['stamp_tax'], fees['transfer_fee'],
          fees['total_fee'], net_amount, fees['exchange'],
          sell_pct, action_label, notes))

    cur.execute("""
        INSERT INTO events (event_date, event_time, event_type, code, title, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (today, datetime.now().strftime('%H:%M:%S'),
          'CLOSE_POSITION' if new_shares == 0 else 'REDUCE_POSITION', code,
          f"{action_label} {pos['name']} {shares}股 @ {price}",
          f"毛盈亏 {sell_pnl_gross:+.0f}元 · 净盈亏 {sell_pnl_net:+.0f}元 · 费 {fees['total_fee']:.2f}元 · {'已清仓' if new_shares == 0 else f'剩余 {new_shares} 股'}"))

    conn_cap = get_conn(config.CAPITAL_DB)
    conn_cap.execute("""
        INSERT INTO cash_flow (flow_date, type, amount, description)
        VALUES (?, 'SELL', ?, ?)
    """, (today, net_amount, f"{action_label} {pos['name']} {shares}股 @{price} - 费 {fees['total_fee']:.2f}元"))
    conn_cap.commit()

    conn.commit()
    print(f"✅ {action_label}成功：{pos['name']}")
    print(f"   本次 {shares} 股 @ {price} = {reduce_amount:,.0f} 元")
    print(f"   手续费: {fees['total_fee']:.2f} 元 · 实际到账: {net_amount:,.2f} 元")
    print(f"   毛盈亏: {sell_pnl_gross:+.0f} 元 · 净盈亏: {sell_pnl_net:+.0f} 元")
    if new_shares == 0:
        print(f"   ✅ 已清仓")


def close_position(code, price, notes=''):
    """清仓（全部卖出）"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM positions WHERE code=? AND active=1", (code,))
    pos = cur.fetchone()
    if not pos:
        print(f"❌ 没有 {code} 的持仓")
        return
    reduce_position(code, price, pos['shares'], notes)


def show():
    """显示当前持仓"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM positions WHERE active=1")
    positions = cur.fetchall()
    if not positions:
        print("📭 当前无持仓")
        return
    print("="*85)
    print("📊 当前持仓")
    print("="*85)
    for p in positions:
        print(f"\n  标的: {p['name']} ({p['code']})")
        print(f"  持股: {p['shares']} 股")
        print(f"  成本: {p['cost_basis']:,.0f} 元 ({p['entry_price']:.2f}/股)")
        print(f"  仓位: {p['position_pct']}%")
        print(f"  建仓: {p['entry_date']}")
        print(f"  T+30 兜底: {p['t30_deadline']} (剩余 {p['t30_days_left']} 天)")
        print(f"  加仓: {p['add_count']} 次 / 累计 {p['add_total_shares']} 股")
        print(f"  策略: {p['strategy_version']}")


def show_history():
    """显示交易历史"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, code, name, action, shares, price, amount, reason, notes
        FROM trades ORDER BY trade_date, id
    """)
    trades = cur.fetchall()
    if not trades:
        print("📭 无交易记录")
        return
    print("="*85)
    print("📜 交易历史")
    print("="*85)
    for t in trades:
        print(f"  {t['trade_date']}  {t['name']:8s}  {t['action']:6s}  "
              f"{t['shares']:4d}股 @ {t['price']:6.2f}  = {t['amount']:>10,.0f}元  "
              f"({t['reason'] or ''})")


def show_pnl():
    """显示 P&L"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM daily_pnl WHERE code IN (SELECT code FROM positions WHERE active=1)
        ORDER BY trade_date DESC LIMIT 30
    """)
    pnls = cur.fetchall()
    if not pnls:
        print("📭 无 P&L 记录")
        return
    print("="*85)
    print("💰 每日 P&L（最近 30 天）")
    print("="*85)
    print(f"{'日期':<12} {'名称':<8} {'股数':<6} {'成本':<10} {'现价':<8} {'市值':<12} {'浮盈':<10} {'收益率':<8}")
    print("-"*85)
    for p in pnls:
        ret_sign = '+' if p['return_pct'] > 0 else ''
        print(f"  {p['trade_date']:<12} {p['name'] or '':<8} {p['shares']:<6} "
              f"{p['cost_basis']:<10.0f} {p['close_price']:<8.2f} {p['market_value']:<12,.0f} "
              f"{p['unrealized_pnl']:>+10.0f} {ret_sign}{p['return_pct']:>6.2f}%")


def report():
    """盈亏分析报表"""
    conn = get_conn()
    cur = conn.cursor()

    # 查询当前持仓
    cur.execute("SELECT * FROM positions WHERE active=1")
    positions = cur.fetchall()
    if not positions:
        print("📭 当前无持仓")
        return

    print("="*85)
    print("📊 盈亏分析报表")
    print("="*85)

    initial_capital = float(get_conn(config.CAPITAL_DB).execute("SELECT value FROM meta WHERE key='initial_capital'").fetchone()['value'])

    for p in positions:
        # 查询 P&L 历史
        cur.execute("""
            SELECT trade_date, close_price, market_value, unrealized_pnl, return_pct
            FROM daily_pnl WHERE code=? ORDER BY trade_date
        """, (p['code'],))
        pnls = cur.fetchall()
        if not pnls:
            print(f"\n  {p['name']} ({p['code']}) - 无 P&L 数据")
            continue

        first = pnls[0]
        last = pnls[-1]
        max_pnl = max(pnls, key=lambda x: x['unrealized_pnl'])
        min_pnl = min(pnls, key=lambda x: x['unrealized_pnl'])

        # 持仓天数
        from datetime import datetime
        entry_date = datetime.strptime(p['entry_date'], '%Y-%m-%d')
        last_date = datetime.strptime(last['trade_date'], '%Y-%m-%d')
        days = (last_date - entry_date).days

        # 平均日收益
        avg_daily_return = last['return_pct'] / days if days > 0 else 0

        # 年化（按 30 天 T+30 周期）
        annualized = avg_daily_return * 365

        # 累计盈亏
        total_pnl = last['unrealized_pnl']
        total_pct = last['return_pct']

        # 胜率（上涨天数 / 总天数）
        win_days = sum(1 for x in pnls if x['unrealized_pnl'] > 0)
        win_rate = win_days / len(pnls) * 100

        print(f"\n  标的: {p['name']} ({p['code']})")
        print(f"  持仓: {p['shares']} 股 × {p['entry_price']:.2f} = {p['cost_basis']:,.0f} 元")
        print(f"  仓位: {p['position_pct']}% (本金 {initial_capital:,.0f} 元)")
        print(f"  策略: {p['strategy_version']}")
        print()
        print(f"  【总体表现（{first['trade_date']} ~ {last['trade_date']}）】")
        print(f"    总成本: {p['cost_basis']:,.0f} 元")
        print(f"    当前市值: {last['market_value']:,.0f} 元")
        print(f"    累计盈亏: {total_pnl:+,.0f} 元（{total_pct:+.2f}%）")
        print(f"    最大浮盈: {max_pnl['unrealized_pnl']:+,.0f} 元（{max_pnl['trade_date']}）")
        print(f"    最大浮亏: {min_pnl['unrealized_pnl']:+,.0f} 元（{min_pnl['trade_date']}）")
        print(f"    平均日收益: {avg_daily_return:+.3f}%/天")
        print(f"    持仓天数: {days} 天")
        print(f"    年化收益率: {annualized:+.2f}%（按 365 天年化）")
        print()
        print(f"  【胜率统计】")
        print(f"    上涨天数: {win_days} / {len(pnls)} = {win_rate:.1f}%")
        print(f"    下跌天数: {len(pnls)-win_days} / {len(pnls)} = {100-win_rate:.1f}%")
        print()
        print(f"  【当前状态】")
        cur.execute("SELECT * FROM positions WHERE code=? AND active=1", (p['code'],))
        pos = cur.fetchone()
        if pos:
            print(f"    最新收盘: {last['close_price']:.2f} 元")
            print(f"    当前浮盈: {last['unrealized_pnl']:+,.0f} 元")
            print(f"    T+30 剩余: {pos['t30_days_left']} 天")


def risk():
    """风险指标"""
    import math
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT code, name, shares, cost_basis FROM positions WHERE active=1")
    positions = cur.fetchall()
    if not positions:
        print("📭 当前无持仓")
        return

    print("="*85)
    print("📊 风险指标")
    print("="*85)

    for p in positions:
        cur.execute("""
            SELECT trade_date, close_price, market_value, unrealized_pnl, return_pct
            FROM daily_pnl WHERE code=? ORDER BY trade_date
        """, (p['code'],))
        pnls = cur.fetchall()
        if len(pnls) < 2:
            print(f"\n  {p['name']} ({p['code']}) - 数据不足（< 2 天）")
            continue

        # 1. 最大回撤
        max_dd = 0
        max_dd_date = None
        peak_pnl = pnls[0]['unrealized_pnl']
        for x in pnls:
            if x['unrealized_pnl'] > peak_pnl:
                peak_pnl = x['unrealized_pnl']
            dd = x['unrealized_pnl'] - peak_pnl
            if dd < max_dd:
                max_dd = dd
                max_dd_date = x['trade_date']

        max_dd_pct = (max_dd / p['cost_basis']) * 100

        # 2. 日收益率序列
        daily_returns = []
        for i in range(1, len(pnls)):
            prev = pnls[i-1]['market_value']
            curr = pnls[i]['market_value']
            daily_returns.append((curr - prev) / prev * 100)

        # 3. 波动率（年化）
        if len(daily_returns) > 1:
            mean = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std = math.sqrt(variance)
            annual_vol = std * math.sqrt(252)  # 年化
        else:
            mean = 0
            std = 0
            annual_vol = 0

        # 4. 最大单日涨幅 / 跌幅
        max_up = max(daily_returns) if daily_returns else 0
        max_dn = min(daily_returns) if daily_returns else 0

        # 5. 夏普比率（假设无风险利率 2%）
        risk_free_daily = 2.0 / 252  # 2% 年化
        if std > 0:
            sharpe = (mean - risk_free_daily) / std * math.sqrt(252)
        else:
            sharpe = 0

        # 6. 索提诺比率（只用下行波动）
        downside_returns = [r for r in daily_returns if r < 0]
        if len(downside_returns) > 1:
            downside_var = sum(r ** 2 for r in downside_returns) / len(downside_returns)
            downside_dev = math.sqrt(downside_var)
            sortino = (mean - risk_free_daily) / downside_dev * math.sqrt(252) if downside_dev > 0 else 0
        else:
            sortino = 0

        # 7. 胜率 / 盈亏比
        win_count = sum(1 for r in daily_returns if r > 0)
        loss_count = sum(1 for r in daily_returns if r < 0)
        win_rate = win_count / len(daily_returns) * 100 if daily_returns else 0

        avg_win = sum(r for r in daily_returns if r > 0) / win_count if win_count > 0 else 0
        avg_loss = abs(sum(r for r in daily_returns if r < 0) / loss_count) if loss_count > 0 else 0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # 8. 当前回撤（vs 最大浮盈）
        peak_pnl_abs = max(x['unrealized_pnl'] for x in pnls)
        current_pnl = pnls[-1]['unrealized_pnl']
        current_dd = current_pnl - peak_pnl_abs
        current_dd_pct = (current_dd / peak_pnl_abs) * 100 if peak_pnl_abs > 0 else 0

        print(f"\n  标的: {p['name']} ({p['code']})")
        print(f"  数据天数: {len(pnls)}")
        print()
        print(f"  【回撤指标】")
        print(f"    最大回撤: {max_dd:+.0f} 元（{max_dd_pct:+.2f}%，{max_dd_date or 'N/A'}）")
        print(f"    当前回撤: {current_dd:+.0f} 元（{current_dd_pct:+.2f}%，相对峰值）")
        print()
        print(f"  【波动率】")
        print(f"    日均收益: {mean:+.3f}%")
        print(f"    日波动率: {std:.3f}%")
        print(f"    年化波动率: {annual_vol:.2f}%")
        print(f"    最大单日涨: {max_up:+.2f}%")
        print(f"    最大单日跌: {max_dn:+.2f}%")
        print()
        print(f"  【风险调整收益】")
        print(f"    夏普比率: {sharpe:.3f}（无风险利率 2%）")
        print(f"    索提诺比率: {sortino:.3f}（仅下行风险）")
        print()
        print(f"  【胜率/盈亏比】")
        print(f"    日胜率: {win_count}/{len(daily_returns)} = {win_rate:.1f}%")
        print(f"    平均盈利: {avg_win:+.2f}%")
        print(f"    平均亏损: {avg_loss:.2f}%")
        print(f"    盈亏比: {profit_loss_ratio:.2f}")
        print()
        print(f"  【风险评级】")
        if max_dd_pct < -3:
            rating = "🔴 高风险"
        elif max_dd_pct < -1.5:
            rating = "🟡 中风险"
        else:
            rating = "🟢 低风险"
        print(f"    评级: {rating}（基于最大回撤）")


def chart(start_date=None, end_date=None):
    """每日收益曲线 HTML 报告"""
    import math
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT code, name FROM positions WHERE active=1")
    positions = cur.fetchall()
    if not positions:
        print("📭 当前无持仓")
        return

    # HTML 模板
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>v5.3.30 持仓收益曲线</title>
<style>
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
  .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
  h1 { color: #1a1a1a; border-bottom: 3px solid #4CAF50; padding-bottom: 10px; }
  h2 { color: #333; margin-top: 30px; }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }
  .stat { background: linear-gradient(135deg, #4CAF50, #45a049); color: white; padding: 20px; border-radius: 8px; text-align: center; }
  .stat.negative { background: linear-gradient(135deg, #f44336, #d32f2f); }
  .stat.neutral { background: linear-gradient(135deg, #2196F3, #1976D2); }
  .stat .value { font-size: 24px; font-weight: bold; margin-top: 8px; }
  .stat .label { font-size: 14px; opacity: 0.9; }
  table { width: 100%; border-collapse: collapse; margin-top: 20px; }
  th { background: #4CAF50; color: white; padding: 12px; text-align: left; }
  td { padding: 10px 12px; border-bottom: 1px solid #eee; }
  tr:hover { background: #f8f8f8; }
  .positive { color: #4CAF50; font-weight: bold; }
  .negative { color: #f44336; font-weight: bold; }
  .chart { margin: 30px 0; padding: 20px; background: #fafafa; border-radius: 8px; }
  .chart-title { text-align: center; font-size: 18px; color: #333; margin-bottom: 15px; }
  .bars { display: flex; align-items: end; justify-content: space-between; height: 200px; border-bottom: 2px solid #333; padding: 0 10px; }
  .bar { flex: 1; background: linear-gradient(to top, #4CAF50, #45a049); margin: 0 2px; border-radius: 4px 4px 0 0; position: relative; min-width: 30px; transition: all 0.3s; }
  .bar:hover { background: linear-gradient(to top, #45a049, #357a38); }
  .bar.negative { background: linear-gradient(to top, #f44336, #d32f2f); }
  .bar-label { position: absolute; bottom: -25px; left: 50%; transform: translateX(-50%); font-size: 11px; color: #666; white-space: nowrap; }
  .bar-value { position: absolute; top: -22px; left: 50%; transform: translateX(-50%); font-size: 11px; color: #333; font-weight: bold; }
</style>
</head>
<body>
<div class="container">
<h1>📈 v5.3.30 持仓收益曲线</h1>
'''

    initial_capital = float(get_conn(config.CAPITAL_DB).execute("SELECT value FROM meta WHERE key='initial_capital'").fetchone()['value'])

    for p in positions:
        cur.execute("""
            SELECT trade_date, close_price, market_value, unrealized_pnl, return_pct
            FROM daily_pnl WHERE code=?
        """, (p['code'],))
        all_pnls = cur.fetchall()
        if not all_pnls:
            continue

        if start_date:
            pnls = [x for x in all_pnls if x['trade_date'] >= start_date]
        else:
            pnls = all_pnls
        if end_date:
            pnls = [x for x in pnls if x['trade_date'] <= end_date]
        if not pnls:
            continue

        first = pnls[0]
        last = pnls[-1]
        max_pnl = max(pnls, key=lambda x: x['unrealized_pnl'])
        min_pnl = min(pnls, key=lambda x: x['unrealized_pnl'])

        html += f'''
<h2>📊 {p['name']} ({p['code']})</h2>

<div class="stats">
  <div class="stat {'negative' if last['unrealized_pnl'] < 0 else ''}">
    <div class="label">累计盈亏</div>
    <div class="value">{last['unrealized_pnl']:+,.0f} 元</div>
  </div>
  <div class="stat {'negative' if last['return_pct'] < 0 else ''}">
    <div class="label">累计收益率</div>
    <div class="value">{last['return_pct']:+.2f}%</div>
  </div>
  <div class="stat neutral">
    <div class="label">最大浮盈</div>
    <div class="value">{max_pnl['unrealized_pnl']:+,.0f} 元</div>
    <div style="font-size:11px;opacity:0.8">({max_pnl['trade_date']})</div>
  </div>
  <div class="stat neutral">
    <div class="label">最大浮亏</div>
    <div class="value">{min_pnl['unrealized_pnl']:+,.0f} 元</div>
    <div style="font-size:11px;opacity:0.8">({min_pnl['trade_date']})</div>
  </div>
</div>

<div class="chart">
  <div class="chart-title">每日浮盈走势（{first['trade_date']} ~ {last['trade_date']}）</div>
  <div class="bars">
'''
        for x in pnls:
            pnl = x['unrealized_pnl']
            cost = last['unrealized_pnl'] - pnl  # 估算成本
            height = max(2, abs(pnl) / max(abs(max_pnl['unrealized_pnl']), abs(min_pnl['unrealized_pnl']), 1) * 180)
            cls = 'negative' if pnl < 0 else ''
            sign = '+' if pnl > 0 else ''
            date_short = x['trade_date'][5:]  # MM-DD
            tip_date = x['trade_date']
            html += f'    <div class="bar {cls}" style="height: {height}px;" title="{tip_date}: {pnl:+.0f}元">\n'
            html += f'      <span class="bar-value">{sign}{pnl:.0f}</span>\n'
            html += f'      <span class="bar-label">{date_short}</span>\n'
            html += f'    </div>\n'

        html += '''  </div>
</div>

<h3>📅 每日明细</h3>
<table>
  <tr><th>日期</th><th>收盘价</th><th>市值</th><th>浮盈</th><th>收益率</th></tr>
'''
        for x in reversed(pnls):
            ret_cls = 'positive' if x['unrealized_pnl'] > 0 else 'negative'
            html += f'''  <tr>
    <td>{x['trade_date']}</td>
    <td>{x['close_price']:.2f}</td>
    <td>{x['market_value']:,.0f}</td>
    <td class="{ret_cls}">{x['unrealized_pnl']:+,.0f} 元</td>
    <td class="{ret_cls}">{x['return_pct']:+.2f}%</td>
  </tr>
'''
        html += '</table>\n'

    html += '''
</div>
</body>
</html>'''

    # 输出文件
    output_path = config.CHART_FILE
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ HTML 报告已生成：{output_path}")
    print(f"   包含 {len(positions)} 个标的的收益曲线")


def update_pnl(date, code, close_price):
    """更新每日 P&L 快照"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM positions WHERE code=? AND active=1", (code,))
    pos = cur.fetchone()
    if not pos:
        print(f"❌ 没有 {code} 的持仓")
        return

    shares = pos['shares']
    cost = pos['cost_basis']
    market_value = shares * close_price
    unrealized_pnl = market_value - cost
    return_pct = (unrealized_pnl / cost) * 100

    cur.execute("""
        INSERT OR REPLACE INTO daily_pnl (
            trade_date, code, name, shares, cost_basis, close_price,
            market_value, unrealized_pnl, return_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (date, code, pos['name'], shares, cost, close_price, market_value, unrealized_pnl, return_pct))

    conn.commit()
    print(f"✅ P&L 更新：{pos['name']} {date}")
    print(f"   收盘 {close_price} · 浮盈 {unrealized_pnl:+.0f} 元 ({return_pct:+.2f}%)")


def set_capital(amount):
    """设置初始本金（资金库）"""
    conn = get_conn(config.CAPITAL_DB)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('initial_capital', ?)", (str(amount),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('current_capital', ?)", (str(amount),))
    conn.commit()
    print(f"✅ 本金已设置：{amount:,.0f} 元")


def deposit(amount, desc=''):
    """入金"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key='current_capital'")
    capital = float(cur.fetchone()['value'])
    new_capital = capital + amount
    cur.execute("UPDATE meta SET value=? WHERE key='current_capital'", (str(new_capital),))
    cur.execute("""
        INSERT INTO cash_flow (flow_date, type, amount, balance_after, description)
        VALUES (?, 'DEPOSIT', ?, ?, ?)
    """, (datetime.now().strftime('%Y-%m-%d'), amount, new_capital, desc or '入金'))
    conn.commit()
    print(f"✅ 入金 {amount:,.0f} 元，账户余额 {new_capital:,.0f}")


def withdraw(amount, desc=''):
    """出金"""
    deposit(-amount, desc or '出金')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == 'show':
        show()
    elif cmd == 'show-history':
        show_history()
    elif cmd == 'show-pnl':
        show_pnl()
    elif cmd == 'open':
        if len(args) != 4:
            print("用法: python3 portfolio_manager.py open <code> <name> <price> <shares>")
            sys.exit(1)
        open_position(args[0], args[1], float(args[2]), int(args[3]))
    elif cmd == 'add':
        if len(args) < 3:
            print("用法: python3 portfolio_manager.py add <code> <price> <shares> [notes]")
            sys.exit(1)
        notes = args[3] if len(args) > 3 else ''
        add_position(args[0], float(args[1]), int(args[2]), notes)
    elif cmd == 'reduce':
        if len(args) < 3:
            print("用法: python3 portfolio_manager.py reduce <code> <price> <shares> [notes]")
            sys.exit(1)
        notes = args[3] if len(args) > 3 else ''
        reduce_position(args[0], float(args[1]), int(args[2]), notes)
    elif cmd == 'close':
        if len(args) < 2:
            print("用法: python3 portfolio_manager.py close <code> <price> [notes]")
            sys.exit(1)
        notes = args[2] if len(args) > 2 else ''
        close_position(args[0], float(args[1]), notes)
    elif cmd == 'update-pnl':
        if len(args) != 3:
            print("用法: python3 portfolio_manager.py update-pnl <date> <code> <close_price>")
            sys.exit(1)
        update_pnl(args[0], args[1], float(args[2]))
    elif cmd == 'report':
        report()
    elif cmd == 'risk':
        risk()
    elif cmd == 'chart':
        sd = args[0] if len(args) > 0 else None
        ed = args[1] if len(args) > 1 else None
        chart(sd, ed)
    elif cmd == 'capital':
        set_capital(float(args[0]))
    elif cmd == 'deposit':
        deposit(float(args[0]))
    elif cmd == 'withdraw':
        withdraw(float(args[0]))
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)
