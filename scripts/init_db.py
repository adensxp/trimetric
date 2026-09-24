#!/usr/bin/env python3
"""一键初始化四库（新用户安装第一步）

用法：python3 scripts/init_db.py
四库架构（业务域分割）：
  MASTER_DB     总数据库：K线 + 年报 + 财务 + 质量快照（494 股池，唯一必须备份的库）
  CANDIDATES_DB 候选库：评分/预备池/皇冠/质量警报（每日可 truncate 重建）
  POSITIONS_DB  持仓库：持仓/交易流水/事件/每日盈亏
  CAPITAL_DB    资金库：现金流/本金/费率/规则存档

幂等：全部 CREATE TABLE IF NOT EXISTS，重复运行安全。
部署机从老库升级：先跑 scripts/migrate_to_4db.py。
"""
import sqlite3

import config

log = config.setup_logging("init_db")

MASTER_TABLES = [
    """CREATE TABLE IF NOT EXISTS stock_daily (
        trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
        open_price REAL, high_price REAL, low_price REAL, close_price REAL, volume REAL)""",
    "CREATE INDEX IF NOT EXISTS idx_sd ON stock_daily(stock_code, trade_date)",
    """CREATE TABLE IF NOT EXISTS stock_pe_ttm (
        stock_code TEXT PRIMARY KEY, pe_ttm REAL, pe_ttm_date TEXT,
        dividend_ratio REAL, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS stock_info (
        stock_code TEXT, stock_name TEXT, industry TEXT,
        rank_in_industry INTEGER, list_date TEXT)""",
    """CREATE TABLE IF NOT EXISTS w19_18steps (
        stock_code TEXT NOT NULL, year INTEGER NOT NULL,
        op_cash_flow REAL, inv_cash_flow REAL, fin_cash_flow REAL, capex REAL, goodwill REAL,
        revenue REAL, net_profit REAL, op_profit REAL, total_assets REAL,
        total_liability REAL, total_equity REAL, fixed_assets REAL, advance_receipts REAL)""",
    "CREATE TABLE IF NOT EXISTS market_pe_history (trade_date TEXT, index_code TEXT, pe_ttm REAL, pe_percentile REAL)",
    "CREATE TABLE IF NOT EXISTS financial_data (stock_code TEXT, report_date TEXT, financevalue REAL, yoy REAL)",
    """CREATE TABLE IF NOT EXISTS fraud_redflags (
        stock_code TEXT NOT NULL, year INTEGER NOT NULL,
        account_receivable REAL, monetary_funds REAL, advance_payment REAL,
        other_receivable REAL, construction_in_process REAL, interest_income REAL,
        total_assets REAL, r071_ratio REAL, r072_ratio REAL, r073_ratio REAL, r074_ratio REAL,
        r071_triggered INTEGER DEFAULT 0, r072_triggered INTEGER DEFAULT 0,
        r073_triggered INTEGER DEFAULT 0, r074_triggered INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS stock_moats (
        stock_code TEXT PRIMARY KEY, industry TEXT, moats TEXT,
        moat_count INTEGER DEFAULT 0, score REAL DEFAULT 0, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS w20_8indicators (
        stock_code TEXT NOT NULL, year INTEGER NOT NULL,
        operating_revenue REAL, operating_profit REAL, fixed_assets REAL, total_assets REAL,
        op_margin REAL, revenue_growth REAL, fixed_asset_ratio REAL,
        r075_pass INTEGER DEFAULT 0, r076_pass INTEGER DEFAULT 0, r077_pass INTEGER DEFAULT 0,
        PRIMARY KEY (stock_code, year))""",
    """CREATE TABLE IF NOT EXISTS daily_company_quality (
        stock_code TEXT, trade_date TEXT, w19_grade TEXT, w19_score REAL,
        w20_indicators_pass INTEGER, w22_moats_count INTEGER, w22_roa REAL,
        w16_redflag INTEGER, div_yield REAL, roe_5y REAL, is_core_holding INTEGER,
        quality_change TEXT, prev_w19_grade TEXT, prev_w20_pass INTEGER, prev_w22_moats INTEGER)""",
]

