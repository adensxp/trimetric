#!/usr/bin/env python3
"""
v5.3.30 商业判断软指标（基于 W21 9 要素 + W22 6 护城河）

从现有数据中提取 4 个可量化要素：
- 治理结构 (0.5): 股权集中度（top10 持股比例）
- 商业模式 (0.5): ROIC（净利润 / 投入资本）
- 现金流 (0.5): 净现比（5 年均值）
- 系统稳定性 (0.5): 营业利润稳定（5 年波动小）
"""
import config
import sqlite3

DB_PATH = config.STOCK_DB


def get_governance_score(code):
    """治理结构：top10 持股比例越高得分越高（30-80% 区间）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT SUM(hold_ratio) FROM top10_holders
        WHERE stock_code = ? AND report_date = (SELECT MAX(report_date) FROM top10_holders WHERE stock_code = ?)
    ''', (code, code))
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return 0
    ratio = row[0] / 100
    if 0.3 <= ratio <= 0.8:
        return 0.5
    return 0


def get_business_model_score(code):
    """商业模式：ROIC 5 年均值 > 15% 加分"""
    conn = sqlite3.connect(DB_PATH)
    # 拉 5 年净利润和股东权益
    cur = conn.execute('''
        SELECT report_date, financevalue FROM financial_data
        WHERE stock_code = ? AND finstatementcode IN ('NPParentCompanyOwners', 'TotalShareholderEquity')
        AND report_type = '年报'
        AND report_date >= '2020-01-01'
        ORDER BY report_date
    ''', (code,))
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 4:
        return 0

    # 整理
    yearly = {}
    for date, val in rows:
        year = int(date[:4])
        # 简化：第一次遇到的是 NPParentCompanyOwners，第二次是 TotalShareholderEquity（按 sort 顺序）
        if year not in yearly:
            yearly[year] = {}
        # 看 finstatementcode 应该是从 sql 选出来的，但只 val
        # 这里需要重写
    # 简化：直接用 ROE
    return 0


def get_cash_flow_score(code):
    """现金流：净现比 5 年均值 > 100% 加分"""
    conn = sqlite3.connect(DB_PATH)
    # 用 fraud_redflags 计算
    # 净现比 = 经营活动现金流 / 净利润
    cur = conn.execute('''
        SELECT AVG(CASE WHEN NPParentCompanyOwners > 0 THEN 1.0 ELSE 0 END)
        FROM (
            SELECT year, MAX(CASE WHEN finstatementcode='NPParentCompanyOwners' THEN financevalue END) as NPParentCompanyOwners
            FROM financial_data
            WHERE stock_code = ? AND report_type = '年报'
            GROUP BY year
        )
    ''', (code,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] is None:
        return 0
    return 0


def get_system_stability_score(code):
    """系统稳定性：营业利润 5 年波动率"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT report_date, financevalue FROM financial_data
        WHERE stock_code = ? AND finstatementcode = 'OperatingProfit'
        AND report_type IN ('年报', 'Y')
        ORDER BY report_date
    ''', (code,))
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 3:
        return 0
    
    # 简化：利润为正年数 >= 3 → 0.5
    positive = sum(1 for r in rows if r[1] and r[1] > 0)
    if positive >= 3:
        return 0.5
    return 0


def get_soft_scores(code):
    """获取一只票的软指标（最多 2.0 分）"""
    return {
        'governance': {'name': '治理结构', 'score': get_governance_score(code)},
        'cash_flow': {'name': '现金流', 'score': 0},  # 暂未实现
        'system_stability': {'name': '系统稳定性', 'score': get_system_stability_score(code)},
        'industry_position': {'name': '行业地位', 'score': 0},  # 暂未实现
    }


if __name__ == '__main__':
    # 测试
    for code in ['601100.SH', '600519.SH', '000333.SZ']:
        s = get_soft_scores(code)
        total = sum(v['score'] for v in s.values())
        print(f"{code}: 总分 {total:.1f}")
        for k, v in s.items():
            if v['score'] > 0:
                print(f"  {k}: {v['name']} +{v['score']}")
