#!/usr/bin/env python3
"""
高量战法 v5.3.30 — 核心规则纯函数库
================================
全部函数只依赖入参数据（K 线序列 / 阈值），不访问 DB / 网络 —— 可独立单测。

工业级改造 Phase 3：自 monitor.py 逐字抽离：
  - B1-B8 加仓 / S1-S8 减仓 / C1-C6 清仓 三组信号判定
  - R090-R094 建仓时机（趋势阶段 / 位置百分位 / 底部形态 / MACD 背离 / 共振）
  - 波动率分档（C1 阈值自适应）

数据契约：
  klines: [(trade_date, open, high, low, close, volume), ...] 从早到晚排序
  rows  : DB 原始顺序（trade_date 降序），字段见各函数 docstring
  vol_class: {'volatility_max', 'c1', 'b7', 'name'}

抽离自 monitor.py；工业级改造中已修复三个规则 bug：
  C5 off-by-one（死代码激活）、高量日扫描含今日（C1/C3 破位失明）、
  R094 评分制 → 全 AND（对齐 SKILL.md「5 条件必须同时满足」+ 大盘择时接入）。
验收基准：test_rules.py 全绿。
"""

# ========== 原始高量战法参数 ==========

# C1 跌幅阈值（按 60 日均振幅分档）
C1_THRESHOLDS = [
    {'volatility_max': 2.0, 'c1': 1.0, 'b7': 1.0, 'name': '极低波动'},
    {'volatility_max': 3.0, 'c1': 1.5, 'b7': 1.5, 'name': '低波动'},
    {'volatility_max': 5.0, 'c1': 3.0, 'b7': 3.0, 'name': '普通'},
    {'volatility_max': 999.0, 'c1': 5.0, 'b7': 5.0, 'name': '高波动'},
]

# B8 极强趋势：3 日量价齐升 + 涨幅 ≥ 8%
B8_MIN_GAIN_3D = 8.0
B8_MIN_VOLUME_TREND = 'increasing'  # 量逐日增

# B4 高量上影突破：距高量 ≤ 10 日
B4_MAX_DAYS = 10

# S3 三日高低点下移
S3_DAYS = 3

# S5 连涨放量收阴
S5_MIN_RISE_DAYS = 4
S5_VOL_INCREASE = 1.5  # 量 > 前日 1.5 倍

# S7 高位量大实体小
S7_VOL_INCREASE = 1.5
S7_BODY_DECREASE = 0.5  # 实体 < 前日 50%


def volatility_class(vol):
    """波动率分档（原始高量战法）
    vol: 60 日均振幅（%）
    返回: {'volatility_max', 'c1', 'b7', 'name'}
    """
    for cls in C1_THRESHOLDS:
        if vol < cls['volatility_max']:
            return cls
    return C1_THRESHOLDS[-1]


def compute_position(hl, current_price):
    """R091 位置百分位：当前价格在 60 日区间的位置（0-100%）
    hl: {'high', 'low', 'max_close', 'min_close'} 或 None
    返回: 0-100（保留 1 位小数）；数据缺失返回 None
    """
    if not hl or hl['max_close'] == hl['min_close']:
        return None
    position_pct = (current_price - hl['min_close']) / (hl['max_close'] - hl['min_close']) * 100
    return round(max(0, min(100, position_pct)), 1)


