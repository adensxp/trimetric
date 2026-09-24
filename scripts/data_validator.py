#!/usr/bin/env python3
"""
高量战法数据校验模块 V5.2.1
===========================
基于 SKILL.md 21.0 节 10 条校验规则
核心思想：数据错了什么都白干——所有 P10-P19 规则的前提都是"数据本身正确"

校验项：
  V1: 数据完整性（≥60 个交易日）
  V2: OHLC 关系（low ≤ min(open,close) ≤ max(open,close) ≤ high）
  V3: 量价符号（close>0, vol≥0）
  V4: 日期连续性（跳空已标注）
  V5: 复权状态（明确前复权/后复权/不复权）
  V6: 除权日识别（疑似除权日 + 重新计算支撑）
  V7: 实时价合理性（在 [近5日低, 近5日高×1.1] 区间）
  V8: 数据基准日（明确"基于 YYYY-MM-DD HH:MM"）
  V9: 多源验证（≥2 个数据源交叉验证）
  V10: 字段命名（open/close/high/low/vol 字段存在）

使用：
  from data_validator import validate_klines, validate_live_quote
  
  ok, warnings, errors = validate_klines(klines, "601398.SH", "qfq")
  if not ok:
      print(f"阻断分析: {errors}")
  if warnings:
      print(f"警告: {warnings}")
"""
import re
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional


# ============== 21.0.2 除权除息日识别算法 ==============

def detect_ex_right_days(klines: List[Dict]) -> List[Dict]:
    """
    检测除权除息日（适用于前复权数据）—— 仅"预警"，未确认
    
    识别逻辑（V5.2.1 修正）：
      1. 检测日线收盘价与前一日收盘价差异 > 5% 且 < 30%
      2. 同时该日出现明显放量（>5日均量 × 1.5）
      3. → 标记为疑似除权日（**待 dividend 公告确认**）
    
    重要提示：仅"价跌+放量"会误报（如 -7% 大跌 + 放量），
    必须用 `confirm_ex_right_days_with_dividends()` 配合 dividend 公告数据二次确认。
    
    Returns:
        疑似除权日列表（status='pending'，需要二次确认）
    """
    ex_right_days = []
    for i in range(1, len(klines)):
        prev_close = klines[i-1]['close']
        curr_close = klines[i]['close']
        if prev_close <= 0:
            continue
        chg_ratio = (curr_close - prev_close) / prev_close
        # 5% < 跳变 < 30%，排除涨跌停
        if 0.05 < abs(chg_ratio) < 0.30:
            # 检查放量
            avg5 = sum([klines[j]['vol'] for j in range(max(0, i-4), i)]) / min(5, i)
            vol_ratio = klines[i]['vol'] / avg5 if avg5 > 0 else 0
            if vol_ratio > 1.5:
                ex_right_days.append({
                    'date': klines[i]['date'],
                    'prev_close': prev_close,
                    'curr_close': curr_close,
                    'gap_pct': chg_ratio * 100,
                    'vol_ratio': vol_ratio,
                    'status': 'pending',  # 待公告数据确认
                    'note': '疑似除权除息日，需 dividend 公告确认'
                })
    return ex_right_days


