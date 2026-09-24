#!/usr/bin/env python3
"""
V5.3.26 财务造假红旗检查模块（W16 微淼）

4 大红旗规则：
- R071: 应收账款/总资产 > 20%（任一年）→ 虚增收入风险
- R072: 利息收入/货币资金 < 2%（任一年，且利息收入为正）→ 货币资金造假风险
- R073: (预付款项+其他应收款)/总资产 > 10%（任一年）→ 流动资产造假风险
- R074: 在建工程/总资产 > 30%（任一年）→ 非流动资产异常

逻辑：4 红旗任一触发 → 直接从预备池剔除
"""
import config
import sqlite3
import json

DB_PATH = config.STOCK_DB

# 阈值
R071_THRESHOLD = 0.20  # 应收账款/总资产
R072_THRESHOLD = 0.02  # 利息收入/货币资金
R073_THRESHOLD = 0.10  # (预付+其他应收)/总资产
R074_THRESHOLD = 0.30  # 在建工程/总资产

def init_db():
    """创建 fraud_redflags 表"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS fraud_redflags (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            account_receivable REAL,
            monetary_funds REAL,
            advance_payment REAL,
            other_receivable REAL,
            construction_in_process REAL,
            interest_income REAL,
            total_assets REAL,
            r071_ratio REAL,
            r072_ratio REAL,
            r073_ratio REAL,
            r074_ratio REAL,
            r071_triggered INTEGER DEFAULT 0,
            r072_triggered INTEGER DEFAULT 0,
            r073_triggered INTEGER DEFAULT 0,
            r074_triggered INTEGER DEFAULT 0,
            PRIMARY KEY (stock_code, year)
        )
    ''')
    conn.commit()
    conn.close()


def calculate_redflags_for_stock(conn, code, year, account_receivable, monetary_funds,
                                  advance_payment, other_receivable, construction_in_process,
                                  interest_income, total_assets):
    """计算单只单年的 4 红旗"""
    if total_assets is None or total_assets <= 0:
        return None
    
    r071 = (account_receivable or 0) / total_assets
    r072 = abs(interest_income or 0) / monetary_funds if monetary_funds and monetary_funds > 0 else 0
    r073 = ((advance_payment or 0) + (other_receivable or 0)) / total_assets
    r074 = (construction_in_process or 0) / total_assets
    
    # 触发判断
    t071 = int(r071 > R071_THRESHOLD)
    t072 = int(r072 < R072_THRESHOLD and (interest_income or 0) > 0)  # 利息收入为负不算
    t073 = int(r073 > R073_THRESHOLD)
    t074 = int(r074 > R074_THRESHOLD)
    
    return {
        'r071_ratio': r071,
        'r072_ratio': r072,
        'r073_ratio': r073,
        'r074_ratio': r074,
        'r071_triggered': t071,
        'r072_triggered': t072,
        'r073_triggered': t073,
        'r074_triggered': t074
    }


def get_redflag_summary(code):
    """获取一只票 5 年的红旗汇总

    返回：{
        'triggered_rules': [规则列表],
        'max_r071': 0.xx,
        'max_r072': 0.xx,
        'max_r073': 0.xx,
        'max_r074': 0.xx,
        'is_blacklist': True/False
    }
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT year, r071_ratio, r072_ratio, r073_ratio, r074_ratio,
               r071_triggered, r072_triggered, r073_triggered, r074_triggered
        FROM fraud_redflags
        WHERE stock_code = ?
        ORDER BY year
    ''', (code,))
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        return {'triggered_rules': [], 'max_r071': 0, 'max_r072': 0,
                'max_r073': 0, 'max_r074': 0, 'is_blacklist': False,
                'data_available': False}
    
    triggered = set()
    max_r071 = max(r[1] for r in rows) if rows else 0
    max_r072 = max(r[2] for r in rows) if rows else 0
    max_r073 = max(r[3] for r in rows) if rows else 0
    max_r074 = max(r[4] for r in rows) if rows else 0
    
    for r in rows:
        if r[5]: triggered.add('R071 应收/总资产>20%')
        if r[6]: triggered.add('R072 利息/货币<2%')
        if r[7]: triggered.add('R073 预付+其他应收/总资产>10%')
        if r[8]: triggered.add('R074 在建工程/总资产>30%')
    
    return {
        'triggered_rules': sorted(triggered),
        'max_r071': max_r071,
        'max_r072': max_r072,
        'max_r073': max_r073,
        'max_r074': max_r074,
        'is_blacklist': len(triggered) > 0,
        'data_available': True
    }


def filter_pool_by_redflags(codes):
    """从股票池中过滤掉触发红旗的票

    返回：(safe_codes, blacklisted)
    """
    safe = []
    blacklisted = []
    for code in codes:
        summary = get_redflag_summary(code)
        if summary['is_blacklist']:
            blacklisted.append((code, summary))
        else:
            safe.append(code)
    return safe, blacklisted


if __name__ == '__main__':
    import sys
    init_db()
    print("✅ fraud_redflags 表已创建/已存在")
    
    # 测一下恒立液压
    s = get_redflag_summary('601100.SH')
    print(f"\n=== 恒立液压 (601100.SH) ===")
    print(f"  数据可用: {s['data_available']}")
    print(f"  R071 max: {s['max_r071']*100:.2f}%")
    print(f"  R072 max: {s['max_r072']*100:.2f}%")
    print(f"  R073 max: {s['max_r073']*100:.2f}%")
    print(f"  R074 max: {s['max_r074']*100:.2f}%")
    print(f"  触发规则: {s['triggered_rules']}")
    print(f"  是否黑名单: {s['is_blacklist']}")