def compute_trend_stage(rows):
    """R090 趋势阶段判定：基于 MA5/MA10/MA20 排列 + 量价特征

    rows: [(trade_date, close, high, low, volume), ...] 按 trade_date 降序（DB 原始顺序）

    返回 stage ∈ {bottom_building, starting, main_rising, topping, declining, consolidating, unknown}
    """
    if len(rows) < 10:
        return {'stage': 'unknown', 'reason': f'数据不足 {len(rows)} 日'}

    closes = [r[1] for r in rows[::-1] if r[1] is not None]  # 正序，过滤 None
    highs = [r[2] for r in rows[::-1] if r[2] is not None]
    lows = [r[3] for r in rows[::-1] if r[3] is not None]

    if len(closes) < 10:
        return {'stage': 'unknown', 'reason': f'有效数据不足 {len(closes)} 日'}

    ma5_now = sum(closes[-5:]) / 5 if len(closes) >= 5 else closes[-1]
    ma10_now = sum(closes[-10:]) / 10 if len(closes) >= 10 else closes[-1]
    ma20_now = sum(closes[-20:]) / 20 if len(closes) >= 20 else closes[-1]

    current = closes[-1]

    # 顶部阶段：最近 10 日大涨 + 距高点 < 5% + MA5 拐头向下
    recent_10_high = max(highs[-10:])
    distance_to_high = (recent_10_high - current) / recent_10_high * 100
    if distance_to_high < 5 and current < ma5_now:
        return {'stage': 'topping', 'reason': f'距10日高点 {distance_to_high:.1f}%+跌破MA5',
                'ma5': ma5_now, 'ma10': ma10_now, 'ma20': ma20_now, 'distance_to_high': distance_to_high}

    # 下跌阶段：MA5 < MA20 + 持续下跌
    if ma5_now < ma20_now:
        recent_10 = closes[-10:]
        decline_pct = (recent_10[-1] - recent_10[0]) / recent_10[0] * 100
        if decline_pct < -3:
            return {'stage': 'declining', 'reason': f'MA5<MA20+10日跌 {decline_pct:.1f}%',
                    'ma5': ma5_now, 'ma10': ma10_now, 'ma20': ma20_now, 'decline_pct': decline_pct}

    # 启动阶段：MA5 > MA10 + 价格站上 MA5
    if ma5_now > ma10_now and current > ma5_now:
        recent_5 = closes[-5:]
        recent_low = min(recent_5)
        recent_high = max(recent_5)
        rebound_pct = (current - recent_low) / recent_low * 100
        if rebound_pct <= 15:
            return {'stage': 'starting', 'reason': f'MA5>MA10+站上MA5+涨幅 {rebound_pct:.1f}%',
                    'ma5': ma5_now, 'ma10': ma10_now, 'ma20': ma20_now, 'rebound_pct': rebound_pct}
        else:
            return {'stage': 'main_rising', 'reason': f'MA5>MA10+主升 {rebound_pct:.1f}%',
                    'ma5': ma5_now, 'ma10': ma10_now, 'ma20': ma20_now, 'rebound_pct': rebound_pct}

    # 底部筑底：价格围绕 MA20 波动 + 振幅收窄
    if len(closes) >= 10:
        recent_10 = closes[-10:]
        high_10 = max(recent_10)
        low_10 = min(recent_10)
        range_pct = (high_10 - low_10) / low_10 * 100
        if range_pct < 8 and abs(current - ma10_now) / ma10_now < 0.05:
            return {'stage': 'bottom_building', 'reason': f'10日振幅 {range_pct:.1f}% 筑底',
                    'ma5': ma5_now, 'ma10': ma10_now, 'ma20': ma20_now, 'range_pct': range_pct}

    # 其他情况：横盘
    return {'stage': 'consolidating', 'reason': '横盘整理',
            'ma5': ma5_now, 'ma10': ma10_now, 'ma20': ma20_now}


