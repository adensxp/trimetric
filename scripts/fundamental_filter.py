#!/usr/bin/env python3
"""
v5.3.30 财务硬过滤评分工具
============================

基于微淼商学院"财务自由 A 股投资方法"8 个硬指标 + v5.3.30-LT 已有信号。

8 个硬指标（10 个评分维度）：
- ROE 5 年均值 > 15%（15 分）
- 营收增速 > 10%（10 分）
- 净现金含量 > 80%（15 分）
- 毛利率 > 40%（10 分）
- 资产负债率 < 60%（15 分）
- 分红比例 > 25%（10 分）
- 上市年限 > 3 年（5 分）
- 股息率 > 10Y 国债（10 分）
- 经营性现金流/净利润 > 0.8（10 分）
合计：100 分

评分等级（C 阈值：90/75/65）：
- 90+ → A 级（强烈推荐）
- 75-90 → B 级（可以建仓）
- 65-75 → C 级（观望）
- < 65 → D 级（淘汰）

用法：
  python3 fundamental_filter.py score 600519.SH
  python3 fundamental_filter.py batch
"""
import config
import sqlite3
import json
import sys
from datetime import datetime

DB = config.PORTFOLIO_DB

# 10 个评分维度配置
SCORE_RULES = {
    'ROE_5Y': {'weight': 15, 'threshold': 15.0, 'comparison': '>=', 'desc': 'ROE 5 年均值'},
    'REVENUE_GROWTH': {'weight': 10, 'threshold': 10.0, 'comparison': '>=', 'desc': '营收增速'},
    'CASH_CONTENT': {'weight': 15, 'threshold': 80.0, 'comparison': '>=', 'desc': '净现金含量'},
    'GROSS_MARGIN': {'weight': 10, 'threshold': 40.0, 'comparison': '>=', 'desc': '毛利率'},
    'DEBT_RATIO': {'weight': 15, 'threshold': 60.0, 'comparison': '<', 'desc': '资产负债率'},
    'DIVIDEND_RATIO': {'weight': 10, 'threshold': 25.0, 'comparison': '>=', 'desc': '分红比例'},
    'LISTING_YEARS': {'weight': 5, 'threshold': 3.0, 'comparison': '>=', 'desc': '上市年限'},
    'YIELD_VS_BOND': {'weight': 10, 'threshold': 1.0, 'comparison': '>=', 'desc': '股息率 > 10Y 国债'},
    'OCF_NETPROFIT': {'weight': 10, 'threshold': 0.8, 'comparison': '>=', 'desc': '经营现金流/净利润'},
}

# 评分等级（C 选项：90/75/65）
GRADES = {
    'A': 90,
    'B': 75,
    'C': 65,
    'D': 0,
}


def check_rule(value, threshold, comparison):
    """检查单个指标是否达标"""
    if value is None:
        return False
    if comparison == '>=':
        return value >= threshold
    elif comparison == '<':
        return value < threshold
    return False


def score_stock(fundamentals):
    """
    给单只股票打分
    fundamentals: dict of {rule_name: value} 例如：
    {
        'ROE_5Y': 25.0,
        'REVENUE_GROWTH': 15.0,
        ...
    }
    返回 dict: {total, grade, details, fail_rules}
    """
    total = 0
    details = []
    fail_rules = []

    for key, rule in SCORE_RULES.items():
        value = fundamentals.get(key)
        passed = check_rule(value, rule['threshold'], rule['comparison'])
        weight = rule['weight'] if passed else 0
        total += weight

        # 符号
        if rule['comparison'] == '>=':
            symbol = '≥'
        else:
            symbol = '<'

        if value is None:
            icon = '⚪'
            status = '无数据'
        elif passed:
            icon = '✅'
            status = f'+{weight}'
        else:
            icon = '❌'
            status = '+0'
            fail_rules.append(key)

        details.append({
            'rule': key,
            'desc': rule['desc'],
            'value': value,
            'threshold': rule['threshold'],
            'symbol': symbol,
            'passed': passed,
            'weight': rule['weight'],
            'score': weight,
            'icon': icon,
            'status': status,
        })

    # 等级
    if total >= GRADES['A']:
        grade = 'A'
    elif total >= GRADES['B']:
        grade = 'B'
    elif total >= GRADES['C']:
        grade = 'C'
    else:
        grade = 'D'

    return {
        'total': total,
        'grade': grade,
        'details': details,
        'fail_rules': fail_rules,
    }