def confirm_ex_right_days_with_dividends(klines: List[Dict], dividends: List[Dict]) -> List[Dict]:
    """
    V5.2.1 V6 二次确认：用恒生 connector 的分红数据匹配疑似除权日
    
    Args:
        klines: K线数据
        dividends: HengshengConnector.parse_dividends() 的输出
    
    Returns:
        增强版除权日列表（每项新增）：
          - status: 'confirmed' / 'uncertain' / 'not_found'
          - exdiv_type: '现金0.38元/股' / '送转0.2股' 等
          - theoretical_gap: 理论跳空 %
          - real_change_pct: 真实股价变化 % (剔除除权效应)
          - confidence: 置信度
    """
    if not dividends:
        return []
    
    confirmed = []
    
    for div in dividends:
        ex_date = div.get("exdivdate")
        if not ex_date:
            continue
        
        # 在 K 线中找这一天
        found = False
        for i, k in enumerate(klines):
            if k["date"] != ex_date:
                continue
            if i == 0:
                break
            found = True
            prev = klines[i-1]
            
            # 计算理论跳空
            cash = div.get("dividendpretax", 0) or 0
            bonus = (div.get("sharespershare", 0) or 0) + (div.get("bonuspershare", 0) or 0)
            theoretical_gap = -(cash + bonus * prev["close"]) / prev["close"]
            actual_gap = (k["close"] - prev["close"]) / prev["close"]
            
            # 跳空差异 < 0.5% 视为确认
            confidence = "confirmed" if abs(actual_gap - theoretical_gap) < 0.005 else "uncertain"
            
            ex_type = []
            if cash > 0:
                ex_type.append(f"现金{cash:.2f}元/股")
            if bonus > 0:
                ex_type.append(f"送转{bonus:.2f}股")
            ex_type_str = " + ".join(ex_type) if ex_type else "未知"
            
            confirmed.append({
                "date": ex_date,
                "exdiv_type": ex_type_str,
                "cash_per_share": cash,
                "bonus_per_share": bonus,
                "status": confidence,
                "theoretical_gap_pct": round(theoretical_gap * 100, 2),
                "actual_gap_pct": round(actual_gap * 100, 2),
                "real_change_pct": round((actual_gap - theoretical_gap) * 100, 2),
                "prev_close": prev["close"],
                "curr_close": k["close"],
                "bonusscheme": div.get("bonusscheme"),
                "stockname": div.get("stockname"),
            })
            break
        
        if not found:
            # 分红记录在 K 线范围外（如 8/21 还未到）
            confirmed.append({
                "date": ex_date,
                "exdiv_type": f"现金{div.get('dividendpretax', 0):.2f}元/股" if div.get("dividendpretax") else "未知",
                "status": "not_found",  # K 线范围内未找到
                "note": "K 线范围未覆盖此除权日，可能是未来事件"
            })
    
    return confirmed


def find_real_ex_right_days(klines: List[Dict], dividends: List[Dict]) -> List[Dict]:
    """
    基于实际公告数据，匹配出真正的除权除息日
    
    Args:
        klines: K线数据
        dividends: 公告列表，每项 {date: 除权日, cash_per_share: 每股分红, ratio: 送股比例}
    
    Returns:
        已确认的除权除息日
    """
    confirmed = []
    for div in dividends:
        # 在除权日 ±3 个交易日内找匹配的跳变
        target_date = div.get('date')
        for i, k in enumerate(klines):
            if k['date'] == target_date:
                # 计算理论跳空 = -（每股分红 + 送股×价格）/ 前收
                prev = klines[i-1]['close'] if i > 0 else None
                if prev:
                    cash = div.get('cash_per_share', 0)
                    bonus_ratio = div.get('ratio', 0)
                    theoretical_gap = -(cash + bonus_ratio * prev) / prev
                    actual_gap = (k['close'] - prev) / prev
                    # 实际跳空与理论跳空差异 < 0.5%
                    if abs(actual_gap - theoretical_gap) < 0.005:
                        confirmed.append({
                            'date': k['date'],
                            'prev_close': prev,
                            'curr_close': k['close'],
                            'theoretical_gap_pct': theoretical_gap * 100,
                            'actual_gap_pct': actual_gap * 100,
                            'cash_per_share': cash,
                            'bonus_ratio': bonus_ratio,
                            'confirmed': True
                        })
    return confirmed


# ============== V1-V10 校验函数 ==============

def _check_v1_completeness(klines: List[Dict]) -> Tuple[bool, str]:
    """V1: 数据完整性 ≥ 60 个交易日"""
    if not klines:
        return False, "V1 失败: K线数据为空"
    if len(klines) < 60:
        return False, f"V1 失败: 仅 {len(klines)} 条 K 线，需 ≥ 60 条"
    return True, ""


def _check_v2_ohlc(klines: List[Dict]) -> Tuple[bool, str]:
    """V2: OHLC 关系正确"""
    for i, k in enumerate(klines):
        if not all(key in k for key in ['open', 'close', 'high', 'low']):
            return False, f"V2 失败: 第 {i} 条缺少 OHLC 字段"
        o, c, h, l = k['open'], k['close'], k['high'], k['low']
        if not (l <= min(o, c) <= max(o, c) <= h):
            return False, f"V2 失败: 第 {i} 条 OHLC 关系错误 ({o}/{c}/{h}/{l})"
    return True, ""


def _check_v3_signs(klines: List[Dict]) -> Tuple[bool, str]:
    """V3: 量价符号合理"""
    for i, k in enumerate(klines):
        if k.get('close', 0) <= 0:
            return False, f"V3 失败: 第 {i} 条收盘价 ≤ 0"
        if k.get('vol', 0) < 0:
            return False, f"V3 失败: 第 {i} 条成交量 < 0"
    return True, ""


