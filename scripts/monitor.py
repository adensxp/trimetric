#!/usr/bin/env python3
"""
高量战法盘中完整监控（每个时间点 9:35/10:00/11:00/13:30/14:30 触发）
================================
- 完整检查：建仓 + 加仓 + 减仓 + 清仓
- 集成 微淼完整方法论 + 原始高量战法 B/S/C 规则

⭐ B/S/C 规则（量价 + 趋势）
  - B1-B8 加仓规则（量价齐升 + 趋势确认）
  - S1-S8 减仓规则（量价背离 + 趋势遇阻）
  - C1-C6 清仓规则（量价反转 + 趋势破位）
  - 3 日定性法则（T+1/T+2/T+3 站稳支撑）
  - 高量支撑/压力位（实体低/实体高）

⭐ V5.3.30 新增建仓时机判定系统（R090-R094）：
  - R090 趋势阶段判定（底部筑底/启动/主升/顶部 4 阶段）
  - R091 位置百分位（60 日区间位置，建仓安全区 30-70%）
  - R092 底部反转形态确认（双底/底分型/突破下降趋势线）
  - R093 MACD 背离判定（底背离买入 / 顶背离卖出）
  - R094 量价时空共振（建仓 5 条件：底部+位置+量价+MACD+大盘择时）

高量战法 7 步决策流程（完整版）：
  第一步：建仓区判断（L1-L4 左侧 + 已有持仓）
  第二步：建仓时机 R090-R094（V5.3.30 新增 - 必过 5 条件）
  第三步：极端趋势检查（B8 条件：连续 3 日量价齐升 3 日涨幅 ≥ 8%）
  第四步：高量定性 + 左右衔接（T+1~T+3 站稳支撑）
  第五步：清仓信号 C1-C6（最高优先级）
  第六步：减仓信号 S1-S8
  第七步：加仓信号 B1-B8

裁决总纲：安全 > 收益，减仓 > 加仓，观望 > 操作。

历史升级:
  - 大盘择时：深证 PE 20 → 沪深 300 PE 18 + 多指数加权
  - 长线 PE：统一 15 → 行业分桶（价值 8-12 / 平衡 18-25 / 成长 30-40）
  - 底仓 PE：统一 20 → 行业分桶 + ROE>12% + 股息率>3% 三重过滤
  - 短线 PE：30 → 40（题材股放宽）
  - 极值保护：PE<0 亏损 / PE>100 极值 都过滤
  - 加仓/止盈：浮盈百分比 → B/S/C 量价+趋势 规则（灵魂回归）
  - V5.3.30 建仓时机：5 条件共振（趋势阶段 + 位置百分位 + 底部形态 + MACD 背离 + 大盘择时）
"""
import config
log = config.setup_logging("monitor")
import rules
import sqlite3
import urllib.request
import re
import json
from datetime import datetime


# ⭐ 申万 Top 10: 升级到 494 只（沪深 300 + 申万 31 行业 Top 10）
STOCK_POOL_FILE = config.STOCK_POOL_500
DB_PATH = config.MASTER_DB
PORTFOLIO_DB = config.POSITIONS_DB
CANDIDATES_DB = config.CANDIDATES_DB


# ========== 原始高量战法参数（已迁移 rules.py 纯函数库，此处保留别名）==========
C1_THRESHOLDS = rules.C1_THRESHOLDS
B8_MIN_GAIN_3D = rules.B8_MIN_GAIN_3D
B8_MIN_VOLUME_TREND = rules.B8_MIN_VOLUME_TREND
B4_MAX_DAYS = rules.B4_MAX_DAYS
S3_DAYS = rules.S3_DAYS
S5_MIN_RISE_DAYS = rules.S5_MIN_RISE_DAYS
S5_VOL_INCREASE = rules.S5_VOL_INCREASE
S7_VOL_INCREASE = rules.S7_VOL_INCREASE
S7_BODY_DECREASE = rules.S7_BODY_DECREASE


def load_pool():
    """加载 226 只股票池"""
    with open(STOCK_POOL_FILE, 'r') as f:
        data = json.load(f)
    pool = []
    batches = data.get('batches', {})
    if isinstance(batches, dict):
        for batch in batches.values():
            pool.extend(batch)
    elif isinstance(batches, list):
        for batch in batches:
            pool.extend(batch)
    return pool


