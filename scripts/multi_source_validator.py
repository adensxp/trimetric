#!/usr/bin/env python3
"""
V9 多源数据验证模块 V5.2.1
============================
基于《高量战法》SKILL.md 21.0.3 V9 规则

V9 规则：同一日 K 线从 2 个接口取值，差异 > 0.5% 报警
        优先用主流接口

支持数据源：
  1. 腾讯证券（web.ifzq.gtimg.cn） - HTTP 直接拉
  2. 恒生 StockMultiPeriodQuote - 通过 connector 工具

设计思路：数据处理层（与 hengsheng_connector 一致）
  - fetch_* 函数拉原始数据
  - parse_* 函数标准化
  - compare_two_sources 交叉对比
"""
import urllib.request
import json
import re
from typing import List, Dict, Optional
from datetime import datetime


class MultiSourceValidator:
    """V9 多源数据验证器 V5.2.1"""
    
    def __init__(self, threshold: float = 0.005):
        """
        Args:
            threshold: 价格差异阈值（默认 0.5% = 0.005）
        """
        self.threshold = threshold
        self.sources_used = []
    
    # ========================================================================
    # 1. 腾讯 K 线（HTTP 直接拉）
    # ========================================================================
    
    def fetch_tencent_klines(self, stock_code: str, count: int = 100) -> List[Dict]:
        """
        拉取腾讯 K 线数据（前复权）
        
        Args:
            stock_code: 6 位代码（如 "601398"）
            count: 拉取条数
        
        Returns:
            标准化 K 线列表
        """
        if stock_code.endswith(('.SH', '.SZ')):
            stock_code = stock_code.split('.')[0]
        
        if stock_code.startswith('6'):
            sec = 'sh' + stock_code
        else:
            sec = 'sz' + stock_code
        
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sec},day,,,{count},qfq"
        
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
            rows = data['data'][sec]['qfqday']
            
            klines = []
            for r in rows:
                klines.append({
                    'date': r[0],
                    'open': float(r[1]),
                    'close': float(r[2]),
                    'high': float(r[3]),
                    'low': float(r[4]),
                    'vol': float(r[5]),
                    'source': 'tencent',
                    'adjtype': 'qfq'
                })
            self.sources_used.append('tencent')
            return klines
        except Exception as e:
            print(f"  ❌ 腾讯拉取失败: {e}")
            return []
    
    # ========================================================================
    # 2. 恒生 K 线（处理 connector 返回的原始 JSON）
    # ========================================================================
    
    def parse_hengsheng_klines(self, raw_response: Dict) -> List[Dict]:
        """
        解析恒生 StockMultiPeriodQuote 返回的 K 线数据
        
        Args:
            raw_response: connector__hengsheng__call_api 返回的 dict
                         结构: {"data": {"columns": [...], "rows": [{...}, ...]}}
        
        Returns:
            标准化 K 线列表
        """
        if not raw_response or "data" not in raw_response:
            return []
        
        rows = raw_response["data"].get("rows", [])
        klines = []
        for r in rows:
            # 恒生成交量单位是"万股"，需转为"手"（×10000 ÷ 100 = ×100）
            vol_wan = self._to_float(r.get("turnovervolume"))  # 万股
            if vol_wan is not None:
                # 万股 → 手：1 万股 = 10000 股 = 100 手
                vol = vol_wan * 100
            else:
                vol = 0
            
            # 复权方式（restorationStatus="1"=前复权，"2"=后复权，"3"=不复权）
            status = r.get("restorationStatus", "1")
            adj_map = {"1": "qfq", "2": "hfq", "3": None, "前复权": "qfq", "后复权": "hfq", "不复权": None}
            adjtype = adj_map.get(status, "qfq")
            
            klines.append({
                'date': self._format_date(r.get("enddate")),
                'open': self._to_float(r.get("openprice")),
                'close': self._to_float(r.get("closeprice")),
                'high': self._to_float(r.get("highprice")),
                'low': self._to_float(r.get("lowprice")),
                'vol': vol,
                'amount_wan': self._to_float(r.get("turnovervalue")),  # 万元
                'turnoverrate': self._to_float(r.get("turnoverrate")),
                'source': 'hengsheng',
                'adjtype': adjtype
            })
        
        self.sources_used.append('hengsheng')
        return klines
    
    # ========================================================================
    # 3. 新浪 K 线（HTTP 直接拉，V5.2.1 V2.2 新增第三源）
    # ========================================================================
    
    def fetch_sina_klines(self, stock_code: str, datalen: int = 100) -> List[Dict]:
        """
        拉取新浪 K 线数据（**原始数据为不复权**）
        
        Args:
            stock_code: 6 位代码（如 "601398"）
            datalen: 拉取条数（默认 100）
        
        Returns:
            标准化 K 线列表（adjtype=None, 表示不复权）
        """
        if stock_code.endswith(('.SH', '.SZ')):
            stock_code = stock_code.split('.')[0]
        
        # 新浪代码格式：沪市 sh + code，深市 sz + code
        if stock_code.startswith('6'):
            symbol = 'sh' + stock_code
        else:
            symbol = 'sz' + stock_code
        
        # scale=240 表示日 K 线，ma=no 不带均线
        url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={datalen}"
        
        try:
            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://finance.sina.com.cn'
                }
            )
            data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode('utf-8'))
            
            klines = []
            for r in data:
                # 新浪 volume 是股数，需除以 100 转手
                vol_shares = self._to_float(r.get("volume", 0)) or 0
                vol = vol_shares / 100  # 股 → 手
                
                klines.append({
                    'date': r.get("day"),
                    'open': self._to_float(r.get("open")),
                    'close': self._to_float(r.get("close")),
                    'high': self._to_float(r.get("high")),
                    'low': self._to_float(r.get("low")),
                    'vol': vol,
                    'source': 'sina',
                    'adjtype': None  # 新浪原始数据 = 不复权
                })
            
            self.sources_used.append('sina')
            return klines
        except Exception as e:
            print(f"  ❌ 新浪拉取失败: {e}")
            return []
    
    def adjust_sina_to_qfq(
        self, 
        sina_klines: List[Dict], 
        dividends: List[Dict]
    ) -> List[Dict]:
        """
        对新浪 K 线做前复权（qfq）调整
        
        前复权规则：在除权日（已发生）之前的所有 K 线价格 -= 该次分红每股金额
        
        Args:
            sina_klines: 新浪 K 线（adjtype=None）
            dividends: 分红列表 [{'exdivdate': '2026-05-13', 'dividendpretax': 0.1689, ...}, ...]
        
        Returns:
            调整后的 K 线（adjtype='qfq'）
        """
        if not sina_klines or not dividends:
            return sina_klines
        
        # 找出新浪数据中**最新**的日期，作为"当前日期"
        latest_date = max(k['date'] for k in sina_klines)
        
        # 找出已发生的除权日（除权日 <= 最新日期）
        # 这些除权日的分红需要被前复权
        past_dividends = []
        for d in dividends:
            ex_date = d.get('exdivdate') or d.get('regdate')  # 兼容两种字段
            if ex_date and ex_date <= latest_date:
                past_dividends.append({
                    'ex_date': ex_date,
                    'div_per_share': d.get('dividendpretax', 0)
                })
        
        adjusted = []
        for k in sina_klines:
            k_adj = k.copy()
            k_adj['adjtype'] = 'qfq'
            
            # 找出在该日之前的所有已发生除权日
            cumulative_div = 0
            for pd in past_dividends:
                if k['date'] < pd['ex_date']:
                    cumulative_div += pd['div_per_share']
            
            if cumulative_div > 0:
                k_adj['open'] = k['open'] - cumulative_div
                k_adj['close'] = k['close'] - cumulative_div
                k_adj['high'] = k['high'] - cumulative_div
                k_adj['low'] = k['low'] - cumulative_div
                k_adj['adj_factor'] = cumulative_div
            else:
                k_adj['adj_factor'] = 0
            
            adjusted.append(k_adj)
        
        return adjusted
    
    # ========================================================================
    # 3. 多源对比（V9 核心）—— 支持 2 源或 3 源
    # ========================================================================
    
    def compare_two_sources(
        self,
        klines_a: List[Dict],
        klines_b: List[Dict],
        source_a_name: str = "tencent",
        source_b_name: str = "hengsheng"
    ) -> Dict:
        """
        V9 多源对比：同一日 close 价格对比（2 源）
        
        Args:
            klines_a: 数据源 A
            klines_b: 数据源 B
        
        Returns:
            {
                'compared_days': 比较天数,
                'matched_days': 一致天数,
                'diffs': [{'date', 'price_a', 'price_b', 'diff_pct', 'status'}],
                'max_diff_pct': 最大差异,
                'avg_diff_pct': 平均差异,
                'pass': 是否通过（所有差异 < threshold）
            }
        """
        # 建立日期 → K 线的索引
        idx_a = {k['date']: k for k in klines_a}
        idx_b = {k['date']: k for k in klines_b}
        
        common_dates = set(idx_a.keys()) & set(idx_b.keys())
        common_dates = sorted(common_dates)
        
        diffs = []
        for date in common_dates:
            price_a = idx_a[date].get('close', 0)
            price_b = idx_b[date].get('close', 0)
            
            if price_a <= 0 or price_b <= 0:
                continue
            
            # 差异 = |A - B| / A
            diff_pct = abs(price_a - price_b) / price_a
            status = 'pass' if diff_pct < self.threshold else 'fail'
            
            diffs.append({
                'date': date,
                'price_a': price_a,
                'price_b': price_b,
                'diff_pct': diff_pct,
                'diff_pct_str': f"{diff_pct*100:.3f}%",
                'status': status,
                'source_a': source_a_name,
                'source_b': source_b_name
            })
        
        if not diffs:
            return {
                'compared_days': 0,
                'matched_days': 0,
                'diffs': [],
                'max_diff_pct': 0,
                'avg_diff_pct': 0,
                'pass': False,
                'message': '无可比日期'
            }
        
        max_diff = max(d['diff_pct'] for d in diffs)
        avg_diff = sum(d['diff_pct'] for d in diffs) / len(diffs)
        passed = sum(1 for d in diffs if d['status'] == 'pass')
        
        return {
            'compared_days': len(diffs),
            'matched_days': passed,
            'failed_days': len(diffs) - passed,
            'diffs': diffs,
            'max_diff_pct': max_diff,
            'avg_diff_pct': avg_diff,
            'pass': passed == len(diffs),
            'threshold_pct': self.threshold * 100,
            'message': f"V9 {source_a_name} vs {source_b_name}: {len(diffs)}天对比，{passed}天一致，{len(diffs)-passed}天差异>{self.threshold*100:.1f}%"
        }
    
    def compare_three_sources(
        self,
        klines_dict: Dict[str, List[Dict]]
    ) -> Dict:
        """
        V9 3 源对比：两两对比，取最差结果
        
        Args:
            klines_dict: {'tencent': [...], 'hengsheng': [...], 'sina': [...]}
        
        Returns:
            {
                'pairwise': {'tencent_vs_hengsheng': ..., 'tencent_vs_sina': ..., 'hengsheng_vs_sina': ...},
                'overall_pass': 是否全部通过,
                'worst_pair': 最差的一对,
                'summary': '...'
            }
        """
        sources = list(klines_dict.keys())
        if len(sources) < 2:
            return {
                'pairwise': {},
                'overall_pass': False,
                'summary': f'需要 ≥ 2 个数据源，当前 {len(sources)} 个'
            }
        
        # 两两对比
        pairwise = {}
        for i in range(len(sources)):
            for j in range(i+1, len(sources)):
                a_name, b_name = sources[i], sources[j]
                a, b = klines_dict[a_name], klines_dict[b_name]
                pair_name = f"{a_name}_vs_{b_name}"
                pairwise[pair_name] = self.compare_two_sources(a, b, a_name, b_name)
        
        # 取最差
        worst_pair_name = max(pairwise.keys(), key=lambda k: pairwise[k]['max_diff_pct'])
        worst_pair = pairwise[worst_pair_name]
        overall_pass = all(p['pass'] for p in pairwise.values())
        
        # 总结
        summary_parts = []
        for pname, presult in pairwise.items():
            icon = "✅" if presult['pass'] else "❌"
            summary_parts.append(
                f"{icon} {pname}: {presult['compared_days']}天/{presult['matched_days']}一致, "
                f"最大差异 {presult['max_diff_pct']*100:.3f}%"
            )
        summary = " | ".join(summary_parts)
        
        return {
            'pairwise': pairwise,
            'overall_pass': overall_pass,
            'worst_pair': worst_pair_name,
            'worst_max_diff_pct': worst_pair['max_diff_pct'],
            'summary': summary,
            'sources_count': len(sources),
        }
    
    # ========================================================================
    # 4. 合并多源数据
    # ========================================================================
    
    def merge_sources(
        self,
        klines_list: List[List[Dict]],
        source_names: List[str] = None
    ) -> List[Dict]:
        """
        合并多源 K 线数据（用于决策卡/分析）
        
        策略：以第一个源为主，其他源做验证
        输出：每个交易日保留主源数据 + 校验结果
        """
        if not klines_list:
            return []
        
        if source_names is None:
            source_names = [f"src{i}" for i in range(len(klines_list))]
        
        main = klines_list[0]
        others = klines_list[1:]
        
        # 建立日期索引
        idx_list = [{k['date']: k for k in kls} for kls in klines_list]
        
        merged = []
        for k in main:
            entry = dict(k)
            entry['verify'] = {}
            
            for i, idx in enumerate(idx_list[1:], 1):
                date = k['date']
                if date in idx:
                    other_price = idx[date].get('close', 0)
                    main_price = k.get('close', 0)
                    if main_price > 0 and other_price > 0:
                        diff = abs(main_price - other_price) / main_price
                        entry['verify'][source_names[i]] = {
                            'close': other_price,
                            'diff_pct': diff,
                            'pass': diff < self.threshold
                        }
            
            # 整体验证：所有源都通过 = verified
            all_pass = all(v.get('pass', True) for v in entry['verify'].values())
            entry['multi_source_verified'] = all_pass
            merged.append(entry)
        
        return merged
    
    # ========================================================================
    # 5. V9 主验证函数
    # ========================================================================
    
    def validate_v9(
        self,
        tencent_klines: List[Dict] = None,
        hengsheng_klines: List[Dict] = None,
        sina_klines: List[Dict] = None,
        dividends: List[Dict] = None
    ) -> Dict:
        """
        V9 多源验证主函数（V5.2.1 V2.2 升级：支持 2-3 个源 + 新浪自动前复权）
        
        Args:
            tencent_klines: 腾讯 K 线（前复权）
            hengsheng_klines: 恒生 K 线（前复权）
            sina_klines: 新浪 K 线（原始 = 不复权，需用 dividends 调整）
            dividends: 分红列表（用于新浪做前复权）
        
        Returns:
            {
                'sources_count': 数据源数量,
                'comparison': 2 源对比结果（仅 2 源时）或 3 源 pairwise（≥3 源时）,
                'merge_result': 合并后的 K 线,
                'overall_pass': 是否通过 V9
            }
        """
        # 调整新浪为前复权
        if sina_klines and dividends:
            sina_klines = self.adjust_sina_to_qfq(sina_klines, dividends)
        
        sources = []
        if tencent_klines:
            sources.append(('tencent', tencent_klines))
        if hengsheng_klines:
            sources.append(('hengsheng', hengsheng_klines))
        if sina_klines:
            sources.append(('sina', sina_klines))
        
        sources_count = len(sources)
        
        if sources_count < 2:
            return {
                'sources_count': sources_count,
                'comparison': None,
                'merge_result': sources[0][1] if sources else [],
                'overall_pass': False,
                'message': f'V9: 仅 {sources_count} 个数据源，需要 ≥ 2 个'
            }
        
        if sources_count == 2:
            # 2 源对比
            comparison = self.compare_two_sources(
                sources[0][1], sources[1][1],
                sources[0][0], sources[1][0]
            )
            merge_result = self.merge_sources(
                [s[1] for s in sources],
                [s[0] for s in sources]
            )
            return {
                'sources_count': sources_count,
                'comparison': comparison,
                'merge_result': merge_result,
                'overall_pass': comparison['pass'],
                'message': comparison['message']
            }
        else:
            # 3+ 源对比：两两对比取最差
            klines_dict = dict(sources)
            three_result = self.compare_three_sources(klines_dict)
            merge_result = self.merge_sources(
                [s[1] for s in sources],
                [s[0] for s in sources]
            )
            return {
                'sources_count': sources_count,
                'comparison': three_result,
                'pairwise': three_result['pairwise'],
                'merge_result': merge_result,
                'overall_pass': three_result['overall_pass'],
                'worst_pair': three_result['worst_pair'],
                'worst_max_diff_pct': three_result['worst_max_diff_pct'],
                'message': three_result['summary']
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
            if len(value) == 8 and value.isdigit():
                return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
            if " " in value:
                return value.split(" ")[0]
            return value
        return str(value)


# ============================================================================
# 决策卡格式化器
# ============================================================================

class DecisionCardGenerator:
    """P16 决策卡自动生成器 V5.2.1"""
    
    @staticmethod
    def generate(
        stock_name: str,
        stock_code: str,
        base_date: str,
        risk_check: Dict,
        long_grade: int,
        long_detail: str,
        sector: str,
        vol_label: str,
        thresholds: Dict,
        window_periods: Dict,
        current_price: float,
        prev_close: float,
        high_60: float,
        low_60: float,
        high_60_date: str,
        low_60_date: str,
        support: float,
        resistance: float,
        current_hv_support: Optional[float] = None,
        current_hv_pressure: Optional[float] = None,
        signals: Dict = None,
        key_monitoring: List[str] = None,
        action: str = "观望",
        position: str = "0%",
        stop_loss: Optional[float] = None,
        target: Optional[float] = None
    ) -> str:
        """
        生成完整决策卡
        
        所有参数都已通过 p9_engine 算好，直接传入即可
        """
        if signals is None:
            signals = {}
        if key_monitoring is None:
            key_monitoring = []
        
        # 涨跌幅
        chg = (current_price - prev_close) / prev_close * 100 if prev_close else 0
        chg_str = f"{chg:+.2f}%"
        
        # 长线等级
        grade_str = {1: '一级（完全长线）', 2: '二级（边缘长线）', 3: '三级（非长线）'}.get(long_grade, '未知')
        
        # C1/B7/C7 阈值
        c1_str = f"{thresholds.get('C1', 0)*100:.1f}%"
        c7_str = f"{thresholds.get('C7', 0)*100:.1f}%"
        
        # 窗口期
        win_a = window_periods.get('A', 8)
        win_b = window_periods.get('B', 8)
        win_c = window_periods.get('C', 10)
        
        # 风险股状态
        risk_status = "✅ 通过" if risk_check.get('pass', True) else f"❌ {risk_check.get('reason', '阻断')}"
        
        # 信号状态
        sig_l = "触发" if signals.get('L') else "未触发"
        sig_b = "触发" if signals.get('B') else "未触发"
        sig_s = "触发" if signals.get('S') else "未触发"
        sig_c = "触发" if signals.get('C') else "未触发"
        
        # 关键监控
        monitor_str = "\n".join(f"  {i+1}. {m}" for i, m in enumerate(key_monitoring[:3])) if key_monitoring else "  （暂无）"
        
        # 止损/目标
        sl_str = f"{stop_loss:.2f}" if stop_loss else "-"
        tgt_str = f"{target:.2f}" if target else "-"
        
        # 高量位
        hv_sup = f"{current_hv_support:.3f}" if current_hv_support else "-"
        hv_res = f"{current_hv_pressure:.3f}" if current_hv_pressure else "-"
        
        # 60日高低
        h60_str = f"{high_60:.2f} ({high_60_date[5:]})" if high_60_date else f"{high_60:.2f}"
        l60_str = f"{low_60:.2f} ({low_60_date[5:]})" if low_60_date else f"{low_60:.2f}"
        
        # 自高/低跌幅
        from_high = (current_price - high_60) / high_60 * 100 if high_60 else 0
        from_low = (current_price - low_60) / low_60 * 100 if low_60 else 0
        
        card = f"""
┌──────────────────────────────────────────────────────────┐
│  🎯 高量战法决策卡 - {stock_name} ({stock_code})             │
│  数据基准: {base_date}                                       │
├──────────────────────────────────────────────────────────┤
│  风险股阻断: {risk_status}                                │
│  长线机构票: {grade_str}  板块: {sector}                 │
│  品种分类: {vol_label}  C1/C7 阈值: {c1_str} / {c7_str}   │
│  窗口期 A/B/C: {win_a}/{win_b}/{win_c} 日                   │
├──────────────────────────────────────────────────────────┤
│  当前价: {current_price:.2f}  昨收: {prev_close:.2f}  {chg_str:>7s} │
│  60日高/低: {h60_str} / {l60_str}                       │
│  自60日高: {from_high:+.2f}%   自60日低: {from_low:+.2f}%         │
│  关键支撑: {support:.2f}    关键压力: {resistance:.2f}        │
│  高量支撑: {hv_sup}    高量压力: {hv_res}                │
├──────────────────────────────────────────────────────────┤
│  信号状态:                                                 │
│    左侧建仓 L1-L4: {sig_l:<6s}  加仓 B1-B8: {sig_b:<6s}        │
│    减仓 S1-S8:     {sig_s:<6s}  清仓 C1-C7: {sig_c:<6s}        │
├──────────────────────────────────────────────────────────┤
│  关键监控点:                                                │
{monitor_str}
├──────────────────────────────────────────────────────────┤
│  ✅ 建议操作: {action}  仓位: {position}                  │
│  止损位: {sl_str}    目标位: {tgt_str}                        │
└──────────────────────────────────────────────────────────┘
"""
        return card


# ============================================================================
# 自测
# ============================================================================

def _selftest():
    """自测函数"""
    print("=" * 70)
    print("MultiSourceValidator + DecisionCard 自测 V5.2.1 V2.2")
    print("=" * 70)
    
    # 1. 测试腾讯拉取
    print("\n【测试 1: 腾讯 K 线拉取】")
    v = MultiSourceValidator()
    tencent = v.fetch_tencent_klines("601398", count=10)
    print(f"  拉取 {len(tencent)} 条")
    if tencent:
        print(f"  最新: {tencent[-1]}")
    
    # 2. 测试新浪拉取（V2.2 新增）
    print("\n【测试 2: 新浪 K 线拉取（V2.2 新增第三源）】")
    sina = v.fetch_sina_klines("601398", datalen=10)
    print(f"  拉取 {len(sina)} 条")
    if sina:
        print(f"  最新: {sina[-1]}")
    
    # 3. 测试恒生解析
    print("\n【测试 3: 恒生 K 线解析】")
    hengsheng_raw = {
        "data": {
            "rows": [
                {"enddate": "2026-08-13", "openprice": 7.48, "closeprice": 7.60, 
                 "highprice": 7.60, "lowprice": 7.46, "turnovervolume": 38895.48},
                {"enddate": "2026-08-12", "openprice": 7.57, "closeprice": 7.52,
                 "highprice": 7.59, "lowprice": 7.49, "turnovervolume": 31937.80},
            ]
        }
    }
    hengsheng = v.parse_hengsheng_klines(hengsheng_raw)
    print(f"  解析 {len(hengsheng)} 条")
    if hengsheng:
        print(f"  最新: {hengsheng[0]}")
    
    # 4. 测试 3 源对比
    print("\n【测试 4: V9 3 源对比（腾讯 + 恒生 + 新浪）】")
    tencent_full = v.fetch_tencent_klines("601398", count=5)
    sina_full = v.fetch_sina_klines("601398", datalen=5)
    hengsheng_raw2 = {
        "data": {"rows": [
            {"enddate": k['date'], "openprice": k['open'], "closeprice": k['close'] + 0.01,
             "highprice": k['high'], "lowprice": k['low'], "turnovervolume": 38895.48}
            for k in tencent_full if k['date'] in [s['date'] for s in sina_full]
        ]}
    }
    hengsheng_full = v.parse_hengsheng_klines(hengsheng_raw2)
    
    if tencent_full and sina_full and hengsheng_full:
        result = v.validate_v9(tencent_full, hengsheng_full, sina_full)
        print(f"  源数: {result['sources_count']}")
        print(f"  消息: {result['message']}")
        print(f"  通过: {result['overall_pass']}")
        if 'pairwise' in result:
            for pname, presult in result['pairwise'].items():
                print(f"  {pname}: {presult['compared_days']}天, 最大差异 {presult['max_diff_pct']*100:.3f}%")
    
    # 5. 测试决策卡
    print("\n【测试 5: 决策卡生成】")
    card = DecisionCardGenerator.generate(
        stock_name="工商银行",
        stock_code="601398.SH",
        base_date="2026-08-14 12:30",
        risk_check={'pass': True},
        long_grade=1,
        long_detail="一级（完全长线机构票）",
        sector="FINANCE",
        vol_label="低波动",
        thresholds={'C1': 0.015, 'C7': 0.02},
        window_periods={'A': 8, 'B': 8, 'C': 10},
        current_price=7.59,
        prev_close=7.60,
        high_60=8.16,
        low_60=6.95,
        high_60_date="2026-07-30",
        low_60_date="2026-07-06",
        support=7.49,
        resistance=7.99,
        current_hv_support=7.38,
        current_hv_pressure=7.50,
        signals={'L': False, 'B': False, 'S': False, 'C': False},
        key_monitoring=[
            "8/15 缩量小阴 → L1 触发验证",
            "8/14 -1.20% 已破 8/13 低点 7.49",
            "5日均量 302 万 vs 阈值 301 万（临界）"
        ],
        action="观望",
        position="0%",
        stop_loss=7.21,
        target=8.16
    )
    print(card)
    
    print("\n" + "=" * 70)
    print("✅ 自测完成")
    print("=" * 70)


if __name__ == "__main__":
    _selftest()