def compute_macd(closes, fast=12, slow=26, signal=9):
    """R093 MACD 计算 + 背离判定
    closes: 收盘价列表，从早到晚
    返回最近 5 日 MACD 数据 + divergence ∈ {top_divergence, bottom_divergence, none}；数据不足返回 None
    """
    if len(closes) < slow + signal:
        return None

    # 计算 EMA
    def ema(data, period):
        k = 2 / (period + 1)
        ema_values = [data[0]]
        for price in data[1:]:
            ema_values.append(price * k + ema_values[-1] * (1 - k))
        return ema_values

    ema12 = ema(closes, fast)
    ema26 = ema(closes, slow)

    # DIF = EMA12 - EMA26
    dif = [a - b for a, b in zip(ema12, ema26)]
    # DEA = EMA(DIF, 9)
    dea = ema(dif, signal)
    # MACD = (DIF - DEA) * 2
    macd = [(d - e) * 2 for d, e in zip(dif, dea)]

    # 最近 5 日
    recent_diff = dif[-5:]
    recent_dea = dea[-5:]
    recent_macd = macd[-5:]
    recent_close = closes[-5:]

    # 顶背离：价格新高但 DIF 没新高
    # 底背离：价格新低但 DIF 没新低
    price_new_high = recent_close[-1] == max(recent_close)
    price_new_low = recent_close[-1] == min(recent_close)
    diff_not_high = recent_diff[-1] < max(recent_diff[:-1]) if len(recent_diff) > 1 else False
    diff_not_low = recent_diff[-1] > min(recent_diff[:-1]) if len(recent_diff) > 1 else False

    divergence = 'none'
    if price_new_high and diff_not_high and len(recent_diff) >= 3:
        divergence = 'top_divergence'  # ⚠️ 顶背离 - 强卖出信号
    elif price_new_low and diff_not_low and len(recent_diff) >= 3:
        divergence = 'bottom_divergence'  # ⭐ 底背离 - 强买入信号

    return {
        'dif': dif[-1],
        'dea': dea[-1],
        'macd': macd[-1],
        'recent_5_diff': recent_diff,
        'recent_5_close': recent_close,
        'divergence': divergence
    }


def compute_bottom_reversal(rows):
    """R092 底部反转形态确认（双底/底分型）
    rows: [(trade_date, open, high, low, close, volume), ...] 按 trade_date 降序（DB 原始顺序）
    """
    if len(rows) < 10:
        return {'has_pattern': False, 'pattern': 'unknown', 'reason': '数据不足'}

    rows = rows[::-1]  # 正序
    closes = [r[4] for r in rows if r[4] is not None]
    lows = [r[3] for r in rows if r[3] is not None]

    if len(closes) < 10 or len(lows) < 10:
        return {'has_pattern': False, 'pattern': 'unknown', 'reason': '有效数据不足'}

    # 1. 底分型（左肩 > 底部 < 右肩 + 右肩 > 左肩）
    if len(closes) >= 3:
        left = closes[-3]
        bottom = closes[-2]
        right = closes[-1]
        if left > bottom and right > left:
            # 右肩突破左肩 = 底分型成立
            return {'has_pattern': True, 'pattern': '底分型',
                    'left': left, 'bottom': bottom, 'right': right}

    # 2. 双底（最近 10 日内两个相近的低点）
    if len(lows) >= 10:
        recent_lows = lows[-10:]
        min_low = min(recent_lows)
        # 找最低点的位置
        min_idx = recent_lows.index(min_low)
        # 检查最低点前后是否有相近的低点
        if min_idx > 1 and min_idx < 8:
            left_low = min(recent_lows[:min_idx])
            right_low = min(recent_lows[min_idx+1:])
            # 两个低点差 < 5%
            if left_low > 0 and right_low > 0 and abs(left_low - right_low) / left_low < 0.05:
                # 中间反弹 > 3%
                mid_section = recent_lows[max(0, min_idx-1):min(len(recent_lows), min_idx+2)]
                mid_high = max(mid_section) if mid_section else min_low
                rebound = (mid_high - min_low) / min_low * 100 if min_low > 0 else 0
                if rebound > 3:
                    return {'has_pattern': True, 'pattern': '双底',
                            'left_low': left_low, 'right_low': right_low, 'mid_high': mid_high}

    return {'has_pattern': False, 'pattern': 'none', 'reason': '无底部形态'}


