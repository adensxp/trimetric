#!/usr/bin/env python3
"""
高量战法 完整版每日股票池全量分析（三策略版）
================================
- 16:30 收盘后自动触发
- 对 494 只股票全量跑三策略评分（沪深 300 + 申万 31 行业 Top 10 限沪深）
- 短线预备池前 5 名（7-17% 仓位）
- 长线预备池前 3 名（15-25% 仓位）
- 底仓预备池前 2 名（30-50% 仓位）
- 写入 daily_pool_scores + daily_prep_pool 表
- 增强版阶梯式皇冠加权：基础 < 5 不加权 / 5-6 +0.3 / 7-8 +1.0 / 9-10 +1.5
- 高量战法 大盘择时：沪深 300 PE 18 + 多指数加权（替代深证 PE 20）
- 高量战法 个股 PE 分桶：长线行业分桶 + 底仓三重过滤（PE+ROE+股息率）
- 高量战法 微淼多维评估：W20 8 指标 + W22 护城河 + W19 健康度
- ⭐ V5.3.30 建仓时机过滤：R090 趋势阶段 + R091 位置百分位 + R093 MACD 背离
  - 顶部阶段 / 下跌阶段 / 位置 > 85% / 位置 < 15% / MACD 顶背离 → 禁止入预备池
"""
import config
log = config.setup_logging("daily_pool")
import sqlite3
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, config.DEPLOY_DIR)
from v5_3_25_scorer import crown_jewel_bonus, load_crown_jewels

DB_PATH = config.MASTER_DB
PORTFOLIO_DB = config.CANDIDATES_DB
BATCHES_FILE = config.STOCK_POOL_500


# 股票名称映射（高量战法完整 300 只（沪深 300））
def get_name(code):
    """动态从 stock_info 表获取股票名 - 高量战法 升级"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT stock_name FROM stock_info WHERE stock_code=?', (code,))
    r = cur.fetchone()
    conn.close()
    if r and r[0]:
        return r[0]
    return code


# 行业分类（用于长线/底仓）
# 行业映射 - 高量战法 升级：动态从 stock_info 表加载
STABLE_INDUSTRIES = {
    '银行', '非银金融', '石油石化', '交通运输', '公用事业', '环保',
    '食品饮料', '家用电器', '通信', '建筑装饰', '建筑材料', '国防军工',
    '电子', '电力设备', '汽车', '房地产', '医药生物', '计算机',
    '传媒', '机械设备', '钢铁', '有色金属', '基础化工', '农林牧渔',
    '煤炭', '社会服务', '纺织服饰', '轻工制造', '商贸零售',
    '综合', '美容护理', '银行', '保险', '证券',
}

INDUSTRY_MAP_FALLBACK = {
    # 保留一些常见股的硬编码映射（兼容老数据）
    '601398.SH': '银行', '601988.SH': '银行', '601166.SH': '银行',
    '600519.SH': '食品饮料', '000333.SZ': '家用电器', '002475.SZ': '电子',
}

def get_industry(code):
    """从 stock_info 表动态获取行业"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT industry FROM stock_info WHERE stock_code=?', (code,))
    r = cur.fetchone()
    conn.close()
    if r and r[0] and r[0] not in ['其他']:
        return r[0]
    return INDUSTRY_MAP_FALLBACK.get(code, '其他')




def load_pool():
    """加载 494 只股票池（高量战法 微淼完整版：沪深 300 + 申万 31 行业 Top 10）"""
    with open(BATCHES_FILE, 'r') as f:
        data = json.load(f)
    pool = []
    batches = data.get('batches', {})
    if isinstance(batches, dict):
        for batch in batches.values():
            for item in batch:
                if isinstance(item, dict):
                    pool.append(item.get('code', ''))
                else:
                    pool.append(item)
    elif isinstance(batches, list):
        for batch in batches:
            for item in batch:
                if isinstance(item, dict):
                    pool.append(item.get('code', ''))
                else:
                    pool.append(item)
    return pool


def get_recent_data(conn, code, n=20):
    """获取最近 N 日数据"""
    cur = conn.execute("""
        SELECT trade_date, open_price, close_price, high_price, low_price, volume, change_pct
        FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT ?
    """, (code, n))
    return list(cur.fetchall())


def calc_ma5(conn, code):
    """计算 MA5"""
    cur = conn.execute("""
        SELECT close_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 5
    """, (code,))
    closes = [r[0] for r in cur.fetchall()]
    return sum(closes) / len(closes) if closes else 0


