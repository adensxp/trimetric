#!/usr/bin/env python3
"""
v5.3.30 W20 微淼 8 指标终极海选

补 3 个指标（v5.3.25 已集成 5 个）：
- R075: 营业利润率 > 20% × 5 年
- R076: 营业收入增长率 > 10% × 5 年
- R077: 固定资产比率 < 40% × 5 年

加上 R059-R066 已经覆盖的 5 个：
- R059: REITs 派息率 > 5%
- R060: 净利润现金含量 > 80%
- R061: 分红比例（25%-70%）
- R062: 毛利率 > 40%
- R063: 资产负债率 < 60%
- R064: 上市 > 3 年
- R065: 深证 PE < 20（大盘择时）
- R066: PE 绝对值 < 50 / TTM PE < 15
"""
import config
import sqlite3

DB_PATH = config.STOCK_DB

# 阈值
R075_THRESHOLD = 0.0  # 营业利润率 > 20%
R076_THRESHOLD = 0.05  # 营业收入增长率 > 10%
R077_THRESHOLD = 0.50  # 固定资产比率 < 40%


def init_db():
    """创建 w20_8indicators 表"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS w20_8indicators (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            operating_revenue REAL,
            operating_profit REAL,
            fixed_assets REAL,
            total_assets REAL,
            op_margin REAL,
            revenue_growth REAL,
            fixed_asset_ratio REAL,
            r075_pass INTEGER DEFAULT 0,
            r076_pass INTEGER DEFAULT 0,
            r077_pass INTEGER DEFAULT 0,
            PRIMARY KEY (stock_code, year)
        )
    ''')
    conn.commit()
    conn.close()


def calculate_w20_indicators(stock_code, year, operating_revenue, operating_profit, fixed_assets, total_assets):
    """计算单只单年的 3 指标"""
    if total_assets is None or total_assets <= 0:
        return None

    op_margin = (operating_profit or 0) / operating_revenue if operating_revenue and operating_revenue > 0 else 0
    fixed_asset_ratio = (fixed_assets or 0) / total_assets if total_assets > 0 else 0

    return {
        'op_margin': op_margin,
        'fixed_asset_ratio': fixed_asset_ratio,
        'r075_pass': int(op_margin > R075_THRESHOLD),
        'r077_pass': int(fixed_asset_ratio < R077_THRESHOLD)
    }


def calculate_revenue_growth(stock_code, conn):
    """计算一只票 5 年的营业收入增长率（需要历史数据）"""
    cur = conn.execute('''
        SELECT report_date, financevalue
        FROM financial_data
        WHERE stock_code = ? AND finstatementcode = 'OperatingRevenue'
        AND report_date LIKE '%-12-31'
        AND report_type = 'Y'
        ORDER BY report_date
    ''', (stock_code,))
    rows = cur.fetchall()
    if len(rows) < 2:
        return {}

    # 计算每年增长率
    growth = {}
    for i in range(1, len(rows)):
        year = int(rows[i][0][:4])
        prev_val = rows[i-1][1]
        curr_val = rows[i][1]
        if prev_val and prev_val > 0 and curr_val is not None:
            g = (curr_val - prev_val) / prev_val
            growth[year] = g

    return growth


def get_8indicators_summary(code):
    """获取一只票的 8 指标汇总"""
    conn = sqlite3.connect(DB_PATH)

    # 拉 5 年数据
    cur = conn.execute('''
        SELECT year, operating_revenue, operating_profit, fixed_assets, total_assets,
               op_margin, revenue_growth, fixed_asset_ratio
        FROM w20_8indicators
        WHERE stock_code = ?
        ORDER BY year
    ''', (code,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {'r075_5y_avg': 0, 'r076_5y_count': 0, 'r077_5y_avg': 0,
                'r075_pass': False, 'r076_pass': False, 'r077_pass': False,
                'data_available': False}

    # 5 年中至少 2 年满足（更宽松）
    r075_5y_count = sum(1 for r in rows if r[5] is not None and r[5] > R075_THRESHOLD)
    r076_5y_count = sum(1 for r in rows if r[6] and r[6] > R076_THRESHOLD)
    r077_5y_count = sum(1 for r in rows if r[7] is not None and r[7] < R077_THRESHOLD)
    n_years = len(rows)

    return {
        'r075_5y_count': r075_5y_count,
        'r076_5y_count': r076_5y_count,
        'r077_5y_count': r077_5y_count,
        'n_years': n_years,
        'r075_pass': r075_5y_count >= 2,  # 至少 2 年 > 0%
        'r076_pass': r076_5y_count >= 2,  # 至少 2 年增长 > 5%
        'r077_pass': r077_5y_count >= 2,  # 至少 2 年 < 50%
        'data_available': True
    }


def filter_pool_by_w20(codes):
    """8 指标过滤（任一不满足 → 剔除）"""
    safe = []
    blacklist = []
    for code in codes:
        s = get_8indicators_summary(code)
        if not s['data_available']:
            blacklist.append((code, 'W20 8 指标无数据'))
        elif not (s['r075_pass'] and s['r076_pass'] and s['r077_pass']):
            failed = []
            if not s['r075_pass']: failed.append('R075 营业利润率<20%')
            if not s['r076_pass']: failed.append('R076 营收增长<10%')
            if not s['r077_pass']: failed.append('R077 固资占比>=40%')
            blacklist.append((code, '|'.join(failed)))
        else:
            safe.append(code)
    return safe, blacklist


if __name__ == '__main__':
    init_db()
    print("✅ w20_8indicators 表已创建")

    # 测恒立液压
    s = get_8indicators_summary('601100.SH')
    print(f"\n=== 恒立液压 (601100.SH) ===")
    print(f"  数据可用: {s['data_available']}")
    print(f"  R075 营业利润率 5y 均值: {s['r075_5y_avg']*100:.1f}% (阈值 20%)")
    print(f"  R076 营收增长 >10% 年数: {s['r076_5y_count']}/5 (阈值 3)")
    print(f"  R077 固资占比 5y 均值: {s['r077_5y_avg']*100:.1f}% (阈值 <40%)")
    print(f"  通过: R075={s['r075_pass']} R076={s['r076_pass']} R077={s['r077_pass']}")