CANDIDATES_TABLES = [
    "CREATE TABLE IF NOT EXISTS daily_prep_pool (prep_date TEXT, strategy TEXT, rank INTEGER, code TEXT, name TEXT, total_score REAL, signal_count INTEGER)",
    """CREATE TABLE IF NOT EXISTS daily_pool_scores (
        score_date TEXT, strategy TEXT, code TEXT, name TEXT, total_score REAL,
        signal_count INTEGER, change_pct REAL, vol_ratio REAL, close_price REAL,
        ma5 REAL, pattern TEXT, rank INTEGER, created_at TEXT)""",
    "CREATE TABLE IF NOT EXISTS crown_jewels (code TEXT PRIMARY KEY, name TEXT, roe_5y_avg REAL, debt_5y_max REAL, level TEXT, updated_at TEXT)",
    """CREATE TABLE IF NOT EXISTS quality_alert_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code TEXT, stock_name TEXT,
        trade_date TEXT, alert_type TEXT, severity TEXT,
        prev_value TEXT, current_value TEXT, action TEXT, handled INTEGER DEFAULT 0)""",
]

POSITIONS_TABLES = [
    """CREATE TABLE IF NOT EXISTS positions (
        code TEXT PRIMARY KEY, name TEXT, shares INTEGER, cost_basis REAL, entry_date TEXT,
        entry_price REAL, position_pct REAL, t30_deadline TEXT, t30_days_left INTEGER,
        add_count INTEGER DEFAULT 0, add_total_shares INTEGER DEFAULT 0,
        strategy_type TEXT, strategy_version TEXT, active INTEGER DEFAULT 1,
        notes TEXT, created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT, trade_time TEXT,
        code TEXT, name TEXT, action TEXT, shares INTEGER, price REAL, amount REAL,
        commission REAL, stamp_tax REAL, transfer_fee REAL, total_fee REAL,
        net_amount REAL, exchange TEXT, position_pct REAL,
        reason TEXT, strategy_version TEXT, notes TEXT)""",
    "CREATE TABLE IF NOT EXISTS events (event_date TEXT, event_time TEXT, event_type TEXT, code TEXT, title TEXT, description TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS daily_pnl (trade_date TEXT, code TEXT, name TEXT, shares INTEGER, cost_basis REAL, close_price REAL, market_value REAL, unrealized_pnl REAL, return_pct REAL, UNIQUE(trade_date, code))",
]

CAPITAL_TABLES = [
    "CREATE TABLE IF NOT EXISTS cash_flow (flow_date TEXT, type TEXT, amount REAL, balance_after REAL, description TEXT)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS position_rules (rule_id INTEGER PRIMARY KEY, rule_type TEXT, is_active INTEGER DEFAULT 1)",
]


def init(db_path, tables, label):
    conn = sqlite3.connect(db_path)
    for ddl in tables:
        conn.execute(ddl)
    conn.commit()
    n = len(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())
    conn.close()
    log.info("✅ %s: %s（%d 张表就绪）", label, db_path, n)


if __name__ == "__main__":
    init(config.MASTER_DB, MASTER_TABLES, "① 总数据库（K线+年报+财务）")
    init(config.CANDIDATES_DB, CANDIDATES_TABLES, "② 候选库（每日可重建）")
    init(config.POSITIONS_DB, POSITIONS_TABLES, "③ 持仓库（持仓+流水+事件）")
    init(config.CAPITAL_DB, CAPITAL_TABLES, "④ 资金库（现金流+本金+规则）")
    log.info("四库初始化完成。下一步：python3 scripts/fetch_data.py --codes 600519.SH")
    log.info("部署机老数据升级：python3 scripts/migrate_to_4db.py")
