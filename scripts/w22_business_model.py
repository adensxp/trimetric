#!/usr/bin/env python3
"""
v5.3.30 W22 商业模式软指标

R097: ROA > 2× GDP 增长（= 10%）
- 模式：ROA = OperatingProfit / TotalAssets
- 5 年平均 > 10% → 通过

R098: 应付预收 - 应收预付 > 0
- 应付预收 (Payable + AdvanceReceipt) - 应收预付 (Receivable + AdvancePayment) > 0
- 商业地位硬指标
"""
import config
import sqlite3

DB_PATH = config.STOCK_DB

R097_THRESHOLD = 0.10  # ROA > 2 × GDP 增长（~5% × 2 = 10%）


def get_roa_score(code):
    """ROA 5 年均值（用 OperatingProfit / TotalAssets）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT year, operating_profit, total_assets FROM w20_8indicators
        WHERE stock_code = ? ORDER BY year
    ''', (code,))
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 3:
        return 0
    
    ratios = []
    for year, op_prof, total in rows:
        if op_prof and total and total > 0:
            ratios.append(op_prof / total)
    
    if not ratios:
        return 0
    
    avg = sum(ratios) / len(ratios)
    if avg > R097_THRESHOLD:
        return 1.0  # 完整加分
    elif avg > R097_THRESHOLD / 2:
        return 0.5  # 轻量加分
    return 0


def get_business_position_score(code):
    """应付预收 - 应收预付（如果数据可用）"""
    # 暂时基于现有字段估算
    conn = sqlite3.connect(DB_PATH)
    # 需要: AdvanceReceipt
    cur = conn.execute('''
        SELECT DISTINCT finstatementcode FROM financial_data
        WHERE finstatementcode LIKE '%Advance%' OR finstatementcode LIKE '%Prepay%'
    ''')
    rows = cur.fetchall()
    conn.close()
    return 0  # 暂未实现


def get_business_scores(code):
    return {
        'roa': {'name': 'ROA>10%', 'score': get_roa_score(code)},
        'business_position': {'name': '商业地位', 'score': get_business_position_score(code)},
    }


if __name__ == '__main__':
    for code in ['601100.SH', '600519.SH', '000333.SZ']:
        s = get_business_scores(code)
        total = sum(v['score'] for v in s.values())
        print(f"{code}: 总分 {total:.1f}")
        for k, v in s.items():
            if v['score'] > 0:
                print(f"  {k}: {v['name']} +{v['score']}")
