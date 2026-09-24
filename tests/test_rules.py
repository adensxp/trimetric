#!/usr/bin/env python3
"""
高量战法 v5.3.30 核心规则单测（纯函数，无 DB / 网络）
运行：python3 tests/test_rules.py   （pytest 兼容：pytest tests/）

覆盖：C1-C6 清仓 / S1-S8 减仓 / B1-B8 加仓 / R090-R094 建仓时机的判定边界。
原则：钉住当前实现行为。已修复三个规则 bug：C5 off-by-one、「高量扫描含今日」、
R094 评分制 → 全 AND（对齐 SKILL.md「5 条件必须同时满足」+ 大盘择时接入）。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))
import rules  # noqa: E402

VOL_NORMAL = {'volatility_max': 5.0, 'c1': 3.0, 'b7': 3.0, 'name': '普通'}


def K(o, h, l, c, v, d='D'):
    """构造 K 线元组 (trade_date, open, high, low, close, volume)"""
    return (d, o, h, l, c, v)


def has(alerts, tag):
    return any(tag in a for a in alerts)


# ========== 波动率分档 ==========

def test_volatility_class_boundaries():
    assert rules.volatility_class(1.9)['name'] == '极低波动'
    assert rules.volatility_class(2.0)['name'] == '低波动'   # 2.0 不属于极低（需严格 < 2.0）
    assert rules.volatility_class(2.9)['name'] == '低波动'
    assert rules.volatility_class(3.0)['name'] == '普通'
    assert rules.volatility_class(4.9)['name'] == '普通'
    assert rules.volatility_class(5.0)['name'] == '高波动'
    assert rules.volatility_class(99.0)['c1'] == 5.0


# ========== R091 位置百分位 ==========

def test_compute_position():
    hl = {'high': 100, 'low': 50, 'max_close': 100, 'min_close': 50}
    assert rules.compute_position(hl, 75) == 50.0
    assert rules.compute_position(hl, 65) == 30.0   # 安全区下边界（含）
    assert rules.compute_position(hl, 70) == 40.0
    assert rules.compute_position(hl, 200) == 100  # 钳制
    assert rules.compute_position(hl, 0) == 0
    assert rules.compute_position(None, 75) is None
    assert rules.compute_position({'high': 1, 'low': 1, 'max_close': 1, 'min_close': 1}, 1) is None


# ========== R090 趋势阶段 ==========

def _rows_from_closes(closes):
    """构造 compute_trend_stage 入参（DESC 顺序，含高低点扰动）"""
    asc = [(f'd{i}', c, c + 0.5, c - 0.5, 100) for i, c in enumerate(closes)]
    return asc[::-1]


def test_trend_stage_topping():
    closes = [100.0] * 9 + [98.0]   # 高位滞涨回落
    res = rules.compute_trend_stage(_rows_from_closes(closes))
    assert res['stage'] == 'topping', res


def test_trend_stage_declining():
    closes = [110.0 - 0.6 * i for i in range(25)]   # 110 → 95.6 持续阴跌
    res = rules.compute_trend_stage(_rows_from_closes(closes))
    assert res['stage'] == 'declining', res


def test_trend_stage_starting():
    closes = [100.0] * 12 + [102.0, 103.0, 104.0]   # 站上 MA5 + 反弹 4% ≤ 15%
    res = rules.compute_trend_stage(_rows_from_closes(closes))
    assert res['stage'] == 'starting', res


def test_trend_stage_main_rising():
    closes = [100.0] * 12 + [105.0, 112.0, 120.0]   # 反弹 20% > 15%
    res = rules.compute_trend_stage(_rows_from_closes(closes))
    assert res['stage'] == 'main_rising', res


def test_trend_stage_bottom_building():
    closes = [100.0, 99.4, 98.8, 98.2, 97.6, 97.0, 96.4, 95.8, 95.2, 94.6]
    res = rules.compute_trend_stage(_rows_from_closes(closes))
    assert res['stage'] == 'bottom_building', res


def test_trend_stage_unknown_when_insufficient():
    res = rules.compute_trend_stage([('d', 100, 100, 100, 100)] * 5)
    assert res['stage'] == 'unknown'


# ========== R093 MACD ==========

def test_compute_macd_basic():
    assert rules.compute_macd([100.0] * 10) is None          # 数据不足 → None
    flat = rules.compute_macd([100.0] * 40)
    assert flat['divergence'] == 'none' and abs(flat['dif']) < 1e-9
    up = rules.compute_macd([100.0 + i for i in range(40)])
    assert up['dif'] > 0 and up['divergence'] == 'none'


def test_compute_macd_bottom_divergence():
    # 长阴跌 → 深坑(39.5) → 反弹 → 边际新低(39.4)：价新低但 DIF 抬高 = 底背离
    closes = [50.0 - 0.3 * i for i in range(30)] + [40.5, 39.5, 40.2, 40.6, 40.9, 39.4]
    res = rules.compute_macd(closes)
    assert res['divergence'] == 'bottom_divergence', (res['dif'], res['recent_5_diff'])


def test_compute_macd_top_divergence():
    # 长上涨 → 冲顶(60.5) → 回调 → 边际新高(60.6)：价新高但 DIF 走低 = 顶背离
    closes = [50.0 + 0.3 * i for i in range(30)] + [59.5, 60.5, 59.8, 59.4, 59.1, 60.6]
    res = rules.compute_macd(closes)
    assert res['divergence'] == 'top_divergence', (res['dif'], res['recent_5_diff'])


# ========== R092 底部形态 ==========

def _bh_rows(closes, lows):
    asc = [(f'd{i}', c, c + 0.3, l, c, 100) for i, (c, l) in enumerate(zip(closes, lows))]
    return asc[::-1]


def test_bottom_reversal_fenxing():
    closes = [10.0] * 7 + [10.5, 10.0, 10.8]
    lows = [c - 0.2 for c in closes]
    res = rules.compute_bottom_reversal(_bh_rows(closes, lows))
    assert res['has_pattern'] and res['pattern'] == '底分型', res


def test_bottom_reversal_double_bottom():
    lows = [12.0, 10.3, 12.0, 11.0, 10.0, 11.5, 10.2, 12.0, 12.0, 12.0]
    closes = [12.0, 11.5, 12.2, 11.8, 10.0, 11.6, 10.3, 11.9, 11.8, 11.7]
    res = rules.compute_bottom_reversal(_bh_rows(closes, lows))
    assert res['has_pattern'] and res['pattern'] == '双底', res


def test_bottom_reversal_none():
    closes = [10.0 + 0.5 * i for i in range(10)]   # 一路上行
    res = rules.compute_bottom_reversal(_bh_rows(closes, closes))
    assert not res['has_pattern'], res


# ========== R094 建仓共振 ==========

def test_evaluate_entry_matrix():
    # 全 AND 语义回归（v5.3.30 工业级改造）
    T_START = {'stage': 'starting', 'reason': 'x'}
    T_TOP = {'stage': 'topping', 'reason': 'x'}
    B_PAT = {'has_pattern': True, 'pattern': '底分型'}
    B_NO = {'has_pattern': False, 'pattern': 'none'}
    M_OK = {'dif': 0, 'dea': 0, 'macd': 0, 'divergence': 'none'}
    M_TOP = {'dif': 0, 'dea': 0, 'macd': 0, 'divergence': 'top_divergence'}

    # 全 AND 语义（v5.3.30 工业级改造对齐 SKILL.md「5 条件必须同时满足」）
    r = rules.evaluate_entry(T_START, 50.0, B_PAT, M_OK)
    assert r['can_buy'] and r['score'] == 5, r

    # 任一条件不满足 → 禁（全 AND）
    assert not rules.evaluate_entry(T_TOP, 50.0, B_PAT, M_OK)['can_buy']                     # R090 顶部
    assert not rules.evaluate_entry(T_START, 90.0, B_PAT, M_OK)['can_buy']                   # R091 禁区
    assert not rules.evaluate_entry(T_START, None, B_PAT, M_OK)['can_buy']                   # R091 无数据
    assert not rules.evaluate_entry(T_START, 75.0, B_PAT, M_OK)['can_buy']                   # R091 警告区 70-85（收紧）
    assert not rules.evaluate_entry(T_START, 50.0, B_NO, M_OK)['can_buy']                    # R092 无形态（收紧）
    assert not rules.evaluate_entry(T_START, 50.0, B_PAT, M_TOP)['can_buy']                   # R093 顶背离
    assert not rules.evaluate_entry(T_START, 50.0, B_PAT, M_OK, market_ok=False)['can_buy']  # R094 大盘风险（新增）

    # 警告区标志保留（仅展示）
    r = rules.evaluate_entry(T_START, 75.0, B_PAT, M_OK)
    assert not r['can_buy'] and r['details']['R091_position']['warn'], r

    # macd None → 不强制
    r = rules.evaluate_entry(T_START, 50.0, B_PAT, None)
    assert r['can_buy'] and r['details']['R093_macd']['divergence'] == 'unknown', r

    # R094_market 细节存在
    assert 'R094_market' in r['details'] and r['details']['R094_market']['pass']


# ========== C1-C6 清仓 ==========

def _c_base():
    return [K(10, 10.5, 9.5, 10.0, 100),
            K(10, 20.5, 10.0, 20.0, 500),    # d1 高量日，实体低 10
            K(20, 20.6, 19.9, 20.5, 300),
            K(20.5, 20.8, 20.2, 21.0, 200)]


def test_c1_threshold_boundary():
    # 普通品种 C1 阈值 3% → 贯破线 = 10 × 0.97 = 9.7
    a = rules.check_c_rules('T', 0, _c_base() + [K(20, 20.5, 9.0, 9.7, 250)], VOL_NORMAL)
    assert has(a, 'C1-b 长阴贯破'), a              # 收盘恰在阈值线（<=）→ 立即清
    a = rules.check_c_rules('T', 0, _c_base() + [K(20, 20.5, 9.0, 9.8, 250)], VOL_NORMAL)
    assert has(a, 'C1-a 微破') and not has(a, 'C1-b 长阴贯破'), a  # 微破 → 1 日缓冲（消息含 C1-b 字样，须用完整短语）
    # 收盘恰等于实体低 → 支撑有效，不触发任何 C 信号
    a = rules.check_c_rules('T', 0, _c_base() + [K(20, 20.5, 9.0, 10.0, 250)], VOL_NORMAL)
    assert a == [], a


def test_c3_t23_breakdown():
    # T+1 破位不触发 C3（走 C1-a 缓冲）
    d = [K(10, 10.5, 9.5, 10.0, 100), K(10, 20.5, 10.0, 20.0, 500),
         K(19.5, 20.0, 9.5, 9.8, 200)]
    a = rules.check_c_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'C1-a') and not has(a, 'C3'), a
    # T+2 破位 → C3 立即清
    d = [K(10, 10.5, 9.5, 10.0, 100), K(10, 20.5, 10.0, 20.0, 500),
         K(20, 20.5, 15.0, 15.5, 200), K(15.5, 15.8, 9.5, 9.6, 150)]
    a = rules.check_c_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'C3'), a


def test_c5_skyvol_next_red():
    # bug 修复回归：昨日量 > 其前 3 日（天量）+ 今日收阴 → C5 触发
    d = [K(10, 10.5, 9.5, 10.0, 100), K(15, 20.5, 15.0, 20.0, 400),
         K(20, 20.2, 18.5, 19.0, 150)]
    a = rules.check_c_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'C5 天量次日收阴') and len(a) == 1, a


def test_c_panic_breakdown_not_blind():
    # bug 修复回归：恐慌天量破位（今日量 700 为区间最大）——
    # 修复前今日自身被当作高量日参照 → C1/C3 失明；修复后正常触发
    d = [K(10, 10.5, 9.5, 10.0, 100), K(10, 20.5, 10.0, 20.0, 500),
         K(20, 20.6, 19.9, 20.5, 300), K(20.5, 20.8, 20.2, 21.0, 200),
         K(21.0, 21.5, 8.5, 9.0, 700)]
    a = rules.check_c_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'C1-b 长阴贯破') and has(a, 'C3'), a


def test_c6_engulfing():
    d = [K(10, 10.5, 9.5, 10.0, 100),
         K(5, 5.5, 5.0, 5.0, 500),      # 高量一字板（实体 5-5）
         K(10, 12.2, 10.0, 12.0, 50),   # 昨日大阳（实体 10-12）
         K(12.2, 12.3, 9.5, 9.9, 30)]   # 今日阴线吞没昨日实体顶+底
    a = rules.check_c_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'C6'), a


def test_c_no_high_volume_no_alerts():
    d = [K(10, 10.5, 9.5, 10.0, 100)] * 5   # 量全相等 → 无高量日
    assert rules.check_c_rules('T', 0, d, VOL_NORMAL) == []


# ========== S1-S8 减仓 ==========

def test_s3_descending():
    d = [K(10, 25, 9, 10, 100), K(20, 20, 20, 20, 100),
         K(19, 19, 19, 19, 100), K(18, 18, 18, 18, 100)]
    a = rules.check_s_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'S3') and len(a) == 1, a


def test_s5_volume_reversal():
    # 注：S1 压力位取自此前高量日——构造 d2 高量阳线且实体（12.0）
    # 压过今日上影（11.95），隔离出 S5 单信号。
    d = [K(10, 10.4, 9.8, 10.2, 100), K(10.2, 10.6, 10.0, 10.4, 100),
         K(11.5, 11.8, 11.2, 12.0, 500), K(11.9, 12.1, 11.6, 12.0, 100),
         K(11.9, 11.95, 10.4, 10.5, 160)]
    a = rules.check_s_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'S5') and len(a) == 1, a
    # 量不足 1.5 倍 → 不触发
    d2 = [k for k in d[:-1]] + [K(11.9, 11.95, 10.4, 10.5, 140)]
    assert rules.check_s_rules('T', 0, d2, VOL_NORMAL) == []


def test_s6_long_leg():
    d = [K(19, 19.2, 18.8, 19.0, 100), K(19.5, 19.8, 19.2, 19.5, 100),
         K(19.5, 19.8, 19.2, 19.5, 100), K(19.5, 19.8, 19.2, 19.5, 100),
         K(20, 20.8, 18.0, 20.5, 100)]
    a = rules.check_s_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'S6') and len(a) == 1, a


def test_s7_big_vol_small_body():
    d = [K(19, 19.2, 18.8, 19.0, 100), K(19.5, 19.9, 19.2, 19.8, 100),
         K(19.8, 20.0, 19.5, 19.9, 100), K(19.9, 20.6, 19.5, 20.2, 100),
         K(21.0, 21.3, 20.8, 21.1, 200)]
    a = rules.check_s_rules('T', 0, d, VOL_NORMAL)
    assert has(a, 'S7') and len(a) == 1, a


# ========== B1-B8 加仓 ==========

def test_b1_bottom_fenxing():
    d = [K(10, 10.5, 9.5, 10.0, 100), K(10, 12.5, 10.0, 12.0, 500),
         K(12.0, 12.4, 11.0, 11.5, 200), K(11.5, 11.8, 10.8, 11.0, 150),
         K(11.0, 12.2, 11.0, 12.0, 180)]
    a = rules.check_b_rules('T', 0, d, 0)
    assert has(a, 'B1') and len(a) == 1, a


def test_b2_three_soldiers():
    d = [K(10, 10.5, 9.5, 10.0, 100), K(10, 10.5, 9.9, 10.3, 100),
         K(10.3, 10.9, 10.2, 10.8, 100), K(10.8, 11.6, 10.7, 11.5, 100),
         K(11.5, 12.8, 11.4, 12.6, 100)]
    a = rules.check_b_rules('T', 0, d, 0)
    assert has(a, 'B2') and len(a) == 1, a


def test_b4_highvol_breakout():
    d = [K(10, 10.5, 9.5, 10.0, 100), K(10, 12.5, 10.0, 12.0, 500),
         K(12.0, 12.6, 11.8, 12.3, 200), K(12.3, 12.8, 12.0, 12.5, 150),
         K(12.5, 13.6, 12.4, 13.5, 180)]
    a = rules.check_b_rules('T', 0, d, 0)
    assert has(a, 'B4') and len(a) == 1, a


def test_b6_shrink_break_high():
    d = [K(13.9, 14.0, 13.8, 13.9, 300), K(13.9, 14.0, 13.8, 13.9, 300),
         K(13.9, 14.0, 13.8, 13.9, 300), K(13.9, 14.1, 13.8, 14.0, 300),
         K(13.9, 14.0, 13.8, 13.9, 300), K(13.8, 13.9, 13.7, 13.8, 300),
         K(13.9, 14.1, 13.8, 14.0, 300), K(13.5, 13.7, 13.3, 13.4, 300),
         K(13.4, 13.7, 13.3, 13.5, 300), K(14.0, 14.3, 13.9, 14.2, 250)]
    a = rules.check_b_rules('T', 0, d, 0)
    assert has(a, 'B6') and len(a) == 1, a


def test_b8_extreme_trend():
    # 注：B8 量递增使昨日成为高量日（今日已排除出扫描）→ B4 伴随触发
    d = [K(9.9, 10.1, 9.8, 10.0, 30), K(10, 10.2, 9.8, 10.0, 50),
         K(10.0, 10.4, 9.9, 10.3, 100), K(10.3, 10.7, 10.2, 10.5, 150),
         K(10.5, 11.0, 10.4, 10.8, 200)]
    a = rules.check_b_rules('T', 0, d, 0)
    assert has(a, 'B8') and has(a, 'B4') and len(a) == 2, a
    # 3 日涨幅 7.9% < 8% → 不触发 B8（边界）；B4 仍触发（10.79 > 昨日高量实体高 10.5）
    d2 = d[:-1] + [K(10.5, 11.0, 10.4, 10.79, 200)]
    a2 = rules.check_b_rules('T', 0, d2, 0)
    assert not has(a2, 'B8') and has(a2, 'B4') and len(a2) == 1, a2


# ========== monitor.py 委托一致性 ==========

def test_monitor_delegation():
    import monitor
    for fn in ['get_trend_stage', 'get_60d_position', 'get_macd', 'check_bottom_reversal',
               'check_entry_conditions_v5_3_30', 'get_volatility_class',
               'check_c_rules', 'check_s_rules', 'check_b_rules']:
        assert hasattr(monitor, fn), fn
    kl = [K(10, 10.5, 9.5, 10.0, 100), K(10, 20.5, 10.0, 20.0, 500),
          K(20, 20.5, 9.0, 9.7, 250)]
    assert monitor.check_c_rules('T', 0, kl, VOL_NORMAL) == rules.check_c_rules('T', 0, kl, VOL_NORMAL)
    assert monitor.check_s_rules('T', 0, kl, VOL_NORMAL) == rules.check_s_rules('T', 0, kl, VOL_NORMAL)
    assert monitor.check_b_rules('T', 0, kl, 0) == rules.check_b_rules('T', 0, kl, 0)


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS  ' + name)
        except AssertionError as e:
            failed += 1
            print('FAIL  %s: %s' % (name, e))
    print()
    print('%d/%d 通过' % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
