#!/usr/bin/env python3
"""
W19 18 步财报分析（微淼方法论）宽松版
基于已有财务数据做 18 步分析，缺数据的步骤用 None。
"""
import config
import sqlite3
import os

DB_PATH = config.STOCK_DB

def get_db():
    conn = sqlite3.connect(DB_PATH)
    return conn


# ==================== 18 步核心检查 ====================

def get_step_results_batch(years=[2020, 2021, 2022, 2023, 2024]):
    """
    批量查询所有 226 只票的 9 项检查结果
    返回 {code: {step_name: {pass_count: int, years_passed: int}}}
    """
    conn = get_db()
    cur = conn.cursor()
    
    years_str = ','.join(str(y) for y in years)
    
    # 一次性拉所有 5 年数据
    cur.execute(f'''
        SELECT stock_code, year, op_cash_flow, inv_cash_flow, fin_cash_flow, capex, goodwill,
               revenue, net_profit, op_profit, total_assets, total_liability, total_equity, fixed_assets
        FROM w19_18steps
        WHERE year IN ({years_str})
    ''')
    rows = cur.fetchall()
    conn.close()
    
    # 整理成 {code: {year: data}}
    data = {}
    for r in rows:
        code, year, ocf, icf, fcf, capex, gw, rev, np, op, ta, tl, te, fa = r
        if code not in data:
            data[code] = {}
        data[code][year] = {
            'ocf': ocf, 'icf': icf, 'fcf': fcf, 'capex': capex, 'gw': gw,
            'rev': rev, 'np': np, 'op': op, 'ta': ta, 'tl': tl, 'te': te, 'fa': fa,
        }
    
    # 计算 9 项检查
    results = {}
    for code, year_data in data.items():
        checks = {
            's2_营业利润': 0,
            's3_现金流匹配': 0,
            's4_ROE': 0,
            's5_负债率': 0,
            's6_现金为王': 0,
            's10_净利率': 0,
            's11_ROA': 0,
            's13_自由现金流': 0,
            's16_商誉': 0,
        }
        years_passed = 0
        last_3_years_ocf_pos = []
        
        for y in sorted(year_data.keys()):
            d = year_data[y]
            if d['op'] is not None and d['op'] > 0:
                checks['s2_营业利润'] += 1
            if d['ocf'] is not None and d['np'] is not None and d['np'] > 0:
                if d['ocf'] / d['np'] >= 0.8:
                    checks['s3_现金流匹配'] += 1
            if d['np'] is not None and d['te'] is not None and d['te'] > 0:
                if d['np'] / d['te'] > 0.10:
                    checks['s4_ROE'] += 1
            if d['tl'] is not None and d['ta'] is not None and d['ta'] > 0:
                if d['tl'] / d['ta'] < 0.60:
                    checks['s5_负债率'] += 1
            if d['ocf'] is not None and d['ocf'] > 0:
                checks['s6_现金为王'] += 1
            if d['rev'] is not None and d['np'] is not None and d['rev'] > 0:
                if d['np'] / d['rev'] > 0.05:
                    checks['s10_净利率'] += 1
            if d['op'] is not None and d['ta'] is not None and d['ta'] > 0:
                if d['op'] / d['ta'] > 0.05:
                    checks['s11_ROA'] += 1
            if d['ocf'] is not None and d['capex'] is not None:
                if d['ocf'] - d['capex'] > 0:
                    checks['s13_自由现金流'] += 1
            if d['gw'] is not None and d['te'] is not None and d['te'] > 0:
                if d['gw'] / d['te'] < 0.30:
                    checks['s16_商誉'] += 1
            years_passed += 1
            
            # 3 年稳定检查
            if y in years[-3:] and d['ocf'] is not None:
                last_3_years_ocf_pos.append(d['ocf'] > 0)
        
        # 健康等级
        if checks['s5_负债率'] >= 3 and checks['s13_自由现金流'] >= 4 and checks['s6_现金为王'] >= 4:
            grade = 'A'
        elif sum(1 for v in checks.values() if v >= 4) >= 5:
            grade = 'B'
        else:
            grade = 'C'
        
        # R 规则
        pass_count = sum(1 for v in checks.values() if v >= 4)
        stable_3y = all(last_3_years_ocf_pos) if len(last_3_years_ocf_pos) >= 3 else None
        
        rules = {
            'R079': checks['s2_营业利润'] < 3,
            'R080': checks['s3_现金流匹配'] < 2,
            'R081': checks['s4_ROE'] < 2,
            'R082': checks['s5_负债率'] < 2,
            'R083': checks['s6_现金为王'] < 3,
            'R084': checks['s10_净利率'] < 2,
            'R085': checks['s11_ROA'] < 2,
            'R086': checks['s13_自由现金流'] < 2,
            'R087': checks['s16_商誉'] < 2,
            'R088': stable_3y is False,
            'R089': grade == 'A',
            'R090': grade == 'B',
            'R091': pass_count >= 7,
            'R092': pass_count >= 5,
            'R093': pass_count <= 2,
            'R094': grade == 'C' and checks['s13_自由现金流'] < 2,
            'R095': checks['s13_自由现金流'] >= 4,
            'R096': checks['s6_现金为王'] >= 4 and checks['s13_自由现金流'] >= 4,
        }
        
        results[code] = {
            'health_score': sum(checks.values()),
            'pass_5y_count': pass_count,
            'health_grade': grade,
            'rules': rules,
            'checks': checks,
        }
    
    return results