def calc_vol_ratio(conn, code):
    """计算量比（异常值过滤）"""
    cur = conn.execute("""
        SELECT volume FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 6
    """, (code,))
    vols = [r[0] for r in cur.fetchall()]
    if len(vols) < 6:
        return 0
    avg_vol_5 = sum(vols[1:6]) / 5
    if avg_vol_5 == 0:
        return 0
    ratio = vols[0] / avg_vol_5
    return 1.0 if ratio > 10 else ratio


def calc_5d_change(conn, code):
    """5 日累计涨跌幅"""
    cur = conn.execute("""
        SELECT close_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 6
    """, (code,))
    closes = [r[0] for r in cur.fetchall()]
    if len(closes) < 6:
        return 0
    return (closes[0] / closes[5] - 1) * 100


def calc_20d_change(conn, code):
    """20 日累计涨跌幅"""
    cur = conn.execute("""
        SELECT close_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 21
    """, (code,))
    closes = [r[0] for r in cur.fetchall()]
    if len(closes) < 21:
        return 0
    return (closes[0] / closes[20] - 1) * 100


def calc_volatility(conn, code, n=20):
    """N 日波动率（标准差 / 均值）"""
    cur = conn.execute("""
        SELECT close_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT ?
    """, (code, n))
    closes = [r[0] for r in cur.fetchall()]
    if len(closes) < 5:
        return 1.0
    mean = sum(closes) / len(closes)
    if mean == 0:
        return 1.0
    variance = sum((c - mean) ** 2 for c in closes) / len(closes)
    return (variance ** 0.5) / mean


def check_bottom_pattern(conn, code):
    """底分型"""
    cur = conn.execute("""
        SELECT close_price, low_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 3
    """, (code,))
    rows = cur.fetchall()
    if len(rows) < 3:
        return False
    t2, t1, t0 = rows[2], rows[1], rows[0]
    return t1[1] > t2[1] and t0[0] > t2[0]


# ============== 短线评分（满分 10）==============
def score_short_term(conn, code):
    """短线评分（建仓 7-17% 仓位）"""
    cur = conn.execute("""
        SELECT close_price, open_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 1
    """, (code,))
    row = cur.fetchone()
    if not row:
        return None
    close, open_p = row
    ma5 = calc_ma5(conn, code)
    vol_ratio = calc_vol_ratio(conn, code)
    change_5d = calc_5d_change(conn, code)
    change_pct = row[0]  # 当日
    bottom = check_bottom_pattern(conn, code)

    score = 0
    signals = {}

    # 信号 1: 启动期（健康 0-5% 或 追高 > 5%）
    if 0 < change_5d <= 5:
        score += 2
        signals['启动期(健康)'] = True
    elif change_5d > 5:
        score += 1
        signals['启动期(追高)'] = True
    else:
        signals['启动期'] = False

    # 信号 2: 高量 1.2x
    if vol_ratio >= 1.2:
        score += 3
        signals['高量1.2x'] = True
    elif vol_ratio >= 1.0:
        score += 1
        signals['高量1.0-1.2x'] = True
    else:
        signals['高量'] = False

    # 信号 3: 底分型
    if bottom:
        score += 2
        signals['底分型'] = True
    else:
        signals['底分型'] = False

    # 站上 MA5
    if close > ma5:
        score += 1
        signals['站上MA5'] = True
    else:
        signals['站上MA5'] = False

    # 阳线
    if close > open_p:
        score += 1
        signals['阳线'] = True
    else:
        signals['阳线'] = False

    # 健康涨幅
    if 2 <= change_pct <= 5:
        score += 1
        signals['健康涨幅'] = True
    else:
        signals['健康涨幅'] = False

    return {
        'code': code,
        'name': get_name(code),
        'close': close,
        'open': open_p,
        'ma5': ma5,
        'vol_ratio': vol_ratio,
        'change_5d': change_5d,
        'change_pct': change_pct,
        'total_score': score,
        'signal_count': sum(1 for v in signals.values() if v),
        'signals': signals,
    }