def load_fundamentals_from_db(code):
    """从 stock_data.db 加载财务数据（长表结构）"""
    stock_db = config.STOCK_DB
    conn = sqlite3.connect(stock_db)
    conn.row_factory = sqlite3.Row

    # 长表结构：finstatementcode 区分指标
    # 常用指标 code：roe, revenue, ocf, gross_profit, total_liab, total_asset 等
    # 这里先取每个指标的最新值（5 年均值留 TODO）

    def get_indicator(code, indicator_code):
        cur = conn.execute("""
            SELECT financevalue, yoy FROM financial_data
            WHERE stock_code = ? AND finstatementcode = ?
            AND report_type = '年报'
            ORDER BY report_date DESC LIMIT 5
        """, (code, indicator_code))
        return [dict(r) for r in cur.fetchall()]

    # 5 年均值
    def get_5y_avg(code, indicator_code):
        rows = get_indicator(code, indicator_code)
        if not rows:
            return None
        values = [r['financevalue'] for r in rows if r['financevalue'] is not None]
        return sum(values) / len(values) if values else None

    # 最新值
    def get_latest(code, indicator_code):
        rows = get_indicator(code, indicator_code)
        return rows[0]['financevalue'] if rows else None

    # 季报 → 用 yoy
    def get_yoy(code, indicator_code):
        rows = get_indicator(code, indicator_code)
        return rows[0]['yoy'] if rows and rows[0]['yoy'] is not None else None

    # 用真实指标名
    roe_5y = get_5y_avg(code, 'WeightedROE') or get_5y_avg(code, 'ROE')
    revenue_growth = get_yoy(code, 'OperatingRevenue')
    # 净现金含量 = 经营性现金流 / 净利润
    ocf = get_latest(code, 'NetCashOperate')
    net_profit = get_latest(code, 'NetProfit')
    cash_content = (ocf / net_profit * 100) if (ocf and net_profit and net_profit > 0) else None
    # 毛利率 = 毛利 / 营收
    gross_profit = get_latest(code, 'OperateIncome')
    revenue = get_latest(code, 'OperatingRevenue')
    gross_margin = (gross_profit / revenue * 100) if (gross_profit and revenue and revenue > 0) else None
    # 资产负债率 = 总负债 / 总资产
    total_liab = get_latest(code, 'TotalLiability')
    total_asset = get_latest(code, 'TotalAsset')
    debt_ratio = (total_liab / total_asset * 100) if (total_liab and total_asset and total_asset > 0) else None
    # 分红比例（暂用 0，dividend 表需单独查）
    dividend_ratio = None
    # 经营现金流/净利润
    ocf_netprofit = (ocf / net_profit) if (ocf and net_profit and net_profit > 0) else None

    # 上市日期
    cur = conn.execute("SELECT list_date FROM stock_info WHERE stock_code = ?", (code,))
    info = cur.fetchone()
    listing_years = 0
    if info and info['list_date']:
        try:
            list_date = datetime.strptime(info['list_date'], '%Y-%m-%d')
            listing_years = (datetime.now() - list_date).days / 365.25
        except:
            pass

    # 股息率 vs 10Y 国债（暂用占位，dividend 数据需查 dividend 表）
    yield_vs_bond = 0  # TODO: 从 dividend 表查

    conn.close()

    return {
        'ROE_5Y': roe_5y,
        'REVENUE_GROWTH': revenue_growth,  # 实际是最近 yoy
        'CASH_CONTENT': cash_content,
        'GROSS_MARGIN': gross_margin,
        'DEBT_RATIO': debt_ratio,
        'DIVIDEND_RATIO': dividend_ratio,
        'LISTING_YEARS': listing_years,
        'YIELD_VS_BOND': yield_vs_bond,
        'OCF_NETPROFIT': ocf_netprofit,
    }


def demo_score(code):
    """演示用评分（无数据库时使用）"""
    demo_data = {
        '600519.SH': {  # 贵州茅台
            'ROE_5Y': 28.5, 'REVENUE_GROWTH': 18.2, 'CASH_CONTENT': 105.0,
            'GROSS_MARGIN': 91.5, 'DEBT_RATIO': 19.2, 'DIVIDEND_RATIO': 52.0,
            'LISTING_YEARS': 23.0, 'YIELD_VS_BOND': 0, 'OCF_NETPROFIT': 1.2,
        },
        '002475.SZ': {  # 立讯精密
            'ROE_5Y': 18.5, 'REVENUE_GROWTH': 22.0, 'CASH_CONTENT': 75.0,
            'GROSS_MARGIN': 18.0, 'DEBT_RATIO': 52.0, 'DIVIDEND_RATIO': 15.0,
            'LISTING_YEARS': 12.0, 'YIELD_VS_BOND': 0, 'OCF_NETPROFIT': 0.6,
        },
        '601328.SH': {  # 交通银行
            'ROE_5Y': 11.0, 'REVENUE_GROWTH': 5.0, 'CASH_CONTENT': None,
            'GROSS_MARGIN': None, 'DEBT_RATIO': 92.0, 'DIVIDEND_RATIO': 30.0,
            'LISTING_YEARS': 18.0, 'YIELD_VS_BOND': 1.0, 'OCF_NETPROFIT': None,
        },
    }
    return demo_data.get(code)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'score':
        if len(sys.argv) < 3:
            print("用法: python3 fundamental_filter.py score <code>")
            sys.exit(1)
        code = sys.argv[2]

        # 先尝试从数据库读，没数据用 demo
        fundamentals = load_fundamentals_from_db(code)
        if not fundamentals:
            print(f"⚠️  数据库无 {code} 财务数据，使用 demo 数据")
            fundamentals = demo_score(code)
        if not fundamentals:
            print(f"❌ 没有 {code} 的数据")
            sys.exit(1)

        result = score_stock(fundamentals)

        print(f"\n=== {code} 财务硬过滤评分 ===")
        for d in result['details']:
            val = d['value'] if d['value'] is not None else 'N/A'
            val_str = f"{val:.2f}" if isinstance(val, (int, float)) else val
            print(f"  {d['icon']} {d['desc']:20s} {val_str:>10} {d['symbol']} {d['threshold']:>6.2f}  {d['status']}")
        print("  " + "-" * 60)
        grade_icon = {'A': '⭐⭐⭐', 'B': '⭐⭐', 'C': '⭐', 'D': '❌'}.get(result['grade'], '')
        print(f"  总分: {result['total']}/100  {result['grade']} 级 {grade_icon}")
        if result['fail_rules']:
            print(f"  ❌ 不达标: {', '.join(result['fail_rules'])}")
        else:
            print(f"  ✅ 全部达标")
    elif cmd == 'batch':
        print("⚠️  batch 模式暂未实现，需要从 financial_data 批量读取")
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