def get_realtime_quote(code):
    """从腾讯财经拉取实时数据"""
    try:
        # 600519.SH -> sh600519
        if code.endswith('.SH'):
            qcode = 'sh' + code[:6]
        else:
            qcode = 'sz' + code[:6]
        url = f'http://qt.gtimg.cn/q={qcode}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'http://finance.qq.com/'
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode('gbk', errors='ignore')
        m = re.search(r'"([^"]+)"', data)
        if not m:
            return None
        parts = m.group(1).split('~')
        if len(parts) < 50:
            return None
        return {
            'name': parts[1],
            'code': parts[2],
            'current': float(parts[3]) if parts[3] else 0,
            'prev_close': float(parts[4]) if parts[4] else 0,
            'open': float(parts[5]) if parts[5] else 0,
            'change_pct': float(parts[32]) if parts[32] else 0,
            'vol_ratio': min(float(parts[49]), 10.0) if float(parts[49]) > 10 else float(parts[49]),
        }
    except Exception as e:
        log.warning("实时行情拉取失败 %s: %s", code, e)
        return None


def get_ma5(code):
    """从数据库获取 MA5"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT close_price FROM stock_daily
        WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 5
    """, (code,))
    closes = [r[0] for r in cur.fetchall()]
    conn.close()
    return sum(closes) / len(closes) if closes else 0


def get_ma20(code):
    """v5.3.30 新增：MA20 长期均线"""
    try:
        import urllib.request
        url = f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code.replace(".SH","").replace(".SZ","").lower()},day,2026-08-01,2026-09-11,30,qfq'
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode('gbk'))
        key = f'{code.lower().split(".")[0]}{code.split(".")[1].lower()}'
        # 试不同的 key
        for k in [key, code.lower(), f'{code.split(".")[0].lower()}{code.split(".")[1].lower()}']:
            if k in data.get('data', {}):
                klines = data['data'][k].get('qfqday') or data['data'][k].get('day', [])
                if len(klines) >= 20:
                    closes = [float(k[2]) for k in klines[-20:]]
                    return sum(closes) / len(closes)
        return 0
    except Exception as e:
        log.warning("MA20 拉取失败 %s: %s", code, e)
        return 0


def get_ma10(code):
    """从数据库获取 MA10"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT close_price FROM stock_daily
        WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 10
    """, (code,))
    closes = [r[0] for r in cur.fetchall()]
    conn.close()
    return sum(closes) / len(closes) if closes else 0


# ========== V5.3.30 新增：建仓时机判定 R090-R094 ==========

def get_60d_high_low(code):
    """获取 60 日高低点（用于 R091 位置百分位）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT MAX(high_price), MIN(low_price), MAX(close_price), MIN(close_price)
        FROM stock_daily
        WHERE stock_code = ? AND trade_date >= DATE('now', '-60 days')
    ''', (code,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] is not None:
        return {'high': row[0], 'low': row[1], 'max_close': row[2], 'min_close': row[3]}
    # 退化到全表
    cur = sqlite3.connect(DB_PATH).cursor()
    cur.execute('''
        SELECT MAX(high_price), MIN(low_price), MAX(close_price), MIN(close_price)
        FROM stock_daily WHERE stock_code = ?
    ''', (code,))
    row = cur.fetchone()
    if row and row[0] is not None and row[0] != row[1]:
        return {'high': row[0], 'low': row[1], 'max_close': row[2], 'min_close': row[3]}
    return None


def get_60d_position(code, current_price):
    """R091 位置百分位（rules.compute_position 的 DB 封装）"""
    return rules.compute_position(get_60d_high_low(code), current_price)


def get_trend_stage(code):
    """R090 趋势阶段判定（rules.compute_trend_stage 的 DB 封装）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT trade_date, close_price, high_price, low_price, volume "
        "FROM stock_daily WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 60",
        (code,))
    rows = cur.fetchall()
    conn.close()
    return rules.compute_trend_stage(rows)