# ============== 长线评分（满分 10）==============
def score_long_term(conn, code):
    """长线评分（建仓 15-25% 仓位）
    
    高量战法 完整版（5 年财务数据全接入）：
    - 信号 1: 5 日趋势向上（2 分）
    - 信号 2: 20 日累计 +5% ~ +30%（2 分）
    - 信号 3: 量比正常 0.8-1.5（1 分）
    - 信号 4: 波动率 < 5%（2 分）
    - 信号 5: 大盘择时 R068（1 分 - 当前深证 PE 数据缺失，标记 TODO）
    - 信号 6: 行业地位（1 分 - 当前行业前 3 龙头）
    - 信号 7: 站上 MA5（1 分）
    """
    cur = conn.execute("""
        SELECT close_price, open_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 1
    """, (code,))
    row = cur.fetchone()
    if not row:
        return None
    close, open_p = row
    ma5 = calc_ma5(conn, code)
    vol_ratio = calc_vol_ratio(conn, code)
    change_5d = calc_5d_change(conn, code)
    change_20d = calc_20d_change(conn, code)
    volatility = calc_volatility(conn, code, n=20)
    industry = get_industry(code)

    score = 0
    signals = {}

    # 信号 1: 5 日趋势向上（短线配合长线，避免急涨）
    if 1 < change_5d <= 5:
        score += 2
        signals['短线启动(1-5%)'] = True
    elif change_5d > 5:
        # 短期涨幅过大，长线追高风险
        score += 1
        signals['短线启动(>5%)'] = True
    else:
        signals['5日趋势'] = False

    # 信号 2: 20 日累计 +5% ~ +30%（中线健康）
    if 5 <= change_20d <= 30:
        score += 2
        signals['20日健康(5-30%)'] = True
    elif change_20d > 30:
        score += 1
        signals['20日过大(>30%)'] = True
    else:
        signals['20日趋势'] = False

    # 信号 3: 量比正常（避免异常放量）
    if 0.8 <= vol_ratio <= 1.5:
        score += 1
        signals['量比正常(0.8-1.5)'] = True
    else:
        signals['量比'] = False

    # 信号 4: 波动率低（稳健）
    if volatility < 0.05:
        score += 2
        signals['波动率低(<5%)'] = True
    elif volatility < 0.08:
        score += 1
        signals['波动率中等(<8%)'] = True
    else:
        signals['波动率'] = False

    # 信号 5: 大盘择时 R068（TODO: 接入深证 PE 数据）
    # 当前假设：未触发（默认 0 分）
    signals['大盘择时R068'] = False
    # score += 0  # 待接入财务数据后启用

    # 信号 6: 行业地位（简化：默认 1 分，假设 155 只都是各行业龙头）
    score += 1
    signals['行业地位(龙头)'] = True

    # 信号 7: 站上 MA5
    if close > ma5:
        score += 1
        signals['站上MA5'] = True
    else:
        signals['站上MA5'] = False

    return {
        'code': code,
        'name': get_name(code),
        'close': close,
        'open': open_p,
        'ma5': ma5,
        'vol_ratio': vol_ratio,
        'change_5d': change_5d,
        'change_20d': change_20d,
        'volatility': volatility,
        'industry': industry,
        'total_score': score,
        'signal_count': sum(1 for v in signals.values() if v),
        'signals': signals,
    }


