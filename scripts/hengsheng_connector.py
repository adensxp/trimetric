#!/usr/bin/env python3
"""
恒生金融数据库 Connector 封装 V5.2.1
====================================
基于 MiniMax Finance MCP 的恒生金融数据接口

设计思路：connector 工具是 MCP 服务，不能直接 Python import。
本模块提供"数据处理层"，接受原始 JSON 输入，输出结构化数据。
Agent 工作流：
  1. 用 connector__hengsheng__call_api 拉取原始数据
  2. 把 JSON 字符串传给 HengshengConnector 处理
  3. 获得标准化的 Python dict 输出

提供 3 个核心功能：
  1. BonusStock - 个股分红（用于 V6 二次确认）
  2. StockValueAnalysis - 价值分析（用于 P19 PE 分位）
  3. AShareAnnouncement - A股公告（用于 P17 公告事件）

使用：
  from hengsheng_connector import HengshengConnector
  
  # 方式 1: 直接处理原始 JSON 字符串
  import json
  raw = json.loads(connector_response)
  conn = HengshengConnector()
  divs = conn.parse_dividends(raw)
  
  # 方式 2: 综合 V6/P17/P19
  confirmed = conn.confirm_ex_right_days_from_raw(klines, divs_raw, ...)
"""
import json
from typing import List, Dict, Optional
from datetime import datetime, timedelta