def evaluate_entry(trend, position, bottom, macd, market_ok=True):
    """R094 量价时空共振 — 建仓 5 条件综合判定（纯函数版，全 AND 语义）

    入参为四个预计算结果 + 大盘择时（由 monitor.py 的 DB 封装层提供）：
      trend:    compute_trend_stage 结果（R090）
      position: compute_position 结果，可为 None（R091）
      bottom:   compute_bottom_reversal 结果（R092）
      macd:     compute_macd 结果，可为 None（R093）
      market_ok: 大盘择时可建仓（R094 第 5 条件，默认 True）

    判定对齐 SKILL.md「建仓 5 条件必须同时满足」：
      can_buy = R090pass 且 R091pass 且 R092pass 且 R093pass 且 market_ok
    score 仅作展示，不参与判定。

    返回: {'can_buy': bool, 'score': int, 'details': {...}}
    """
    # R090 趋势阶段
    stage = trend['stage']
    r090_pass = stage in ['starting', 'bottom_building']
    r090_warn = stage == 'main_rising'
    r090_block = stage in ['topping', 'declining', 'unknown']

    # R091 位置百分位
    if position is None:
        r091_pass = False
        r091_warn = False
        r091_block = True
    elif position < 30 or position > 85:
        r091_pass = False
        r091_block = True
        r091_warn = position < 20 or position > 95
    elif 30 <= position <= 70:
        r091_pass = True
        r091_block = False
        r091_warn = False
    else:  # 70-85
        r091_pass = False
        r091_warn = True
        r091_block = False

    # R092 底部反转形态
    r092_pass = bottom['has_pattern']

    # R093 MACD 背离
    if macd is None:
        r093_pass = True  # 数据不足不强制
        r093_warn = False
        r093_block = False
        divergence = 'unknown'
    else:
        divergence = macd['divergence']
        if divergence == 'top_divergence':
            r093_pass = False
            r093_warn = False
            r093_block = True
        elif divergence == 'bottom_divergence':
            r093_pass = True
            r093_warn = False
            r093_block = False
        else:
            r093_pass = True  # 无背离可通过
            r093_warn = False
            r093_block = False

    # R094 第 5 条件：大盘择时
    r094_pass = bool(market_ok)

    # 综合评分（5 个条件，仅展示用）
    score = sum([r090_pass, r091_pass, r092_pass, r093_pass, r094_pass])

    # 全 AND 语义：5 条件必须同时满足（对齐 SKILL.md，v5.3.30 工业级改造）
    can_buy = r090_pass and r091_pass and r092_pass and r093_pass and r094_pass

    return {
        'can_buy': can_buy,
        'score': score,
        'details': {
            'R090_trend': {'stage': stage, 'reason': trend['reason'], 'pass': r090_pass, 'warn': r090_warn, 'block': r090_block},
            'R091_position': {'position_pct': position, 'pass': r091_pass, 'warn': r091_warn, 'block': r091_block},
            'R092_bottom': {'pattern': bottom['pattern'], 'pass': r092_pass},
            'R093_macd': {'divergence': divergence, 'pass': r093_pass, 'warn': r093_warn, 'block': r093_block},
            'R094_market': {'pass': r094_pass},
        }
    }