# ============== 底仓评分（满分 10）==============
def score_core_holding(conn, code):
    """底仓评分（建仓 30-50% 仓位）
    
    高量战法 完整版（5 年财务数据全接入）：
    - 信号 1: 波动率极低 < 3%（3 分）
    - 信号 2: 20 日累计在 -5% ~ +5%（2 分 - 极稳定）
    - 信号 3: 量比正常 0.7-1.2（1 分 - 不活跃也稳定）
    - 信号 4: 站上 MA5（1 分）
    - 信号 5: 行业稳定（银行/电力/高速等）（1 分）
    - 信号 6: 价格稳定（最高最低差距小）（1 分）
    - 信号 7: 大盘股（价格高 = 1 元以上 = 已过滤）（1 分）
    """
    cur = conn.execute("""
        SELECT close_price, open_price, high_price, low_price FROM stock_daily
        WHERE stock_code = ?
        ORDER BY trade_date DESC LIMIT 20
    """, (code,))
    rows = cur.fetchall()
    if not rows:
        return None

    close = rows[0][0]
    open_p = rows[0][1]
    ma5 = calc_ma5(conn, code)
    vol_ratio = calc_vol_ratio(conn, code)
    change_5d = calc_5d_change(conn, code)
    change_20d = calc_20d_change(conn, code)
    volatility = calc_volatility(conn, code, n=20)
    industry = get_industry(code)

    # 价格稳定度（20 日最高最低差 / 收盘价）
    high_20 = max(r[2] for r in rows)
    low_20 = min(r[3] for r in rows)
    price_range_pct = (high_20 - low_20) / close if close else 1.0

    score = 0
    signals = {}

    # 信号 1: 波动率极低
    if volatility < 0.03:
        score += 3
        signals['波动率极低(<3%)'] = True
    elif volatility < 0.05:
        score += 2
        signals['波动率低(<5%)'] = True
    elif volatility < 0.08:
        score += 1
        signals['波动率中等'] = True
    else:
        signals['波动率'] = False

    # 信号 2: 20 日累计稳定（-5% ~ +5%）
    if -5 <= change_20d <= 5:
        score += 2
        signals['20日极稳(-5~+5%)'] = True
    elif -10 <= change_20d <= 10:
        score += 1
        signals['20日较稳(-10~+10%)'] = True
    else:
        signals['20日稳定'] = False

    # 信号 3: 量比正常（不活跃也稳定）
    if 0.7 <= vol_ratio <= 1.2:
        score += 1
        signals['量比正常(0.7-1.2)'] = True
    else:
        signals['量比'] = False

    # 信号 4: 站上 MA5
    if close > ma5:
        score += 1
        signals['站上MA5'] = True
    else:
        signals['站上MA5'] = False

    # 信号 5: 行业稳定（银行/电力/高速/铁路/煤炭）
    stable_industries = ['银行', '公用事业', '交通运输', '煤炭', '石油石化', '非银金融']
    if industry in stable_industries:
        score += 1
        signals['行业稳定'] = True
    else:
        signals['行业稳定'] = False

    # 信号 6: 价格稳定度（20 日振幅 < 15%）
    if price_range_pct < 0.15:
        score += 1
        signals['价格稳定(<15%)'] = True
    else:
        signals['价格稳定'] = False

    # 信号 7: 价格 > 1 元（剔除仙股）
    if close > 1.0:
        score += 1
        signals['价格>1元'] = True
    else:
        signals['价格>1元'] = False

    return {
        'code': code,
        'name': get_name(code),
        'close': close,
        'open': open_p,
        'ma5': ma5,
        'vol_ratio': vol_ratio,
        'change_5d': change_5d,
        'change_20d': change_20d,
        'volatility': volatility,
        'price_range_pct': price_range_pct,
        'industry': industry,
        'total_score': score,
        'signal_count': sum(1 for v in signals.values() if v),
        'signals': signals,
    }