class HengshengConnector:
    """恒生金融数据库连接器 V5.2.1（数据处理层）"""
    
    def __init__(self):
        self.version = "V5.2.1"
    
    # ========================================================================
    # 1. BonusStock - 个股分红
    # ========================================================================
    
    def parse_dividends(self, raw_response: Dict) -> List[Dict]:
        """
        解析分红数据原始响应
        
        Args:
            raw_response: connector__hengsheng__call_api 返回的 dict
                         结构: {"data": {"columns": [...], "rows": [{...}, ...]}}
        
        Returns:
            标准化的分红记录列表：
              - exdivdate: 除权除息日 (YYYY-MM-DD)
              - regdate: 股权登记日
              - paydate: 派息日
              - dividendpretax: 每股股利(税前)
              - dividendaftertax: 每股股利(税后)
              - bonusscheme: 分红方案
              - sharespershare: 每股送股比例
              - bonuspershare: 每股转增股比例
              - enddate: 分红年度
        """
        if not raw_response or "data" not in raw_response:
            return []
        
        rows = raw_response["data"].get("rows", [])
        return [self._normalize_dividend(r) for r in rows]
    
    def _normalize_dividend(self, row: Dict) -> Dict:
        """标准化分红数据"""
        return {
            "exdivdate": self._format_date(row.get("exdivdate")),
            "regdate": self._format_date(row.get("regdate")),
            "paydate": self._format_date(row.get("paydate")),
            "dividendpretax": self._to_float(row.get("dividendpretax")),
            "dividendaftertax": self._to_float(row.get("dividendaftertax")),
            "bonusscheme": row.get("bonusscheme"),
            "sharespershare": self._to_float(row.get("sharespershare")),
            "bonuspershare": self._to_float(row.get("bonuspershare")),
            "enddate": row.get("enddate"),
            "stockname": row.get("stockname"),
        }
    
    # ========================================================================
    # 2. StockValueAnalysis - 价值分析（PE/PB/股息率）
    # ========================================================================
    
    def parse_value_analysis(self, raw_response: Dict) -> List[Dict]:
        """
        解析价值分析数据原始响应
        
        Returns:
            每日数据列表：
              - tradingday: 交易日 (YYYY-MM-DD)
              - pe: PE-TTM
              - pelyr: PE-LYR（静态）
              - pb: PB-MRQ
              - pblf: PB-LF
              - dividendratio: 滚动股息率
              - dividendratiolyr: 股息率（LYR）
              - peg: PEG
              - totalmv: 总市值（亿元）
              - negotiablemv: 流通市值（亿元）
        """
        if not raw_response or "data" not in raw_response:
            return []
        
        rows = raw_response["data"].get("rows", [])
        return [self._normalize_value(r) for r in rows]
    
    def compute_pe_quantile(self, values: List[Dict], days: int = 250) -> Dict:
        """
        P19 PE 历史分位计算
        
        Args:
            values: parse_value_analysis 的输出
            days: 历史回溯天数（默认 250 ≈ 1 年交易日）
        
        Returns:
            {
                'current_pe': 当前 PE-TTM,
                'current_pb': 当前 PB,
                'current_div_yield': 当前股息率,
                'pe_min': 期间最小 PE,
                'pe_max': 期间最大 PE,
                'pe_mean': 期间平均 PE,
                'pe_median': 期间中位数,
                'quantile_pct': 当前 PE 在历史中的分位 (0-100),
                'data_days': 实际数据天数,
                'period': 起止日期
            }
        """
        if not values:
            return {"error": "无数据", "data_days": 0}
        
        # 按交易日期排序
        values = sorted(values, key=lambda x: x.get("tradingday", ""))
        # 取最近 days 天
        recent = values[-days:] if len(values) > days else values
        
        pe_values = [v["pe"] for v in recent if v["pe"] is not None and v["pe"] > 0]
        pb_values = [v["pb"] for v in recent if v["pb"] is not None and v["pb"] > 0]
        div_values = [v["dividendratio"] for v in recent if v["dividendratio"] is not None]
        
        if not pe_values:
            return {"error": "PE 数据为空", "data_days": len(recent)}
        
        current = recent[-1]
        current_pe = current["pe"]
        # 历史分位 = (比当前 PE 小的天数 / 总天数) × 100
        quantile_pct = sum(1 for pe in pe_values if pe <= current_pe) / len(pe_values) * 100
        
        return {
            "stockname": current.get("stockname", ""),
            "current_pe": current_pe,
            "current_pb": current.get("pb"),
            "current_div_yield": current.get("dividendratio"),
            "pe_min": round(min(pe_values), 2),
            "pe_max": round(max(pe_values), 2),
            "pe_mean": round(sum(pe_values) / len(pe_values), 2),
            "pe_median": round(sorted(pe_values)[len(pe_values) // 2], 2),
            "quantile_pct": round(quantile_pct, 1),
            "data_days": len(pe_values),
            "period": f"{recent[0].get('tradingday', '')} ~ {recent[-1].get('tradingday', '')}",
        }
    
    def _normalize_value(self, row: Dict) -> Dict:
        """标准化价值分析数据"""
        return {
            "tradingday": self._format_date(row.get("tradingday")),
            "pe": self._to_float(row.get("pe")),
            "pelyr": self._to_float(row.get("pelyr")),
            "pb": self._to_float(row.get("pb")),
            "pblf": self._to_float(row.get("pblf")),
            "dividendratio": self._to_float(row.get("dividendratio")),
            "dividendratiolyr": self._to_float(row.get("dividendratiolyr")),
            "peg": self._to_float(row.get("peg")),
            "totalmv": self._to_float(row.get("totalmv")),
            "negotiablemv": self._to_float(row.get("negotiablemv")),
            "stockname": row.get("stockname"),
        }
    
    # ========================================================================
    # 3. AShareAnnouncement - A股公告
    # ========================================================================
    
    def parse_announcements(self, raw_response: Dict) -> List[Dict]:
        """
        解析公告数据原始响应
        
        Returns:
            公告列表：
              - publishDate: 公告日期
              - title: 公告标题
              - category1: 一级分类
              - category2: 二级分类
              - url: 公告 PDF 地址
              - id: 公告 ID
              - stockname: 股票简称
        """
        if not raw_response or "data" not in raw_response:
            return []
        
        rows = raw_response["data"].get("rows", [])
        return [self._normalize_announcement(r) for r in rows]
    
    def filter_important_events(self, announcements: List[Dict]) -> List[Dict]:
        """
        P17 重要事件筛选（只保留影响交易的公告）
        
        重要事件分类：
          - 业绩预告/预增/预减
          - 利润分配/分红
          - 重大资产重组/收购/合并
          - 股东减持/增持
          - 限售解禁
          - 增发/配股
          - 风险提示/停牌
        
        Returns:
            重要事件列表（每项多一个 event_type 字段）
        """
        keywords = {
            "业绩预告": ["业绩预增", "业绩预减", "业绩预告", "业绩快报", "业绩预盈", "业绩预亏"],
            "分红": ["利润分配", "分红实施", "分红派息", "权益分派", "中期分红"],
            "重组并购": ["资产重组", "并购重组", "重组", "收购", "合并", "要约收购"],
            "股东动作": ["减持", "增持", "回购"],
            "解禁": ["限售解禁", "解禁"],
            "再融资": ["增发", "配股", "可转债", "定向增发"],
            "重大风险": ["风险提示", "停牌", "退市", "ST", "诉讼", "仲裁", "处罚"],
        }
        
        important = []
        for ann in announcements:
            title = ann.get("title", "") or ""
            for event_type, kws in keywords.items():
                if any(kw in title for kw in kws):
                    ann_copy = dict(ann)
                    ann_copy["event_type"] = event_type
                    important.append(ann_copy)
                    break
        
        return important
    
    def _normalize_announcement(self, row: Dict) -> Dict:
        """标准化公告数据"""
        return {
            "publishDate": self._format_date(row.get("publishDate")),
            "title": row.get("title") or row.get("sourceTitle"),
            "category1": row.get("firstCategoryName"),
            "category2": row.get("secondCategoryName"),
            "url": row.get("sourceAddress"),
            "id": row.get("id"),
            "stockname": row.get("stockName"),
        }
    
    # ========================================================================
    # 4. 综合：V6 除权日二次确认
    # ========================================================================
    
    def confirm_ex_right_days(self, klines: List[Dict], 
                               dividends: List[Dict],
                               is_forward_adjusted: bool = True) -> List[Dict]:
        """
        V6 除权日二次确认（V5.2.1 修正：支持前复权数据）
        
        Args:
            klines: K线列表
            dividends: parse_dividends 的输出
            is_forward_adjusted: 是否前复权数据（默认 True，对应腾讯 K 线）
        
        Returns:
            已确认的除权日列表，每项：
              - date: 确认除权日
              - exdiv_type: 类型描述
              - cash_per_share: 每股现金分红
              - bonus_per_share: 每股送股
              - confidence: 置信度 (confirmed/uncertain/not_found)
              - theoretical_gap_pct: 理论跳空 (%)
              - actual_gap_pct: 实际跳空 (%)
              - real_change_pct: 真实股价变化 (%)
                - **前复权**：= actual_gap（已扣除除权效应）
                - **不复权**：= actual_gap - theoretical_gap
        """
        confirmed = []
        
        for div in dividends:
            ex_date = div.get("exdivdate")
            if not ex_date:
                continue
            
            # 找对应 K 线
            found = False
            for i, k in enumerate(klines):
                if k["date"] != ex_date:
                    continue
                if i == 0:
                    break
                found = True
                prev = klines[i-1]
                
                cash = div.get("dividendpretax", 0) or 0
                bonus = (div.get("sharespershare", 0) or 0) + (div.get("bonuspershare", 0) or 0)
                theoretical_gap = -(cash + bonus * prev["close"]) / prev["close"]
                actual_gap = (k["close"] - prev["close"]) / prev["close"]
                
                # ⚠️ 关键修正：前复权 vs 不复权
                if is_forward_adjusted:
                    # 前复权：实际跳空已扣除除权效应 = 真实变化
                    real_change_pct = actual_gap * 100
                    # 置信度：除权日实际波动应该小（前复权已平滑）
                    confidence = "confirmed" if abs(actual_gap) < 0.05 else "uncertain"
                else:
                    # 不复权：实际跳空 = 除权效应 + 真实变化
                    real_change_pct = (actual_gap - theoretical_gap) * 100
                    confidence = "confirmed" if abs(actual_gap - theoretical_gap) < 0.005 else "uncertain"
                
                # 识别分红类型
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
                    "confidence": confidence,
                    "is_forward_adjusted": is_forward_adjusted,
                    "theoretical_gap_pct": round(theoretical_gap * 100, 2),
                    "actual_gap_pct": round(actual_gap * 100, 2),
                    "real_change_pct": round(real_change_pct, 2),
                    "prev_close": prev["close"],
                    "curr_close": k["close"],
                    "bonusscheme": div.get("bonusscheme"),
                })
                break
            
            if not found:
                # 分红记录在 K 线范围外（如 8/21 还未到）
                confirmed.append({
                    "date": ex_date,
                    "exdiv_type": f"现金{div.get('dividendpretax', 0):.2f}元/股" if div.get("dividendpretax") else "未知",
                    "confidence": "not_found",
                    "note": "K 线范围未覆盖此除权日，可能是未来事件"
                })
        
        return confirmed
    
    # ========================================================================
    # 5. P19 双引擎自动验证
    # ========================================================================
    
    def check_double_engine(self, pe_quantile: Dict, div_yield: Optional[float] = None) -> Dict:
        """
        P19 双引擎自动验证
        
        Args:
            pe_quantile: compute_pe_quantile 输出
            div_yield: 股息率（如果为 None，使用 pe_quantile 中的）
        
        Returns:
            {
                'engine1_pe': PE 历史分位判定,
                'engine2_div': 股息率判定,
                'overall_level': 总体档位 (0/1/2),
                'actions': ['提升一档', '提升两档', '标准', '降一档']
            }
        """
        if not pe_quantile or "error" in pe_quantile:
            return {"error": "无 PE 数据"}
        
        # Engine 1: PE 历史分位
        quantile = pe_quantile.get("quantile_pct", 50)
        if quantile < 10:
            engine1 = {"status": "极低", "score": 2, "desc": f"PE 分位 {quantile}% < 10%"}
        elif quantile < 30:
            engine1 = {"status": "低", "score": 1, "desc": f"PE 分位 {quantile}% < 30%"}
        elif quantile < 70:
            engine1 = {"status": "中", "score": 0, "desc": f"PE 分位 {quantile}% (30-70%)"}
        elif quantile < 90:
            engine1 = {"status": "高", "score": -1, "desc": f"PE 分位 {quantile}% (70-90%)"}
        else:
            engine1 = {"status": "极高", "score": -2, "desc": f"PE 分位 {quantile}% > 90%"}
        
        # Engine 2: 股息率
        if div_yield is None:
            div_yield = pe_quantile.get("current_div_yield", 0)
        
        if div_yield > 5:
            engine2 = {"status": "极高", "score": 2, "desc": f"股息率 {div_yield:.2f}% > 5%"}
        elif div_yield > 3:
            engine2 = {"status": "高", "score": 1, "desc": f"股息率 {div_yield:.2f}% > 3%"}
        elif div_yield > 1.5:
            engine2 = {"status": "中", "score": 0, "desc": f"股息率 {div_yield:.2f}% (1.5-3%)"}
        else:
            engine2 = {"status": "低", "score": -1, "desc": f"股息率 {div_yield:.2f}% < 1.5%"}
        
        # 综合档位
        total_score = engine1["score"] + engine2["score"]
        if total_score >= 3:
            level = 2
            actions = ["提升两档"]
        elif total_score >= 1:
            level = 1
            actions = ["提升一档"]
        elif total_score >= -1:
            level = 0
            actions = ["标准"]
        else:
            level = -1
            actions = ["降一档"]
        
        return {
            "engine1_pe": engine1,
            "engine2_div": engine2,
            "total_score": total_score,
            "overall_level": level,
            "actions": actions,
        }
    
    # ========================================================================
    # 辅助方法
    # ========================================================================
    
    @staticmethod
    def _to_float(value) -> Optional[float]:
        """安全转为 float"""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def _format_date(value) -> Optional[str]:
        """格式化日期为 YYYY-MM-DD"""
        if not value:
            return None
        if isinstance(value, str):
            # 可能是 "2026-08-13" 或 "2026-08-13 12:34:56" 或 "20260813"
            if len(value) == 8 and value.isdigit():
                return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
            if " " in value:
                return value.split(" ")[0]
            return value
        return str(value)


