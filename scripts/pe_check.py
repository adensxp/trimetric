#!/usr/bin/env python3
"""
v5.3.30 好价格检查（W4 微淼）

好价格双条件：
- 长线：必须 TTM PE < 15
- 底仓：必须 TTM PE < 20
- 市场水位：深证 PE < 20 才考虑建仓（极端情况全市场禁入）
"""
import config
import sqlite3

DB_PATH = config.STOCK_DB

# 阈值
LONG_TERM_PE_THRESHOLD = 15.0  # 长线
CORE_HOLDING_PE_THRESHOLD = 20.0  # 底仓
SHORT_TERM_PE_THRESHOLD = 30.0  # 短线（容忍高 PE）
MARKET_PE_THRESHOLD = 20.0  # 深证 PE 水位


def init_db():
    """创建 stock_pe_ttm 表"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stock_pe_ttm (
            stock_code TEXT PRIMARY KEY,
            pe_ttm REAL,
            pe_ttm_date TEXT,
            dividend_ratio REAL,
            updated_at TEXT
        )
    ''')
    conn.commit()
    conn.close()


def get_stock_pe(code):
    """获取一只票的 TTM PE 和股息率"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT pe_ttm, pe_ttm_date, dividend_ratio
        FROM stock_pe_ttm
        WHERE stock_code = ?
    ''', (code,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'pe_ttm': row[0], 'pe_ttm_date': row[1], 'dividend_ratio': row[2]}
    return None


def get_market_pe(index_code='1055'):
    """获取深证 PE（默认 1055 = 深证成指）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('''
        SELECT trade_date, pe_ttm, pe_percentile
        FROM market_pe_history
        WHERE index_code = ?
        ORDER BY trade_date DESC
        LIMIT 1
    ''', (index_code,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'date': row[0], 'pe_ttm': row[1], 'percentile': row[2]}
    return None


def is_good_price_for_strategy(code, strategy):
    """判断一只票对于某策略是否"好价格"

    返回：(is_good, reason)
    """
    pe_data = get_stock_pe(code)
    if not pe_data or pe_data['pe_ttm'] is None:
        return False, "无 TTM PE 数据"

    pe = pe_data['pe_ttm']
    if strategy == 'LONG_TERM':
        threshold = LONG_TERM_PE_THRESHOLD
    elif strategy == 'CORE_HOLDING':
        threshold = CORE_HOLDING_PE_THRESHOLD
    else:  # SHORT_TERM
        threshold = SHORT_TERM_PE_THRESHOLD

    if pe < threshold:
        return True, f"PE TTM {pe:.1f} < {threshold}"
    else:
        return False, f"PE TTM {pe:.1f} >= {threshold}"


def filter_pool_by_pe(codes, strategy):
    """根据策略过滤股票池（不满足 PE 阈值的剔除）

    返回：(safe_codes, blacklist_with_reasons)
    """
    safe = []
    blacklist = []
    for code in codes:
        is_good, reason = is_good_price_for_strategy(code, strategy)
        if is_good:
            safe.append(code)
        else:
            blacklist.append((code, reason))
    return safe, blacklist


if __name__ == '__main__':
    init_db()
    print("✅ stock_pe_ttm 表已创建")
    
    # 测试
    for code in ['601100.SH', '600519.SH']:
        d = get_stock_pe(code)
        print(f"\n{code}: {d}")
    
    # 测试
    print("\n=== 恒立液压策略过滤 ===")
    for s in ['SHORT_TERM', 'LONG_TERM', 'CORE_HOLDING']:
        is_good, reason = is_good_price_for_strategy('601100.SH', s)
        print(f"  {s}: {is_good} ({reason})")


def get_all_pe_data():
    """批量获取所有票的 PE 和股息率 - 性能优化"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT stock_code, pe_ttm, dividend_ratio FROM stock_pe_ttm')
    data = {r[0]: {'pe_ttm': r[1], 'dividend_ratio': r[2]} for r in cur.fetchall()}
    conn.close()
    return data