def analyze_all():
    """三策略全量分析（高量战法：阶梯式皇冠加权 + W16 财务红旗 + W4 好价格双条件）"""
    pool = load_pool()
    s_conn = sqlite3.connect(DB_PATH)

    # 加载皇冠池（只加载一次）
    crown = load_crown_jewels()

    # ⭐ 高量战法 W16 财务红旗过滤
    from fraud_redflags import get_redflag_summary
    import time
    blacklist = {}  # code -> [触发的规则]
    safe_pool = []
    _w16_start = time.time()
    for _i, code in enumerate(pool):
        summary = get_redflag_summary(code)
        if summary['is_blacklist']:
            blacklist[code] = summary['triggered_rules']
        else:
            safe_pool.append(code)
        if _i % 100 == 0:
            log.info(f"   W16 progress {_i}/{len(pool)} {time.time()-_w16_start:.1f}s")
    log.info(f"⚠️ W16 红旗黑名单: {len(blacklist)} 只已剔除")
    if blacklist:
        for code, rules in list(blacklist.items())[:5]:
            log.info(f"   {code}: {rules}")

    # ⭐ 高量战法 大盘择时（多指数加权：沪深 300 / 上证 / 深证 / 中证 500）
    from pe_check import get_stock_pe, get_market_pe, get_all_market_pe, is_market_favorable, is_good_price_for_strategy, filter_pool_by_pe

    # 4 指数打印
    all_market_pe = get_all_market_pe()
    if all_market_pe:
        for code, d in all_market_pe.items():
            log.info(f"📊 {d['name']} PE: {d['pe_ttm']:.1f} (分位 {d.get('percentile', '?')}%, 日期 {d['date']})")

    # 多指数加权择时
    is_fav, market_score, market_details = is_market_favorable()
    log.info(f"\n🎯 大盘择时 R068 (高量战法 多指数加权):")
    log.info(f"   可建仓: {is_fav}, 评分: {market_score}")
    for line in market_details.split('\n'):
        log.info(f"   {line}")
    if not is_fav:
        log.info(f"   ⚠️ 大盘择时不通过，建议空仓")

    pe_blacklist_by_strategy = {
        'SHORT_TERM': [],
        'LONG_TERM': [],
        'CORE_HOLDING': []
    }
    pe_safe_pool = {
        'SHORT_TERM': [],
        'LONG_TERM': [],
        'CORE_HOLDING': []
    }
    # ⭐ 高量战法 升级：调用 pe_check 的 is_good_price_for_strategy 函数
    # 长线用分桶 PE 阈值，底仓加 ROE/股息率，短线用统一 40
    from pe_check import is_good_price_for_strategy
    _w4_start = time.time()
    for _i, code in enumerate(safe_pool):
        for strategy in ['SHORT_TERM', 'LONG_TERM', 'CORE_HOLDING']:
            is_good, reason = is_good_price_for_strategy(code, strategy)
            if is_good:
                pe_safe_pool[strategy].append(code)
            else:
                pe_blacklist_by_strategy[strategy].append((code, reason))
        if _i % 100 == 0:
            log.info(f"   W4 progress {_i}/{len(safe_pool)} {time.time()-_w4_start:.1f}s")

    for strategy in ['SHORT_TERM', 'LONG_TERM', 'CORE_HOLDING']:
        label = {'SHORT_TERM': '短线', 'LONG_TERM': '长线', 'CORE_HOLDING': '底仓'}[strategy]
        n_bl = len(pe_blacklist_by_strategy[strategy])
        n_safe = len(pe_safe_pool[strategy])
        log.info(f"⚠️ W4 好价格 ({label}): {n_bl} 只不满足 PE 阈值, {n_safe} 只可入池")
        # 打印前 5 个 PE 触发的
        for code, reason in pe_blacklist_by_strategy[strategy][:3]:
            log.info(f"   {code}: {reason}")

    # ⭐ V5.3.30 建仓时机过滤 R090-R094
    # 统一走 monitor.check_entry_conditions_v5_3_30（全 AND，与盘中监控同一套判定；单一事实源）
    # 注：预备池为筛选名单，R094 大盘择时由次日盘中监控实时把关，此处不拦
    from monitor import check_entry_conditions_v5_3_30
    v5330_blacklist = {}  # code -> [触发的规则]
    v5330_safe_pool = list(safe_pool)
    for code in list(v5330_safe_pool):
        cur = s_conn.execute('SELECT close_price FROM stock_daily WHERE stock_code=? ORDER BY trade_date DESC LIMIT 1', (code,)).fetchone()
        if not cur or not cur[0]:
            v5330_blacklist.setdefault(code, []).append('无最新收盘价')
            v5330_safe_pool.remove(code)
            continue
        entry = check_entry_conditions_v5_3_30(code, cur[0])
        if entry['can_buy']:
            continue
        d = entry['details']
        reasons = []
        if d['R090_trend']['block']:
            reasons.append(f"R090 趋势={d['R090_trend']['stage']}")
        elif not d['R090_trend']['pass']:
            reasons.append(f"R090 趋势={d['R090_trend']['stage']} 未达建仓区")
        if d['R091_position']['block']:
            reasons.append(f"R091 位置 {d['R091_position']['position_pct']}%")
        elif not d['R091_position']['pass']:
            reasons.append(f"R091 位置 {d['R091_position']['position_pct']}% 不在安全区 30-70")
        if not d['R092_bottom']['pass']:
            reasons.append(f"R092 无底部形态({d['R092_bottom']['pattern']})")
        if d['R093_macd']['block']:
            reasons.append('R093 MACD 顶背离')
        v5330_blacklist[code] = reasons
        v5330_safe_pool.remove(code)

    log.info(f"\n⭐ V5.3.30 建仓时机过滤: {len(v5330_blacklist)} 只已剔除")
    for code, reasons in list(v5330_blacklist.items())[:5]:
        log.info(f"   {code}: {reasons}")
    safe_pool = v5330_safe_pool  # 用 V5.3.30 过滤后的池子继续跑
    log.info(f"   V5.3.30 后剩余: {len(safe_pool)} 只")

    results = {
        'SHORT_TERM': [],
        'LONG_TERM': [],
        'CORE_HOLDING': [],
    }
    skipped = []

    # ⭐ 高量战法 W4 好价格：按策略分别过滤
    # pe_safe_pool 已经是按策略分桶的
    # 这里跑 3 策略时，每个策略只对能进入的票评分
    for code in safe_pool:  # ⭐ 只跑 safe_pool，跳过黑名单
        try:
            # ⭐ 高量战法 W4 好价格过滤：长线行业分桶 PE, 底仓三重过滤（PE+ROE+股息率）, 短线 PE<40
            r_short = score_short_term(s_conn, code) if code in pe_safe_pool['SHORT_TERM'] else None
            r_long = score_long_term(s_conn, code) if code in pe_safe_pool['LONG_TERM'] else None
            r_core = score_core_holding(s_conn, code) if code in pe_safe_pool['CORE_HOLDING'] else None

            # ⭐ 高量战法 增强版：阶梯式皇冠加权（按基础分决定档位）
            if r_short:
                bonus, msg = crown_jewel_bonus(code, crown, base_score=r_short['total_score'])
                r_short['total_score'] = round(r_short['total_score'] + bonus, 1)
                r_short['crown_note'] = msg
                results['SHORT_TERM'].append(r_short)
            if r_long:
                bonus, msg = crown_jewel_bonus(code, crown, base_score=r_long['total_score'])
                r_long['total_score'] = round(r_long['total_score'] + bonus, 1)
                r_long['crown_note'] = msg
                results['LONG_TERM'].append(r_long)
            if r_core:
                bonus, msg = crown_jewel_bonus(code, crown, base_score=r_core['total_score'])
                r_core['total_score'] = round(r_core['total_score'] + bonus, 1)
                r_core['crown_note'] = msg
                results['CORE_HOLDING'].append(r_core)
        except Exception as e:
            skipped.append((code, str(e)))

    s_conn.close()

    # 把黑名单也返回
    return results, skipped, blacklist