def check_c_rules(code, current_price, klines, vol_class):
    """原始高量战法 C1-C6 清仓规则（最高优先级，逐字抽离自 monitor.py）

    C1 跌破实体低：C1-a 微破（1 日缓冲）/ C1-b 长阴贯破（立即清）
    C3 T+2/T+3 失守：高量后 2/3 日收盘 < 实体低点 → 立即清
    C5 天量次日收阴：次日收盘前清
    C6 穿头破脚：无论高量阴阳 → 提前清

    klines: [(date, open, high, low, close, volume), ...] 从早到晚
    vol_class: {'name', 'c1', ...}
    返回: 触发的告警字符串列表
    """
    alerts = []
    if len(klines) < 3:
        return alerts

    c1_threshold = vol_class['c1']

    # 找最近的高量日（量 > 前 3 日任一天）
    high_volume_idx = None
    for i in range(len(klines) - 2, max(len(klines) - 10, 0), -1):  # bugfix: 不含今日——今日是判定对象，非参照日
        if i < 1:
            continue
        prev_vols = [klines[j][5] for j in range(max(0, i-3), i)]
        if klines[i][5] > max(prev_vols) if prev_vols else False:
            high_volume_idx = i
            break

    if high_volume_idx is None:
        return alerts

    high_vol_day = klines[high_volume_idx]
    high_vol_open = high_vol_day[1]
    high_vol_close = high_vol_day[4]
    entity_low = min(high_vol_open, high_vol_close)
    entity_high = max(high_vol_open, high_vol_close)

    # T+1/T+2/T+3 日
    days_after = len(klines) - 1 - high_volume_idx
    today = klines[-1]
    today_close = today[4]
    today_low = today[3]
    today_pct = (today_close / klines[-2][4] - 1) * 100 if len(klines) >= 2 else 0

    # C1 跌破实体低
    if today_close < entity_low:
        # C1-a 微破（< C1 阈值）: 1 日缓冲
        # C1-b 长阴贯破（>= C1 阈值）: 立即清
        c1_limit = entity_low * (1 - c1_threshold / 100)
        if today_close <= c1_limit:
            alerts.append(f'🔴 C1-b 长阴贯破：收盘 {today_close} ≤ 实体低 {entity_low:.2f}×{c1_threshold}%={c1_limit:.2f}（立即清仓）')
        else:
            alerts.append(f'🟡 C1-a 微破：收盘 {today_close} < 实体低 {entity_low:.2f}，跌幅 {abs(today_pct):.2f}% < C1 阈值 {c1_threshold}%（1 日缓冲，明日不收回触发 C1-b）')

    # C3 T+2/T+3 失守
    if 2 <= days_after <= 3:
        if today_close < entity_low:
            alerts.append(f'🔴 C3 T+{days_after} 失守：收盘 {today_close} < 实体低 {entity_low:.2f}（立即清仓）')

    # C5 天量次日收阴
    if days_after == 1 and today_close < klines[-2][4]:
        prev_vol = klines[-2][5]
        # 检查"天量"（前一日是否高量）
        prev_vols = [klines[j][5] for j in range(max(0, len(klines)-2-3), len(klines)-2)]  # bugfix: 昨日之前的 3 日，不含昨日自身
        if prev_vols and prev_vol > max(prev_vols):
            alerts.append(f'🔴 C5 天量次日收阴：今日收阴 {today_close:.2f} < 昨收 {klines[-2][4]:.2f}（次日收盘前清）')

    # C6 穿头破脚
    if len(klines) >= 2:
        prev = klines[-2]
        prev_entity = abs(prev[4] - prev[1])
        today_entity = abs(today_close - today[1])
        if prev_entity > 0 and today_entity > 0:
            # 昨阳今阴 且 今日实体 吞没 昨日实体
            if prev[4] > prev[1] and today_close < today[1]:
                if (today[1] >= prev[4] and today_close <= prev[1]):
                    alerts.append(f'🔴 C6 穿头破脚：今日 {today[1]:.2f}→{today_close:.2f} 吞没昨日 {prev[1]:.2f}→{prev[4]:.2f}（提前清仓）')

    return alerts