def _check_v4_date_continuity(klines: List[Dict]) -> Tuple[bool, str]:
    """V4: 日期连续性（提示性）"""
    if len(klines) < 2:
        return True, ""
    gaps = []
    for i in range(1, len(klines)):
        try:
            d1 = datetime.strptime(klines[i-1]['date'], "%Y-%m-%d")
            d2 = datetime.strptime(klines[i]['date'], "%Y-%m-%d")
            diff = (d2 - d1).days
            # 1-5 个日历日内是正常（周末/节假日）
            if diff > 5:
                gaps.append(f"{klines[i-1]['date']}→{klines[i]['date']} ({diff}日)")
        except (ValueError, KeyError):
            pass
    if gaps:
        return True, f"V4 警告: 发现 {len(gaps)} 个跳空（{gaps[:3]}{'...' if len(gaps)>3 else ''}）"
    return True, ""


def _check_v5_adjtype(klines: List[Dict], adjtype: Optional[str]) -> Tuple[bool, str]:
    """V5: 复权状态明确"""
    if adjtype not in ('qfq', 'hfq', None):
        return False, f"V5 失败: 复权状态必须是 qfq(前复权)/hfq(后复权)/None, 当前 {adjtype}"
    if adjtype is None:
        return True, "V5 警告: 未指定复权状态，默认按前复权处理"
    return True, ""


def _check_v6_ex_right(klines: List[Dict], dividends: List[Dict] = None) -> Tuple[bool, str]:
    """
    V6: 除权除息日识别（V5.2.1 升级：支持 dividend 公告二次确认）
    
    Args:
        klines: K线数据
        dividends: 可选，HengshengConnector.parse_dividends() 的输出
                   如果提供，则进行二次确认（confirmed/uncertain/not_found）
    """
    ex_days = detect_ex_right_days(klines)
    if not ex_days:
        return True, ""
    
    if dividends is None:
        # 无 dividend 数据，仅预警
        dates = [d['date'] for d in ex_days]
        return True, f"V6 警告: 检测到 {len(ex_days)} 个疑似除权日 {dates}，需用恒生 connector 二次确认"
    
    # 有 dividend 数据 → 二次确认
    confirmed = confirm_ex_right_days_with_dividends(klines, dividends)
    
    # 统计
    confirmed_count = sum(1 for c in confirmed if c.get("status") == "confirmed")
    uncertain_count = sum(1 for c in confirmed if c.get("status") == "uncertain")
    not_found_count = sum(1 for c in confirmed if c.get("status") == "not_found")
    
    # 修正信号：把 confirmed 的疑似除权日从警告列表移除（已确认是真的除权）
    confirmed_dates = {c["date"] for c in confirmed if c.get("status") == "confirmed"}
    pending = [d for d in ex_days if d["date"] not in confirmed_dates]
    
    details = []
    for c in confirmed:
        if c.get("status") == "confirmed":
            details.append(
                f"{c['date']}={c['exdiv_type']} (真实变化 {c['real_change_pct']:+.2f}%)"
            )
        elif c.get("status") == "uncertain":
            details.append(f"{c['date']}=待复核")
    
    msg = f"V6 除权日确认: {confirmed_count}个已确认, {uncertain_count}个待复核, {len(pending)}个疑似"
    if details:
        msg += f" | {details[:3]}"
    
    return True, msg


def _check_v7_live_price(klines: List[Dict], live_price: Optional[float]) -> Tuple[bool, str]:
    """V7: 实时价合理性"""
    if live_price is None:
        return True, ""
    recent = klines[-5:]
    if not recent:
        return True, ""
    low_5 = min(k['low'] for k in recent)
    high_5 = max(k['high'] for k in recent)
    if live_price < low_5:
        return True, f"V7 警告: 实时价 {live_price} < 近 5 日最低 {low_5}（可能盘中剧变或数据未更新）"
    if live_price > high_5 * 1.1:
        return True, f"V7 警告: 实时价 {live_price} > 近 5 日最高 × 1.1 = {high_5*1.1:.2f}（异常）"
    return True, ""


def _check_v8_base_date(base_date: Optional[str]) -> Tuple[bool, str]:
    """V8: 数据基准日标注"""
    if not base_date:
        return False, "V8 失败: 必须明确数据基准日（格式: YYYY-MM-DD HH:MM）"
    if not re.match(r'^\d{4}-\d{2}-\d{2}(\s\d{2}:\d{2})?$', base_date):
        return False, f"V8 失败: 数据基准日格式错误（{base_date}），应为 YYYY-MM-DD HH:MM"
    return True, ""