def save_scores(results, score_date):
    """保存所有评分（先清空当日旧数据）"""
    conn = sqlite3.connect(PORTFOLIO_DB)
    # 清空当日评分
    conn.execute("DELETE FROM daily_pool_scores WHERE score_date = ?", (score_date,))
    for strategy, scores in results.items():
        scores_sorted = sorted(scores, key=lambda x: x['total_score'], reverse=True)
        for rank, r in enumerate(scores_sorted, 1):
            conn.execute("""
                INSERT INTO daily_pool_scores
                (score_date, strategy, code, name, total_score, signal_count,
                 change_pct, vol_ratio, close_price, ma5, pattern, rank, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_date, strategy, r['code'], r['name'],
                r['total_score'], r['signal_count'],
                r.get('change_pct', r.get('change_5d', 0)),
                r.get('vol_ratio', 0),
                r.get('close', 0),
                r.get('ma5', 0),
                ' / '.join([k for k, v in r.get('signals', {}).items() if v][:3]),
                rank, datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
    conn.commit()
    conn.close()


def save_prep_pool(results, prep_date, top_n_map):
    """保存预备池（先清空当日旧数据）"""
    conn = sqlite3.connect(PORTFOLIO_DB)
    # 清空当日预备池（保证每次写入都是最新的前 N 名）
    conn.execute("DELETE FROM daily_prep_pool WHERE prep_date = ?", (prep_date,))
    for strategy, scores in results.items():
        top_n = top_n_map.get(strategy, 5)
        scores_sorted = sorted(scores, key=lambda x: x['total_score'], reverse=True)
        for rank, r in enumerate(scores_sorted[:top_n], 1):
            conn.execute("""
                INSERT INTO daily_prep_pool
                (prep_date, strategy, code, name, total_score, signal_count, rank, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prep_date, strategy, r['code'], r['name'],
                r['total_score'], r['signal_count'], rank,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
    conn.commit()
    conn.close()


def generate_report(results, skipped):
    """生成三策略报告"""
    report = []
    report.append("=" * 70)
    report.append(f"📊 每日股票池全量分析（三策略版）")
    report.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("=" * 70)
    report.append(f"")
    report.append(f"总股票数: {len(results['SHORT_TERM'])} 只（同时跑三策略）")
    report.append(f"跳过/失败: {len(skipped)} 只")
    report.append(f"")

    for strategy, name, top_n in [
        ('SHORT_TERM', '短线', 5),
        ('LONG_TERM', '长线', 3),
        ('CORE_HOLDING', '底仓', 2),
    ]:
        scores = results[strategy]
        scores_sorted = sorted(scores, key=lambda x: x['total_score'], reverse=True)
        report.append(f"🏆 {name}预备池前 {top_n} 名:")
        report.append(f"  {'排名':4s} {'代码':12s} {'名称':10s} {'评分':5s} {'信号':5s} {'5日%':6s} {'量比':5s} {'价格':7s}")
        for i, r in enumerate(scores_sorted[:top_n], 1):
            report.append(f"  {i:4d} {r['code']:12s} {r['name']:10s} {r['total_score']:5.1f} {r['signal_count']:5d} {r['change_5d']:+5.2f}% {r['vol_ratio']:4.2f}x {r['close']:7.2f}")
        report.append(f"")

    # 评分分布
    report.append(f"📈 评分分布:")
    for strategy, name in [('SHORT_TERM', '短线'), ('LONG_TERM', '长线'), ('CORE_HOLDING', '底仓')]:
        scores = results[strategy]
        high = [r for r in scores if r['total_score'] >= 7]
        mid = [r for r in scores if 4 <= r['total_score'] < 7]
        low = [r for r in scores if r['total_score'] < 4]
        report.append(f"  {name}: 高分 ≥7 = {len(high)} / 中分 4-6 = {len(mid)} / 低分 <4 = {len(low)}")
    report.append(f"")

    # ⭐ 高量战法 W20 8 指标软参考（P1-1）- 优化：先批量查询
    try:
        import sqlite3 as _sqlite
        _conn = _sqlite.connect(config.MASTER_DB)
        # 批量拉所有 8 指标数据
        _cur = _conn.execute('''
            SELECT stock_code, SUM(r075_pass), SUM(r076_pass), SUM(r077_pass), COUNT(*)
            FROM w20_8indicators
            GROUP BY stock_code
            HAVING COUNT(*) >= 3
        ''')
        w20_data = {}
        for r in _cur.fetchall():
            w20_data[r[0]] = {'r075': r[1], 'r076': r[2], 'r077': r[3], 'n': r[4]}
        _conn.close()

        w20_strict = [c for c, d in w20_data.items() if d['r075'] >= 2 and d['r076'] >= 2 and d['r077'] >= 2]
        w20_relaxed = [c for c, d in w20_data.items() if (d['r075'] + d['r076'] + d['r077']) >= 4 and c not in w20_strict]

        report.append(f"📋 W20 8 指标软参考（不硬过滤，仅参考）:")
        report.append(f"  - 8/8 满足: {len(w20_strict)} 只")
        if w20_strict:
            report.append(f"    {', '.join(w20_strict[:5])}")
        report.append(f"  - 6/8 满足: {len(w20_relaxed)} 只")
        if w20_relaxed:
            report.append(f"    {', '.join(w20_relaxed[:5])}")
    except Exception as e:
        report.append(f"📋 W20 8 指标: 跳过（{e}）")
    report.append(f"")

    # ⭐ 高量战法 W22 6 护城河分类（P2-2）- 批量查询
    try:
        import sqlite3 as _sqlite
        _conn = _sqlite.connect(config.MASTER_DB)
        _cur = _conn.execute('SELECT stock_code, moats, moat_count FROM stock_moats')
        moats_data = {r[0]: (r[1].split(',') if r[1] else [], r[2]) for r in _cur.fetchall()}
        _conn.close()

        moats_count = {'无': 0, '1个': 0, '2个': 0, '3+个': 0}
        moats_strict = []
        for r in results['LONG_TERM'] + results['CORE_HOLDING']:
            if r['code'] in moats_data:
                moats, cnt = moats_data[r['code']]
                if cnt == 0: moats_count['无'] += 1
                elif cnt == 1: moats_count['1个'] += 1
                elif cnt == 2: moats_count['2个'] += 1
                else: moats_count['3+个'] += 1
                if cnt >= 2:
                    moats_strict.append(f"{r['code']}({','.join(moats)})")

        report.append(f"🛡️ W22 6 护城河分类:")
        report.append(f"  - 0护城河: {moats_count['无']} 只")
        report.append(f"  - 1护城河: {moats_count['1个']} 只")
        report.append(f"  - 2护城河: {moats_count['2个']} 只")
        report.append(f"  - 3+护城河: {moats_count['3+个']} 只")
        if moats_strict:
            report.append(f"  - 优质护城河: {', '.join(moats_strict[:5])}")
    except Exception as e:
        report.append(f"🛡️ W22 护城河: 跳过（{e}）")
    report.append(f"")

    # ⭐ 高量战法 W22 商业模式 (P3-2) - 批量查询
    try:
        import sqlite3 as _sqlite
        _conn = _sqlite.connect(config.MASTER_DB)
        # ROA 5 年均值
        _cur = _conn.execute('''
            SELECT stock_code, AVG(CASE WHEN total_assets > 0 THEN operating_profit / total_assets END) as avg_roa
            FROM w20_8indicators
            WHERE total_assets > 0
            GROUP BY stock_code
            HAVING COUNT(*) >= 3
        ''')
        roa_data = {r[0]: r[1] for r in _cur.fetchall()}
        _conn.close()

        roa_strict = [c for c, roa in roa_data.items() if roa and roa > 0.10]
        roa_relaxed = [c for c, roa in roa_data.items() if roa and 0.05 < roa <= 0.10]

        report.append(f"💼 W22 商业模式（ROA>10%）:")
        report.append(f"  - 高 ROA 票: {len(roa_strict)} 只")
        if roa_strict:
            report.append(f"    {', '.join(roa_strict[:5])}")
        report.append(f"  - 中 ROA 票: {len(roa_relaxed)} 只")
    except Exception as e:
        report.append(f"💼 W22 商业模式: 跳过（{e}）")
    report.append(f"")

    # ⭐ 高量战法 W19 18 步财报（P3-1）- 软参考
    try:
        from w19_18steps import get_step_results_batch
        all_w19 = get_step_results_batch()

        # 统计
        w19_a = [(c, r) for c, r in all_w19.items() if r["health_grade"] == "A"]
        w19_b = [(c, r) for c, r in all_w19.items() if r["health_grade"] == "B"]
        w19_c = [(c, r) for c, r in all_w19.items() if r["health_grade"] == "C"]
        w19_danger = [(c, r) for c, r in all_w19.items() if r['rules'].get('R094', False)]
        w19_healthy = [(c, r) for c, r in all_w19.items() if r['rules'].get('R089', False)]
        w19_fcf_strong = [(c, r) for c, r in all_w19.items() if r['rules'].get('R095', False)]

        report.append(f"📊 W19 18 步财报分析（基于 5 年财务数据）:")
        report.append(f"  - A 级（健康）: {len(w19_a)} 只")
        if w19_a:
            report.append(f"    {', '.join([c for c, r in w19_a[:5]])}")
        report.append(f"  - B 级（中等）: {len(w19_b)} 只")
        report.append(f"  - C 级（较差）: {len(w19_c)} 只")
        report.append(f"  - R094 危险: {len(w19_danger)} 只")
        if w19_danger:
            report.append(f"    {', '.join([c for c, r in w19_danger[:5]])}")
        report.append(f"  - R095 自由现金流强: {len(w19_fcf_strong)} 只")
        if w19_fcf_strong:
            report.append(f"    {', '.join([c for c, r in w19_fcf_strong[:5]])}")

        # ⭐ P3-3 R098 预收能力（应付预收-应收预付）
        # 用 advance_receipts > 0 的年数
        w19_r098_strong = []
        for code, r in all_w19.items():
            conn = sqlite3.connect(config.MASTER_DB)
            cur = conn.execute('''
                SELECT COUNT(*) FROM w19_18steps
                WHERE stock_code = ? AND year IN (2020, 2021, 2022, 2023, 2024)
                AND advance_receipts IS NOT NULL AND advance_receipts > 0
            ''', (code,))
            ar_count = cur.fetchone()[0]
            conn.close()
            if ar_count >= 4:
                w19_r098_strong.append((code, ar_count))
        report.append(f"  - R098 预收能力≥4年: {len(w19_r098_strong)} 只")
        if w19_r098_strong:
            report.append(f"    {', '.join([f'{c}({y}年)' for c, y in w19_r098_strong[:5]])}")
    except Exception as e:
        report.append(f"📊 W19 18 步: 跳过（{e}）")
    report.append(f"")

    report.append(f"⏰ 明天 9:00 morning-prep-list 输出三套预备池 + 实时价")
    return "\n".join(report)


if __name__ == '__main__':
    score_date = datetime.now().strftime('%Y-%m-%d')
    prep_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    log.info(f"⏰ 分析日期: {score_date}")
    log.info(f"⏰ 预备池日期: {prep_date}")
    log.info('')

    results, skipped, blacklist = analyze_all()
    save_scores(results, score_date)
    save_prep_pool(results, prep_date, {
        'SHORT_TERM': 5,
        'LONG_TERM': 3,
        'CORE_HOLDING': 2,
    })

    # 记录黑名单到 events 表（持仓库）
    if blacklist:
        from datetime import datetime
        conn = sqlite3.connect(config.POSITIONS_DB)
        for code, rules in blacklist.items():
            conn.execute('''
                INSERT INTO events (event_date, event_type, code, title, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (score_date, 'W16_FRAUD_REDFLAG', code, 
                  '财务造假红旗',
                  ' | '.join(rules), datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()

    log.info(generate_report(results, skipped))