def check_s_rules(code, current_price, klines, vol_class):
    """原始高量战法 S1-S8 减仓规则（逐字抽离自 monitor.py）

    S1 压力线遇阻：上影线触及压力位后回落 → 减半
    S3 三日高低点下移：高量后 3 日每日高低点均低于前日 → 减半
    S5 连涨放量收阴：连续上涨 ≥ 4 日首次放量收阴 → 减 70%
    S6 高位大长腿：高位下影 > 实体 2 倍 → 减半
    S7 高位量大实体小：高位量 > 前日 1.5 倍 + 实体 < 前日 50% → 减 70%
    """
    alerts = []
    if len(klines) < 4:
        return alerts

    today = klines[-1]
    today_open = today[1]
    today_high = today[2]
    today_low = today[3]
    today_close = today[4]
    today_vol = today[5]
    today_body = abs(today_close - today_open)
    today_upper_shadow = today_high - max(today_close, today_open)
    today_lower_shadow = min(today_close, today_open) - today_low

    # S1 压力线遇阻：上影线触及高量压力位（实体高）后回落，收盘 < 压力位
    for i in range(len(klines) - 2, max(len(klines) - 10, 0), -1):  # bugfix: 不含今日——今日是判定对象，非参照日
        if i < 1:
            continue
        prev_vols = [klines[j][5] for j in range(max(0, i-3), i)]
        if prev_vols and klines[i][5] > max(prev_vols):
            # 找到高量日
            high_vol_high = max(klines[i][1], klines[i][4])
            if today_high >= high_vol_high and today_close < high_vol_high:
                alerts.append(f'🟠 S1 压力线遇阻：上影 {today_high:.2f} ≥ 压力位 {high_vol_high:.2f}，但收盘 {today_close:.2f} < 压力位（减半）')
            break

    # S3 三日高低点下移：最近 3 日每日高点都低于前日高点 且 每日低点都低于前日低点
    if len(klines) >= 4:
        last3 = klines[-3:]
        highs = [k[2] for k in last3]
        lows = [k[3] for k in last3]
        if all(highs[i] < highs[i-1] for i in range(1, len(highs))) and \
           all(lows[i] < lows[i-1] for i in range(1, len(lows))):
            alerts.append(f'🟠 S3 三日高低点下移：高点 {highs[0]:.2f}→{highs[1]:.2f}→{highs[2]:.2f}，低点 {lows[0]:.2f}→{lows[1]:.2f}→{lows[2]:.2f}（减半）')

    # S5 连涨放量收阴：连续上涨 ≥ 4 日，首次放量收阴
    if len(klines) >= S5_MIN_RISE_DAYS + 1:
        rise_count = 0
        for k in klines[-(S5_MIN_RISE_DAYS+1):-1]:
            if k[4] > k[1]:  # 收阳
                rise_count += 1
        if rise_count >= S5_MIN_RISE_DAYS and today_close < today_open:
            prev_vol = klines[-2][5]
            if prev_vol > 0 and today_vol > prev_vol * S5_VOL_INCREASE:
                alerts.append(f'🔴 S5 连涨放量收阴：连续 {rise_count} 日上涨后，今日 {today_close:.2f} < 开 {today_open:.2f}，量 {today_vol:.0f} > 昨 {prev_vol:.0f}×{S5_VOL_INCREASE}（减 70%）')

    # S6 高位大长腿：下影 > 实体 2 倍
    if today_lower_shadow > today_body * 2 and today_body > 0:
        # 检查是否高位（最近 5 日涨幅 > 5%）
        if len(klines) >= 5:
            recent_change = (today_close / klines[-5][4] - 1) * 100
            if recent_change > 5:
                alerts.append(f'🟠 S6 高位大长腿：下影 {today_lower_shadow:.2f} > 实体 {today_body:.2f}×2，近 5 日涨幅 +{recent_change:.2f}%（减半）')

    # S7 高位量大实体小：量 > 前日 1.5 倍 + 实体 < 前日 50%
    if len(klines) >= 2:
        prev = klines[-2]
        prev_body = abs(prev[4] - prev[1])
        if today_body > 0 and prev_body > 0:
            if today_vol > prev[5] * S7_VOL_INCREASE and today_body < prev_body * S7_BODY_DECREASE:
                # 检查高位
                if len(klines) >= 5:
                    recent_change = (today_close / klines[-5][4] - 1) * 100
                    if recent_change > 3:
                        alerts.append(f'🔴 S7 高位量大实体小：量 {today_vol:.0f} > 昨 {prev[5]:.0f}×{S7_VOL_INCREASE}，实体 {today_body:.2f} < 昨 {prev_body:.2f}×{S7_BODY_DECREASE}（减 70%）')

    return alerts


