#!/usr/bin/env python3
"""从 financial_data + fraud_redflags 表生成 w20_8indicators 表"""
import config
import sqlite3
import sys
sys.path.insert(0, config.DEPLOY_DIR)
from w20_8indicators import init_db, calculate_w20_indicators

DB_PATH = config.STOCK_DB
init_db()
conn = sqlite3.connect(DB_PATH)

# 清空表
conn.execute('DELETE FROM w20_8indicators')
conn.commit()

# 拉所有股票
cur = conn.execute('''
    SELECT DISTINCT stock_code FROM financial_data
    WHERE finstatementcode IN ('OperatingRevenue', 'OperatingProfit', 'FixedAssets')
''')
codes = [r[0] for r in cur.fetchall()]
print(f"有数据的票: {len(codes)}")

ok = 0
for code in codes:
    # 拉该票的年报数据（支持 '年报' 和 'Y' 两种）
    cur2 = conn.execute('''
        SELECT report_date, finstatementcode, financevalue, report_type
        FROM financial_data
        WHERE stock_code = ? AND report_type IN ('年报', 'Y')
        AND finstatementcode IN ('OperatingRevenue', 'OperatingProfit', 'FixedAssets')
        ORDER BY report_date
    ''', (code,))
    rows = cur2.fetchall()
    if not rows:
        continue
    
    # 整理数据（取每个 (year, field) 最新一条）
    yearly = {}
    for date, key, val, rt in rows:
        year = int(date[:4])
        if year not in yearly:
            yearly[year] = {}
        if key not in yearly[year]:  # 取第一条（同一年同一字段只保留一条）
            try:
                yearly[year][key] = float(val) if val else 0
            except:
                yearly[year][key] = 0
    
    # 算 R075/R077 - 单年
    for year, d in yearly.items():
        op_rev = d.get('OperatingRevenue', 0)
        op_prof = d.get('OperatingProfit', 0)
        fixed = d.get('FixedAssets', 0)
        
        # 查 TotalAssets (from fraud_redflags)
        cur3 = conn.execute('SELECT total_assets FROM fraud_redflags WHERE stock_code=? AND year=?', (code, year))
        ta_row = cur3.fetchone()
        total = ta_row[0] if ta_row else 0
        
        if total == 0:
            continue
        
        result = calculate_w20_indicators(code, year, op_rev, op_prof, fixed, total)
        if result:
            conn.execute('''
                INSERT OR REPLACE INTO w20_8indicators
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (code, year, op_rev, op_prof, fixed, total,
                  result['op_margin'], None, result['fixed_asset_ratio'],
                  result['r075_pass'], 0, result['r077_pass']))
            ok += 1

conn.commit()
print(f"✅ 初步写入 {ok} 行")

# 计算 R076 营收增长率
ok2 = 0
for code in codes:
    cur3 = conn.execute('''
        SELECT report_date, financevalue FROM financial_data
        WHERE stock_code = ? AND report_type IN ('年报', 'Y') AND finstatementcode = 'OperatingRevenue'
        ORDER BY report_date
    ''', (code,))
    rows = cur3.fetchall()
    if len(rows) < 2:
        continue
    
    seen_years = set()
    for i in range(1, len(rows)):
        year = int(rows[i][0][:4])
        if year in seen_years:
            continue
        seen_years.add(year)
        prev_val = rows[i-1][1]
        curr_val = rows[i][1]
        if prev_val and prev_val > 0 and curr_val is not None:
            g = (curr_val - prev_val) / prev_val
            r076_pass = int(g > 0.10)
            conn.execute('UPDATE w20_8indicators SET revenue_growth=?, r076_pass=? WHERE stock_code=? AND year=?',
                        (g, r076_pass, code, year))
            ok2 += 1
conn.commit()
print(f"✅ R076 增长率: {ok2} 行")

cur = conn.execute('SELECT COUNT(*) FROM w20_8indicators')
print(f"w20_8indicators 总行数: {cur.fetchone()[0]}")
cur = conn.execute('SELECT COUNT(DISTINCT stock_code) FROM w20_8indicators')
print(f"w20_8indicators 股票数: {cur.fetchone()[0]}")

conn.close()