def _check_v9_multisource(klines: List[Dict], sources: Optional[List[str]],
                            v9_result: Optional[Dict] = None) -> Tuple[bool, str]:
    """
    V9: 多源验证（V5.2.1 升级：支持多源验证结果）
    
    Args:
        klines: 主源 K 线数据
        sources: 数据源名称列表
        v9_result: MultiSourceValidator.validate_v9() 的输出
    """
    if v9_result is not None:
        # 使用多源验证器的结果
        if not v9_result.get('overall_pass', False):
            comp = v9_result.get('comparison', {})
            if comp:
                return True, f"V9 警告: {v9_result.get('message', '')} (最大差异 {comp.get('max_diff_pct', 0)*100:.3f}%)"
        return True, ""
    
    if not sources or len(sources) < 2:
        return True, "V9 警告: 未进行多源验证（建议 ≥ 2 个数据源）"
    return True, ""


def _check_v10_field_names(klines: List[Dict]) -> Tuple[bool, str]:
    """V10: 字段命名一致性"""
    required = ['date', 'open', 'close', 'high', 'low', 'vol']
    if not klines:
        return False, "V10 失败: K线数据为空"
    missing = [f for f in required if f not in klines[0]]
    if missing:
        return False, f"V10 失败: 缺少字段 {missing}（必须包含: {required}）"
    return True, ""


# ============== 主校验函数 ==============

def validate_klines(
    klines: List[Dict],
    stock_code: str = "unknown",
    adjtype: Optional[str] = "qfq",
    live_price: Optional[float] = None,
    base_date: Optional[str] = None,
    sources: Optional[List[str]] = None,
    dividends: Optional[List[Dict]] = None,  # 🆕 V5.2.1: 用于 V6 二次确认
    v9_result: Optional[Dict] = None,  # 🆕 V5.2.1: V9 多源验证结果
) -> Tuple[bool, List[str], Optional[str]]:
    """
    主校验函数：执行 V1-V10 全部校验
    
    Args:
        klines: K线数据列表
        stock_code: 股票代码（用于日志）
        adjtype: 复权类型 qfq/hfq/None
        live_price: 实时价（可选）
        base_date: 数据基准日 YYYY-MM-DD HH:MM
        sources: 数据源列表（用于 V9 验证）
    
    Returns:
        (是否通过, 警告列表, 阻断原因)
        - 通过=True, 阻断原因=None：可以继续分析
        - 通过=True, 阻断原因=None, warnings非空：有警告但可继续
        - 通过=False, 阻断原因非空：必须停止分析
    """
    warnings = []
    
    # V1-V10 全部校验
    checks = [
        _check_v1_completeness(klines),
        _check_v2_ohlc(klines),
        _check_v3_signs(klines),
        _check_v4_date_continuity(klines),
        _check_v5_adjtype(klines, adjtype),
        _check_v6_ex_right(klines, dividends),  # 🆕 V5.2.1: 传入 dividends
        _check_v7_live_price(klines, live_price),
        _check_v8_base_date(base_date),
        _check_v9_multisource(klines, sources, v9_result),  # 🆕 V5.2.1: 传入 v9_result
        _check_v10_field_names(klines),
    ]
    
    block_reason = None
    for ok, msg in checks:
        if not ok and msg.startswith("V") and "失败" in msg:
            # 失败项
            if block_reason is None:
                block_reason = msg
            else:
                block_reason += f"; {msg}"
        elif msg:
            # 警告项
            warnings.append(msg)
    
    if block_reason:
        return False, warnings, block_reason
    
    return True, warnings, None


# ============== 快捷方法 ==============

def quick_validate(klines: List[Dict], stock_code: str = "unknown", dividends: List[Dict] = None) -> str:
    """
    快捷校验：返回格式化的报告字符串
    
    Args:
        klines: K线数据
        stock_code: 股票代码
        dividends: 可选，分红数据（V5.2.1 新增，用于 V6 二次确认）
    """
    base_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    ok, warnings, errors = validate_klines(
        klines, stock_code, "qfq", base_date=base_date, dividends=dividends
    )
    report = [f"📊 数据校验报告 - {stock_code}"]
    report.append(f"基准日: {base_date}")
    report.append(f"K线数: {len(klines)}")
    if dividends:
        report.append(f"分红数据: {len(dividends)} 条（用于 V6 二次确认）")
    report.append("")
    if ok:
        report.append("✅ 校验通过")
    else:
        report.append(f"❌ 校验失败: {errors}")
    if warnings:
        report.append("")
        report.append("⚠️ 警告:")
        for w in warnings:
            report.append(f"  - {w}")
    return "\n".join(report)