def get_w19_summary(code, years=[2020, 2021, 2022, 2023, 2024]):
    """单只票的评估（用于实时查询）"""
    batch = get_step_results_batch(years)
    if code not in batch:
        return {'code': code, 'health_grade': 'C', 'pass_5y_count': 0, 'health_score': 0, 'rules': {}}
    r = batch[code]
    r['code'] = code
    return r


if __name__ == "__main__":
    import time
    t0 = time.time()
    batch = get_step_results_batch()
    print(f"\n批量查询耗时: {time.time()-t0:.1f}秒")
    
    # 统计
    a_count = sum(1 for r in batch.values() if r['health_grade'] == 'A')
    b_count = sum(1 for r in batch.values() if r['health_grade'] == 'B')
    c_count = sum(1 for r in batch.values() if r['health_grade'] == 'C')
    danger = sum(1 for r in batch.values() if r['rules'].get('R094', False))
    healthy = sum(1 for r in batch.values() if r['rules'].get('R089', False))
    fcf_strong = sum(1 for r in batch.values() if r['rules'].get('R095', False))
    
    print(f"A 级（健康）: {a_count}")
    print(f"B 级（中等）: {b_count}")
    print(f"C 级（较差）: {c_count}")
    print(f"R094 危险: {danger}")
    print(f"R089 优质: {healthy}")
    print(f"R095 自由现金流强: {fcf_strong}")
    
    print("\nA 级 票（前 10）:")
    a_stocks = [c for c, r in batch.items() if r['health_grade'] == 'A']
    for c in a_stocks[:10]:
        r = batch[c]
        print(f"  {c}: 健康分 {r['health_score']}/45, 通过 {r['pass_5y_count']}/9")
    
    print("\nR094 危险票（前 5）:")
    d_stocks = [c for c, r in batch.items() if r['rules'].get('R094', False)]
    for c in d_stocks[:5]:
        r = batch[c]
        print(f"  {c}: 健康分 {r['health_score']}/45, 自由现金流 {r['checks']['s13_自由现金流']}/5")
    
    # 测单只
    print("\n=== 600519.SH ===")
    s = get_w19_summary('600519.SH')
    print(f"  健康分: {s['health_score']}/45, 健康度: {s['health_grade']}, R089: {s['rules'].get('R089')}")


# ==================== P3-3 R098 规则 ====================

def step19_应付预收_应收预付(code, year):
    """P3-3 R098: 应付预收 - 应收预付 > 0
    简化为：AdvanceReceipts > 0 (有预收能力 = 强势)"""
    conn = get_db()
    cur = conn.execute('''
        SELECT advance_receipts FROM w19_18steps
        WHERE stock_code = ? AND year = ?
    ''', (code, year))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] is None:
        return None
    return row[0] > 0


def get_w19_summary_v5326(code, years=[2020, 2021, 2022, 2023, 2024]):
    """v5.3.30 完整版 W19 18 步分析（含 P3-3 R098）"""
    batch = get_step_results_batch(years)
    if code not in batch:
        return {'code': code, 'health_grade': 'C', 'pass_5y_count': 0, 'health_score': 0, 'rules': {}}
    
    r = batch[code]
    r['code'] = code
    
    # P3-3 R098: 5 年预收款（advance_receipts）> 0 的次数
    conn = get_db()
    cur = conn.execute(f'''
        SELECT COUNT(*) FROM w19_18steps
        WHERE stock_code = ? AND year IN ({','.join(str(y) for y in years)})
        AND advance_receipts IS NOT NULL AND advance_receipts > 0
    ''', (code,))
    ar_count = cur.fetchone()[0]
    conn.close()
    
    # R098: 应付预收-应收预付 > 0 (用 advance_receipts 近似)
    r['rules']['R098'] = ar_count >= 3  # 5 年中至少 3 年有预收
    r['advance_receipts_5y'] = ar_count
    
    return r