# ============================================================================
# 自测（用内置示例数据）
# ============================================================================

def _selftest():
    """自测函数"""
    print("=" * 70)
    print("HengshengConnector 自测 V5.2.1")
    print("=" * 70)
    
    conn = HengshengConnector()
    
    # 1. 测试 parse_dividends
    print("\n【测试 1: parse_dividends - 紫金矿业 2026 年中期分红】")
    divs_raw = {
        "data": {
            "rows": [{
                "exdivdate": "2026-08-21",
                "regdate": "2026-08-20",
                "paydate": "2026-08-21",
                "dividendpretax": 0.42,
                "dividendaftertax": 0.42,
                "bonusscheme": "10派4.2元(含税)",
                "sharespershare": None,
                "bonuspershare": None,
                "enddate": "2026",
                "stockname": "紫金矿业"
            }]
        }
    }
    divs = conn.parse_dividends(divs_raw)
    for d in divs:
        print(f"  除权日: {d['exdivdate']}, 每股股利: {d['dividendpretax']}元, 方案: {d['bonusscheme']}")
    
    # 2. 测试 parse_value_analysis + compute_pe_quantile
    print("\n【测试 2: compute_pe_quantile - 紫金矿业 250 日 PE 分位】")
    values_raw = {
        "data": {
            "rows": [
                {"tradingday": f"2026-{m:02d}-{d:02d}", "pe": 13 + (i % 30) * 0.3,
                 "pb": 4.5, "dividendratio": 1.85, "stockname": "紫金矿业"}
                for i, (m, d) in enumerate([
                    (1, 5), (1, 15), (2, 1), (2, 15), (3, 1), (3, 15), (4, 1), (4, 15),
                    (5, 1), (5, 15), (6, 1), (6, 15), (7, 1), (7, 15), (8, 1), (8, 13)
                ])
            ]
        }
    }
    values = conn.parse_value_analysis(values_raw)
    pe_q = conn.compute_pe_quantile(values, days=250)
    if "error" not in pe_q:
        print(f"  当前 PE: {pe_q['current_pe']:.2f}")
        print(f"  历史分位: {pe_q['quantile_pct']:.1f}%")
        print(f"  PE 区间: [{pe_q['pe_min']:.2f}, {pe_q['pe_max']:.2f}]")
    
    # 3. 测试 parse_announcements + filter_important_events
    print("\n【测试 3: filter_important_events - 紫金矿业 7-8 月公告】")
    ann_raw = {
        "data": {
            "rows": [
                {"publishDate": "2026-08-13", "title": "紫金矿业:2026年中期权益分派实施公告", "firstCategoryName": "重大事项"},
                {"publishDate": "2026-07-30", "title": "紫金矿业:关于控股子公司终止收购Allied Gold并认购其9.2%股权的公告", "firstCategoryName": "重大事项"},
                {"publishDate": "2026-07-10", "title": "紫金矿业:2026年半年度业绩预增公告", "firstCategoryName": "财务报告"},
                {"publishDate": "2026-07-11", "title": "紫金矿业:2026年中期利润分配方案公告", "firstCategoryName": "重大事项"},
                {"publishDate": "2026-07-02", "title": "紫金矿业:H股市场公告", "firstCategoryName": "一般公告"},
            ]
        }
    }
    anns = conn.parse_announcements(ann_raw)
    important = conn.filter_important_events(anns)
    for e in important:
        print(f"  {e['publishDate']} [{e['event_type']}] {e['title'][:50]}")
    
    # 4. 测试 V6 二次确认
    print("\n【测试 4: V6 二次确认 - 紫金 6/25 真假除权】")
    klines = [
        {'date': '2026-06-24', 'close': 27.27, 'open': 27.03, 'high': 27.59, 'low': 26.95, 'vol': 2846096},
        {'date': '2026-06-25', 'close': 25.54, 'open': 26.13, 'high': 26.38, 'low': 25.35, 'vol': 5760700},
        {'date': '2026-06-26', 'close': 25.10, 'open': 25.90, 'high': 25.90, 'low': 24.86, 'vol': 3528208},
    ]
    # 紫金 2025 年度分红 10派3.8元
    div_2025_raw = {
        "data": {
            "rows": [{
                "exdivdate": "2026-06-26",
                "regdate": "2026-06-25",
                "paydate": "2026-06-26",
                "dividendpretax": 0.38,
                "dividendaftertax": 0.38,
                "bonusscheme": "10派3.8元(含税)",
                "sharespershare": None,
                "bonuspershare": None,
                "enddate": "2025",
                "stockname": "紫金矿业"
            }]
        }
    }
    divs_2025 = conn.parse_dividends(div_2025_raw)
    confirmed = conn.confirm_ex_right_days(klines, divs_2025)
    for c in confirmed:
        print(f"  {c['date']} {c['exdiv_type']}")
        print(f"    理论跳空: {c['theoretical_gap_pct']:+.2f}%")
        print(f"    实际跳空: {c['actual_gap_pct']:+.2f}%")
        print(f"    真实变化: {c['real_change_pct']:+.2f}% (除权效应外)")
        print(f"    置信度: {c['confidence']}")
    
    # 5. 测试 P19 双引擎
    print("\n【测试 5: P19 双引擎自动验证】")
    de = conn.check_double_engine(pe_q, div_yield=1.85)
    print(f"  Engine 1 (PE 分位): {de['engine1_pe']['status']} - {de['engine1_pe']['desc']}")
    print(f"  Engine 2 (股息率): {de['engine2_div']['status']} - {de['engine2_div']['desc']}")
    print(f"  总分: {de['total_score']}, 档位: {de['overall_level']}, 动作: {de['actions']}")
    
    print("\n" + "=" * 70)
    print("✅ 自测完成")
    print("=" * 70)


if __name__ == "__main__":
    _selftest()