def get_macd(code, fast=12, slow=26, signal=9):
    """R093 MACD 计算 + 背离判定（rules.compute_macd 的 DB 封装）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT trade_date, close_price FROM stock_daily "
        "WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 30",
        (code,))
    rows = cur.fetchall()
    conn.close()
    closes = [r[1] for r in rows[::-1]]
    return rules.compute_macd(closes, fast, slow, signal)


def check_bottom_reversal(code):
    """R092 底部反转形态确认（rules.compute_bottom_reversal 的 DB 封装）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT trade_date, open_price, high_price, low_price, close_price, volume "
        "FROM stock_daily WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 20",
        (code,))
    rows = cur.fetchall()
    conn.close()
    return rules.compute_bottom_reversal(rows)


def check_entry_conditions_v5_3_30(code, current_price, market_ok=True):
    """R094 量价时空共振 — 建仓 5 条件综合判定（rules.evaluate_entry 的 DB 封装，全 AND）"""
    trend = get_trend_stage(code)
    position = get_60d_position(code, current_price)
    bottom = check_bottom_reversal(code)
    macd = get_macd(code)
    return rules.evaluate_entry(trend, position, bottom, macd, market_ok)


def get_recent_klines(code, n=10):
    """获取最近 N 天 K 线数据（含 OHLCV）
    返回: [(trade_date, open, high, low, close, volume), ...] （从早到晚排序）
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT trade_date, open_price, high_price, low_price, close_price, volume
        FROM stock_daily
        WHERE stock_code = ? ORDER BY trade_date DESC LIMIT ?
    """, (code, n))
    rows = cur.fetchall()
    conn.close()
    # 翻转：从早到晚排序
    return list(reversed(rows))


def get_60d_avg_volatility(code):
    """获取 60 日均振幅（%）
    振幅 = (high - low) / low * 100
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("""
        SELECT high_price, low_price FROM stock_daily
        WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 60
    """, (code,))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return 0
    amplitudes = [(h - l) / l * 100 for h, l in rows if l > 0]
    return sum(amplitudes) / len(amplitudes) if amplitudes else 0


def get_volatility_class(code):
    """波动率分档（rules.volatility_class 的 DB 封装）"""
    return rules.volatility_class(get_60d_avg_volatility(code))


def get_stock_pe_ttm(code):
    """获取 TTM PE（R069 用）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT pe_ttm FROM stock_pe_ttm WHERE stock_code=?', (code,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0]
    return None