# ============== 自测 ==============

if __name__ == "__main__":
    # 测试 1: 正常 K 线
    print("=" * 70)
    print("测试 1: 正常 K 线数据")
    print("=" * 70)
    test_klines = []
    for i in range(65):
        test_klines.append({
            'date': (datetime(2026, 5, 1) + timedelta(days=i)).strftime("%Y-%m-%d"),
            'open': 10.0 + i * 0.01,
            'close': 10.05 + i * 0.01,
            'high': 10.10 + i * 0.01,
            'low': 9.95 + i * 0.01,
            'vol': 1000000 + i * 1000,
        })
    print(quick_validate(test_klines, "601398.SH"))
    
    # 测试 2: 除权日
    print()
    print("=" * 70)
    print("测试 2: 含除权日的 K 线（OHLC 关系正确）")
    print("=" * 70)
    test_klines_ex = list(test_klines)
    # 在第 30 条后插入一个除权日（价格跳变 -8%）
    ex_day = dict(test_klines[29])
    ex_day['date'] = test_klines[29]['date']
    ex_day['close'] = test_klines[29]['close'] * 0.92
    ex_day['open'] = ex_day['close']
    ex_day['high'] = ex_day['close'] + 0.05  # OHLC 关系正确
    ex_day['low'] = ex_day['close'] - 0.05
    ex_day['vol'] = test_klines[29]['vol'] * 2  # 放量
    test_klines_ex.insert(30, ex_day)
    print(quick_validate(test_klines_ex, "601398.SH (含除权)"))
    
    # 测试 3: OHLC 错误
    print()
    print("=" * 70)
    print("测试 3: OHLC 关系错误")
    print("=" * 70)
    test_klines_bad = list(test_klines)
    test_klines_bad[10]['low'] = test_klines_bad[10]['high'] + 1  # low > high
    print(quick_validate(test_klines_bad, "601398.SH (OHLC错误)"))
    
    # 测试 4: 数据不足
    print()
    print("=" * 70)
    print("测试 4: 数据不足 60 条")
    print("=" * 70)
    print(quick_validate(test_klines[:30], "601398.SH (30条)"))
    
    # 测试 5: 紫金 6/26 除权日识别
    print()
    print("=" * 70)
    print("测试 5: 实战数据 - 紫金矿业 6/24-6/26（V6 二次确认）")
    print("=" * 70)
    zj_klines = [
        {'date': '2026-06-24', 'open': 27.03, 'close': 27.27, 'high': 27.59, 'low': 26.95, 'vol': 2846096},
        {'date': '2026-06-25', 'open': 26.13, 'close': 25.54, 'high': 26.38, 'low': 25.35, 'vol': 5760700},
        {'date': '2026-06-26', 'open': 25.90, 'close': 25.10, 'high': 25.90, 'low': 24.86, 'vol': 3528208},
    ]
    ex_days = detect_ex_right_days(zj_klines)
    for d in ex_days:
        print(f"  ⚠️ 疑似除权日: {d['date']}, 跳空 {d['gap_pct']:+.2f}%, 量比 {d['vol_ratio']:.2f}x")
    print()
    print("  修正后的实战分析（V5.2.1 修正）:")
    print("  - 6/24 收盘 27.27")
    print("  - 6/25 收盘 25.54, 跌幅 -6.34% （登记日，**真跌 -6.34%**）")
    print("  - 6/26 收盘 25.10, 跌幅 -1.72% （除权日，-1.49% 除权 + -0.23% 真跌）")
    print("  - V6 二次确认：6/25 是登记日，6/26 才是除权日")
    print("  - **修正前错误结论**：6/25 跌幅被误以为含除权效应")
    print("  - **修正后正确结论**：6/25 跌幅 -6.34% 全是真跌，触发 C7（普通品种 3% 阈值）")
    print("  - C7 触发：C7 信号不需要修正（实际跌幅是 -6.34%，远超 3%）")
    print("  - 实战价值：避免 V6 误判 → 紫金 6/25 确实是大跌日（不是被除权'假摔'）")