def check_b_rules(code, current_price, klines, cost):
    """原始高量战法 B1-B8 加仓规则（逐字抽离自 monitor.py）
    注：code / current_price / cost 保留自原签名（cost 未在体内使用，为兼容保留）

    B1 支撑线上方底分型：回踩高量支撑线不破，'低-更低-高'三 K 线底分型
    B2 支撑线上方红三兵：连续 3 根阳线，实体逐日放大或持平
    B4 高量上影线被阳线实体突破：主升浪确认
    B6 缩量过前高：突破前高，量 < 前高当日
    B8 极强趋势跟随：连续 3 日量价齐升（量逐日增+价创新高+3 日涨幅 ≥ 8%）
    """
    alerts = []
    if len(klines) < 5:
        return alerts

    today = klines[-1]
    today_close = today[4]
    today_vol = today[5]

    # 找最近高量日
    high_volume_idx = None
    for i in range(len(klines) - 2, max(len(klines) - 10, 0), -1):  # bugfix: 不含今日——今日是判定对象，非参照日
        if i < 1:
            continue
        prev_vols = [klines[j][5] for j in range(max(0, i-3), i)]
        if prev_vols and klines[i][5] > max(prev_vols):
            high_volume_idx = i
            break

    # B1 底分型（低-更低-高）+ 站上支撑
    if len(klines) >= 3:
        last3 = klines[-3:]
        l, m, r = last3
        # 低-更低-高（中间最低）
        if l[3] > m[3] and r[3] > m[3] and r[4] > m[4]:
            if high_volume_idx is not None:
                entity_low = min(klines[high_volume_idx][1], klines[high_volume_idx][4])
                if r[3] >= entity_low:
                    alerts.append(f'🟢 B1 底分型：低-更低-高 + 站上高量支撑 {entity_low:.2f}（加仓 1/3）')
            else:
                alerts.append(f'🟢 B1 底分型：低-更低-高（加仓 1/3）')

    # B2 红三兵：连续 3 根阳线 + 实体逐日放大或持平
    if len(klines) >= 3:
        last3 = klines[-3:]
        if all(k[4] > k[1] for k in last3):
            bodies = [abs(k[4] - k[1]) for k in last3]
            if bodies[0] <= bodies[1] <= bodies[2] or bodies[0] >= bodies[1] >= bodies[2]:
                alerts.append(f'🟢 B2 红三兵：连续 3 阳 + 实体逐日变化（加仓 1/3）')

    # B4 高量上影突破：今日收盘 > 高量实体高
    if high_volume_idx is not None:
        days_since = len(klines) - 1 - high_volume_idx
        entity_high = max(klines[high_volume_idx][1], klines[high_volume_idx][4])
        if days_since <= B4_MAX_DAYS and today_close > entity_high:
            alerts.append(f'🟢 B4 高量上影突破：今日 {today_close:.2f} > 高量实体高 {entity_high:.2f}（主升浪确认，加仓到 40%）')

    # B6 缩量过前高：突破前高 + 量 < 前高当日
    if len(klines) >= 4:
        prev_high_close = max(k[4] for k in klines[-4:-1])
        if today_close > prev_high_close:
            prev_high_vol_idx = None
            for k in klines[-4:-1]:
                if k[4] == prev_high_close:
                    break
            # 找前高对应日期
            for i, k in enumerate(klines[:-1]):
                if k[4] == prev_high_close:
                    if i + 1 < len(klines):
                        prev_high_vol = klines[i+1][5]
                        if today_vol < prev_high_vol:
                            days_diff = len(klines) - 1 - i - 1
                            if days_diff >= 3:
                                alerts.append(f'🟢 B6 缩量过前高：今日 {today_close:.2f} > 前高 {prev_high_close:.2f}，量 {today_vol:.0f} < 前高 {prev_high_vol:.0f}（加仓 1/3）')

    # B8 极强趋势：连续 3 日量价齐升（量逐日增 + 价创新高 + 3 日涨幅 ≥ 8%）
    if len(klines) >= 4:
        last3 = klines[-3:]
        # 量逐日增
        vols = [k[5] for k in last3]
        if all(vols[i] > vols[i-1] for i in range(1, len(vols))):
            # 价创新高
            if all(k[4] > k[1] for k in last3):  # 连续 3 阳
                # 3 日涨幅 ≥ 8%
                gain_3d = (last3[-1][4] / klines[-4][4] - 1) * 100
                if gain_3d >= B8_MIN_GAIN_3D:
                    alerts.append(f'🟢 B8 极强趋势：连续 3 日量价齐升 + 涨幅 {gain_3d:.2f}% ≥ {B8_MIN_GAIN_3D}%（加仓 1/3）')

    return alerts