def get_dividend_ratio(code):
    """获取滚动股息率（R078 用）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT dividend_ratio FROM stock_pe_ttm WHERE stock_code=?', (code,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0]
    return None


# ⭐ P0-1: W16 4 红旗黑名单
def is_redflag_blacklisted(code):
    """检查是否在 W16 4 红旗黑名单中"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT COUNT(*) FROM fraud_redflags
        WHERE stock_code = ? AND (r071_triggered = 1 OR r072_triggered = 1 OR r073_triggered = 1 OR r074_triggered = 1)
    ''', (code,))
    n = cur.fetchone()[0]
    conn.close()
    return n > 0


# ⭐ 升级：调用 pe_check.is_good_price_for_strategy
# 长线用分桶 PE 阈值，底仓加 ROE/股息率，短线用统一 40
def is_good_pe_for_strategy(code, strategy):
    """PE 阈值检查

    长线：PE < 行业分桶阈值（银行 8 / 汽车 20 / 电子 35 等）
    底仓：PE < 行业分桶阈值 + 5y ROE > 12% + 股息率 > 3%
    短线：PE < 40 + PE > 0 + PE < 100
    """
    try:
        sys_path = config.DEPLOY_DIR
        import sys
        if sys_path not in sys.path:
            sys.path.append(sys_path)
        from pe_check import is_good_price_for_strategy as v5328_check
        is_good, _ = v5328_check(code, strategy)
        return is_good
    except Exception as e:
        log.warning("分桶 PE 检查失败 %s: %s（走兜底阈值）", code, e)
        # 兜底：原 v5.3.30 逻辑
        pe = get_stock_pe_ttm(code)
        if pe is None or pe <= 0:
            return False
        if strategy == 'LONG_TERM':
            return pe < 15
        elif strategy == 'CORE_HOLDING':
            return pe < 20
        else:
            return pe < 30


# ⭐ v5.3.30 P3-1: W19 18 步财报（健康度）
def get_w20_full(code):
    """v5.3.30 新增：W20 8 指标完整评估"""
    try:
        conn = sqlite3.connect(config.MASTER_DB)
        cur = conn.cursor()
        # 用 stock_pe_ttm + w20_8indicators + 兜底计算
        cur.execute('''
        SELECT op_margin, revenue_growth, fixed_asset_ratio,
               r075_pass, r076_pass, r077_pass
        FROM w20_8indicators WHERE stock_code = ? ORDER BY year DESC LIMIT 1
        ''', (code,))
        row = cur.fetchone()
        # 查 stock_pe_ttm 拿 ROE/股息率
        cur.execute('SELECT dividend_ratio FROM stock_pe_ttm WHERE stock_code = ?', (code,))
        div_row = cur.fetchone()
        conn.close()
        if not row:
            return None
        op_margin, rev_growth, fa_ratio, r075, r076, r077 = row
        div_ratio = div_row[0] if div_row else 0
        # 计算满足数（基于可用字段）
        pass_list = []
        if op_margin and op_margin > 8: pass_list.append('营业利润率>8%')
        if rev_growth and rev_growth > 5: pass_list.append('营收增长>5%')
        if fa_ratio and fa_ratio < 50: pass_list.append('固资占比<50%')
        if r075: pass_list.append('R075通过')
        if r076: pass_list.append('R076通过')
        if r077: pass_list.append('R077通过')
        if div_ratio and div_ratio > 2: pass_list.append(f'股息率{div_ratio:.1f}%')
        return {
            'pass_count': len(pass_list),
            'pass_list': pass_list,
            'op_margin': op_margin,
            'rev_growth': rev_growth,
            'div_ratio': div_ratio
        }
    except Exception as e:
        log.warning("W20 评估失败 %s: %s", code, e)
        return None


def get_w22_full(code):
    """v5.3.30 新增：W22 护城河 + ROA 完整评估"""
    try:
        conn = sqlite3.connect(config.MASTER_DB)
        cur = conn.cursor()
        cur.execute('SELECT moats, moat_count, score FROM stock_moats WHERE stock_code = ?', (code,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            'moats': row[0] or '无',
            'moat_count': row[1] or 0,
            'score': row[2] or 0
        }
    except Exception as e:
        log.warning("W22 评估失败 %s: %s", code, e)
        return None


def check_market_risk():
    """v5.3.30 新增：大盘系统性风险监控
    - 沪深 300 单日跌幅 > 2% → 全部个股降级
    - 沪深 300 PE > 22 → 警戒
    """
    try:
        import urllib.request
        url = 'http://qt.gtimg.cn/q=sh000300'
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read().decode('gbk')
        # 解析 v_sh000300="1~沪深300~000300~3938.89~3974.21~..."
        import re
        match = re.search(r'v_sh000300="1~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)', data)
        if not match:
            return None
        fields = match.groups()
        name = fields[0]
        current = float(fields[3])
        prev_close = float(fields[4])
        change_pct = float(fields[32]) if len(fields) > 32 else ((current / prev_close - 1) * 100)
        # 解析 chg_pct（一般在 field[32] 或计算）
        # 实际格式：v_sh000300="1~沪深300~000300~现价~昨收~开盘~振幅~涨跌~涨跌幅~..."
        # 让我从更多字段尝试
        all_fields = data.split('"')[1].split('~')
        if len(all_fields) > 32:
            change_pct = float(all_fields[32])
        else:
            change_pct = ((current - prev_close) / prev_close) * 100

        risk_level = 'low'
        warnings = []
        if change_pct < -2:
            risk_level = 'high'
            warnings.append(f'🚨 沪深 300 跌 {change_pct:.2f}% > -2%（系统性风险）→ 全部个股降级')
        elif change_pct < -1:
            risk_level = 'medium'
            warnings.append(f'⚠️ 沪深 300 跌 {change_pct:.2f}% > -1%（市场弱势）')

        return {
            'name': name,
            'current': current,
            'change_pct': change_pct,
            'risk_level': risk_level,
            'warnings': warnings
        }
    except Exception as e:
        log.warning("大盘风险监控失败: %s", e)
        return None


def check_industry_resonance(code, industry):
    """v5.3.30 新增：板块共振检查
    拉同行业 5 只股票当日表现，若多数跌 → 该股加仓信号降级
    """
    if not industry:
        return None
    try:
        conn = sqlite3.connect(config.MASTER_DB)
        cur = conn.cursor()
        cur.execute('''
        SELECT stock_code FROM stock_info WHERE industry = ? AND stock_code != ?
        ORDER BY rank_in_industry LIMIT 5
        ''', (industry, code))
        peers = [r[0] for r in cur.fetchall()]
        conn.close()

        if not peers:
            return None

        import urllib.request
        up_count = 0
        down_count = 0
        for peer in peers:
            try:
                code_clean = peer.lower().replace('.sh', '').replace('.sz', '')
                url = f'http://qt.gtimg.cn/q={code_clean}'
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = resp.read().decode('gbk')
                import re
                match = re.search(r'v_[^=]+="[^"]*~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)~([^~]+)', data)
                if match:
                    fields = match.groups()
                    current = float(fields[3])
                    prev_close = float(fields[4])
                    pct = (current - prev_close) / prev_close * 100
                    if pct > 0:
                        up_count += 1
                    else:
                        down_count += 1
            except Exception as e:
                log.warning("板块共振个股行情失败 %s: %s", peer, e)
                continue

        total = up_count + down_count
        if total == 0:
            return None

        up_pct = up_count / total * 100
        resonance = 'neutral'
        warning = None
        if up_pct >= 60:
            resonance = 'positive'
        elif up_pct <= 30:
            resonance = 'negative'
            warning = f'⚠️ 板块 {industry} 共振差（{up_count}/{total} 上涨 < 30%）→ 加仓信号降级'

        return {
            'industry': industry,
            'up_count': up_count,
            'down_count': down_count,
            'up_pct': up_pct,
            'resonance': resonance,
            'warning': warning
        }
    except Exception as e:
        log.warning("板块共振检查失败 %s: %s", industry, e)
        return None


def check_time_rules():
    """v5.3.30 新增：R057/R058 时间规则
    - R057 早盘 9:30-9:45 不追涨
    - R058 30 分钟内必须决策（9:30-10:00）
    """
    from datetime import time
    now_time = datetime.now().time()
    rules = []
    if time(9, 30) <= now_time <= time(9, 45):
        rules.append('⏸️ R057 早盘 9:30-9:45 不追涨，建议观望')
    if time(9, 30) <= now_time <= time(10, 0):
        rules.append('⏰ R058 30 分钟内必须决策')
    return rules


def get_w19_grade(code):
    """获取 W19 健康度等级"""
    try:
        sys_path = config.DEPLOY_DIR
        import sys
        if sys_path not in sys.path:
            sys.path.append(sys_path)
        from w19_18steps import get_w19_summary_v5326
        s = get_w19_summary_v5326(code)
        return s.get('health_grade', 'C')
    except Exception as e:
        log.warning("W19 健康度获取失败 %s: %s（按 C 级降级）", code, e)
        return 'C'


def check_c_rules(code, current_price, klines, vol_class):
    """C1-C6 清仓规则（纯函数版：rules.check_c_rules）"""
    return rules.check_c_rules(code, current_price, klines, vol_class)


def check_s_rules(code, current_price, klines, vol_class):
    """S1-S8 减仓规则（纯函数版：rules.check_s_rules）"""
    return rules.check_s_rules(code, current_price, klines, vol_class)


def check_b_rules(code, current_price, klines, cost):
    """B1-B8 加仓规则（纯函数版：rules.check_b_rules）"""
    return rules.check_b_rules(code, current_price, klines, cost)


def check_position_rules(position, current_price):
    """完整持仓规则检查（建仓/加仓/减仓/清仓）

    优先级（按 SKILL.md §5 决策流程）：
      1. C1-C6 清仓信号（最高优先级，含 C1-a 微破给 1 日缓冲，不被 R024 覆盖）
      2. S1-S8 减仓信号（优先于 R005A 软止盈）
      3. B1-B8 加仓信号（优先于 R007/R008 百分比加仓）
      4. R005/R024/R069/R078 兜底

    新增趋势可视化（MA5/MA10/MA20）+ 微淼完整评估 + 大盘风险 + 时间判断
    """
    code = position[0]
    name = position[1]
    shares = position[2]
    cost = position[4]  # entry_price（每股成本）
    cost_basis = position[3]  # 总成本
    strategy = position[5]

    pnl_pct = (current_price / cost - 1) * 100
    pnl_amount = (current_price - cost) * shares
    ma5 = get_ma5(code)
    ma10 = get_ma10(code)
    ma20 = get_ma20(code)  # v5.3.30 新增

    # 获取 K 线数据 + 波动率分档
    klines = get_recent_klines(code, n=10)
    vol_class = get_volatility_class(code)

    alerts = []
    trend_info = []  # v5.3.30 趋势信息

    # ========== v5.3.30 趋势可视化（MA5/MA10/MA20）==========
    if ma5 > 0:
        if current_price > ma5:
            trend_info.append(f'站上MA5({ma5:.2f})')
        else:
            trend_info.append(f'跌破MA5({ma5:.2f})')
    if ma10 > 0:
        if current_price > ma10:
            trend_info.append(f'站上MA10({ma10:.2f})')
        else:
            trend_info.append(f'跌破MA10({ma10:.2f})')
    if ma20 > 0:
        if current_price > ma20:
            trend_info.append(f'站上MA20({ma20:.2f})')
        else:
            trend_info.append(f'跌破MA20({ma20:.2f})')

    # ========== 第一优先级：C1-C6 清仓信号（最高）==========
    c_alerts = check_c_rules(code, current_price, klines, vol_class)
    c_triggered = len(c_alerts) > 0
    alerts.extend(c_alerts)

    # ========== 第二优先级：S1-S8 减仓信号（优先于 R005A 兜底）==========
    s_alerts = check_s_rules(code, current_price, klines, vol_class)
    s_triggered = len(s_alerts) > 0
    alerts.extend(s_alerts)

    # ========== 第三优先级：B1-B8 加仓信号（优先于 R007/R008 兜底）==========
    b_alerts = check_b_rules(code, current_price, klines, cost)
    b_triggered = len(b_alerts) > 0
    alerts.extend(b_alerts)

    # ========== 兜底：R005/R024/R069/R078（仅在 B/S/C 未触发时）==========
    if not c_triggered and not s_triggered:
        # R005 强平（绝对止损）
        if pnl_pct <= -5:
            alerts.append(f'🔴 R005 强平：当前 {current_price} ≤ 止损 {cost*0.95:.2f} 浮亏 {pnl_pct:.2f}%')
        # R024 红线：仅在 C1-a 未触发时立即清仓（修复冲突：C1-a 给 1 日缓冲，R024 不覆盖）
        elif pnl_pct < 0 and not c_triggered:
            alerts.append(f'🔴 R024 红线：浮盈变浮亏 {pnl_pct:.2f}%')

    if not c_triggered and not s_triggered:
        # R069: PE > 50 减仓
        pe = get_stock_pe_ttm(code)
        if pe and pe > 50:
            alerts.append(f'🟠 R069 减仓：PE TTM {pe:.1f} > 50（高估）')
        # R078: 股息率 < 0.7% 减仓
        div = get_dividend_ratio(code)
        if div is not None and div < 0.7:
            alerts.append(f'🟡 R078 减仓：股息率 {div:.2f}% < 国债/3')

    return pnl_pct, pnl_amount, alerts, trend_info


def check_buy_opportunity(code, name, strategy, market=None):
    """检查建仓机会：v5.3.30 三信号 + 多重过滤
    
    三信号：
    1. 启动期（5日累计涨 1-5%）
    2. 高量（量比 > 1.2）
    3. 底分型（昨收 < 昨开 + 今收 > 今开）
    
    v5.3.30 新增：
    - W16 4 红旗过滤
    - W4 PE 阈值
    - W19 健康度（A/B 级）
    """
    quote = get_realtime_quote(code)
    if not quote:
        return None
    
    cur = quote['current']
    prev = quote['prev_close']
    op = quote['open']
    pct = quote['change_pct']
    vol_ratio = quote['vol_ratio']
    ma5 = get_ma5(code)
    
    # 三信号检查
    signal_count = 0
    signals = []
    
    # 信号 1: 启动期
    if 1.0 <= pct <= 5.0:
        signal_count += 1
        signals.append('启动期')
    
    # 信号 2: 高量
    if vol_ratio > 1.2:
        signal_count += 1
        signals.append('高量')
    
    # 信号 3: 底分型
    if prev < op and cur > op:
        signal_count += 1
        signals.append('底分型')
    
    # 信号 4: 站上 MA5
    if cur > ma5 and ma5 > 0:
        signal_count += 1
        signals.append('站上MA5')
    
    # 信号 5: 阳线
    if cur > op:
        signal_count += 1
        signals.append('阳线')
    
    triggered = signal_count >= 3
    
    # v5.3.30 过滤
    blacklist = []
    if is_redflag_blacklisted(code):
        blacklist.append('W16 4 红旗黑名单')
        triggered = False
    if not is_good_pe_for_strategy(code, strategy):
        blacklist.append(f'PE 不满足 {strategy} 阈值')
        triggered = False
    w19_grade = get_w19_grade(code)
    if w19_grade == 'C' and strategy in ['LONG_TERM', 'CORE_HOLDING']:
        blacklist.append(f'W19 健康度 C 级（差）')
        triggered = False
    
    # V5.3.30 建仓时机判定 R090-R094（全 AND：5 条件必须同时满足，v5.3.30 工业级改造对齐 SKILL.md）
    market_ok = not (market and market.get('risk_level') == 'high')
    entry = check_entry_conditions_v5_3_30(code, cur, market_ok=market_ok)
    if not entry['can_buy']:
        triggered = False
        d = entry['details']
        if d['R090_trend']['block']:
            blacklist.append(f'R090 趋势阶段={d["R090_trend"]["stage"]} 禁建仓')
        elif not d['R090_trend']['pass']:
            blacklist.append(f'R090 趋势阶段={d["R090_trend"]["stage"]} 未达建仓区')
        if d['R091_position']['block']:
            blacklist.append(f'R091 位置 {d["R091_position"]["position_pct"]}% 禁建仓')
        elif not d['R091_position']['pass']:
            blacklist.append(f'R091 位置 {d["R091_position"]["position_pct"]}% 不在安全区 30-70')
        if not d['R092_bottom']['pass']:
            blacklist.append(f'R092 无底部反转形态（{d["R092_bottom"]["pattern"]}）')
        if d['R093_macd']['block']:
            blacklist.append('R093 MACD 顶背离 禁建仓')
        if not d['R094_market']['pass']:
            blacklist.append('R094 大盘系统性风险 禁建仓')
    
    return {
        'code': code,
        'name': quote['name'],
        'current': cur,
        'prev_close': prev,
        'pct': pct,
        'vol_ratio': vol_ratio,
        'ma5': ma5,
        'signal_count': signal_count,
        'signals': signals,
        'w19_grade': w19_grade,
        'triggered': triggered,
        'blacklist': blacklist,
        'v5_3_30_entry': entry,  # 新增建仓时机详情
    }


def main():
    now = datetime.now()

    log.info('=' * 70)
    log.info(f'🔍 高量战法盘中完整监控（{now.strftime("%Y-%m-%d %H:%M:%S")}）')
    log.info('=' * 70)
    log.info('  完整检查：建仓 + 加仓 + 减仓 + 清仓')
    log.info('  完整评估：W16 红旗 + W4 分桶 PE + 底仓 ROE/股息率 + W19 健康度 + W20/W22 微淼 + R069/R078')
    log.info('  趋势可视化：MA5/MA10/MA20 站上/跌破')
    log.info('  大盘风险：沪深 300 单日跌幅监控 + 板块共振 + R057/R058 时间规则')
    log.info('  ⭐ V5.3.30 建仓时机：R090 趋势阶段 + R091 位置百分位 + R092 底部形态 + R093 MACD 背离 + R094 共振')
    log.info('')

    # 0) 时间规则 R057/R058
    time_rules = check_time_rules()
    if time_rules:
        log.info('⏰ 时间规则:')
        for r in time_rules:
            log.info(f'  {r}')
        log.info('')

    # 0.5) 大盘系统性风险
    market = check_market_risk()
    if market and market['warnings']:
        log.info('🌐 大盘风险监控:')
        for w in market['warnings']:
            log.info(f'  {w}')
        log.info('')

    # 1) 加载持仓
    conn = sqlite3.connect(PORTFOLIO_DB)
    cur = conn.execute('SELECT code, name, shares, cost_basis, entry_price, strategy_type FROM positions WHERE active=1')
    positions = cur.fetchall()
    conn.close()

    # 2) 检查持仓规则
    log.info(f'💼 当前持仓: {len(positions)} 只')
    if positions:
        for pos in positions:
            code, name, shares, cost_basis, entry_price, strategy = pos
            quote = get_realtime_quote(code)
            if not quote:
                log.info(f'  ⚠️ {code} {name}: 实时数据拉取失败')
                continue
            cur_price = quote['current']
            pnl_pct, pnl_amount, alerts, trend_info = check_position_rules(pos, cur_price)
            log.info(f'  {code} {name}: 价 {cur_price} 浮盈 {pnl_amount:+,.0f}元 ({pnl_pct:+.2f}%) 量比 {quote["vol_ratio"]:.2f}x')
            # 趋势可视化
            if trend_info:
                log.info(f'    📈 趋势: {" | ".join(trend_info)}')

            # 微淼完整评估
            w19_grade = get_w19_grade(code)
            w20_full = get_w20_full(code)
            w22_full = get_w22_full(code)
            micro_info = [f'W19={w19_grade}']
            if w20_full:
                micro_info.append(f'W20={w20_full["pass_count"]}/8')
            if w22_full:
                micro_info.append(f'W22={w22_full["moat_count"]}护城河')
            log.info(f'    💎 微淼: {" | ".join(micro_info)}')

            if alerts:
                for a in alerts:
                    log.info(f'    {a}')
            else:
                log.info(f'    ✅ 无触发')
    else:
        log.info('  空仓（可以建仓）')
    log.info('')

    # 3) 检查预备池建仓机会
    conn = sqlite3.connect(CANDIDATES_DB)
    cur = conn.execute('''
        SELECT strategy, rank, code, name FROM daily_prep_pool
        WHERE prep_date = (SELECT MAX(prep_date) FROM daily_prep_pool)
        ORDER BY strategy, rank
    ''')
    prep_pool = cur.fetchall()
    conn.close()

    if not positions:
        log.info('📋 预备池建仓检查（无持仓，可建仓）:')
    else:
        log.info('📋 预备池建仓检查（仅供参考，已有持仓）:')

    new_triggers = 0
    for strategy, rank, code, name in prep_pool:
        result = check_buy_opportunity(code, name, strategy, market)
        if not result:
            log.info(f'  ⏸️ {strategy} #{rank} {code} {name}: 实时数据拉取失败')
            continue

        if result['triggered']:
            log.info(f'  🆕 NEW {strategy} #{rank} {code} {result["name"]} 三信号 {result["signal_count"]}/5 触发，现价 {result["current"]} 量比 {result["vol_ratio"]:.2f}x (W19 {result["w19_grade"]})')
            new_triggers += 1
        else:
            reason = ''
            if result['blacklist']:
                reason = f' [过滤: {", ".join(result["blacklist"])}]'
            elif result['signal_count'] < 3:
                reason = f' 三信号 {result["signal_count"]}/5 未触发'
            log.info(f'  ⏸️ {strategy} #{rank} {code} {result["name"]} 价 {result["current"]} {result["pct"]:+.2f}%{reason}')
    log.info('')

    # 4) 监控总结
    log.info('=' * 70)
    log.info(f'📊 监控总结: {new_triggers} 条建仓信号')
    if new_triggers == 0:
        log.info('  当前无新信号，建议空仓等待')
    else:
        log.info(f'  🆕 NEW 信号需人工确认（建仓前必须看分桶 PE + W19 健康度 + W20/W22 微淼）')
    log.info('=' * 70)


if __name__ == '__main__':
    main()
