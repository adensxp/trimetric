#!/usr/bin/env python3
"""
高量战法本地数据库 · 每日增量更新工具
=========================================
功能：
  1. 幂等性检查（避免重复拉取）
  2. 生成拉取计划（分批、API 参数）
  3. 导入拉取的 CSV 数据（INSERT OR REPLACE 去重）
  4. 输出最终报告

用法（cron 调用）：
  python3 /workspace/stock_db/daily_update.py check
    → 输出 today_updated: true/false + 各表最新日期
  python3 /workspace/stock_db/daily_update.py plan
    → 输出拉取计划（JSON 格式），cron 按计划调 API
  python3 /workspace/stock_db/daily_update.py import
    → 导入 /workspace/stock_db/data/daily_*.csv 到数据库
  python3 /workspace/stock_db/daily_update.py report
    → 输出今日更新报告
  python3 /workspace/stock_db/daily_update.py all
    → 执行 check + 打印 plan + 提示需要执行 API
"""
import config
log = config.setup_logging("daily_update")
import sqlite3
import csv
import os
import json
import sys
import glob
import re
import datetime as dt
from collections import defaultdict

DB = config.STOCK_DB
DATA = config.DATA_DIR
TODAY = dt.date.today().strftime("%Y-%m-%d")
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).strftime("%Y-%m-%d")

# 119 票分 3 批（API 限制 ≤50 票/批）
BATCHES = {
    'b1': [  # 49 票
        '000002.SZ', '000063.SZ', '000157.SZ', '000333.SZ', '000425.SZ',
        '000528.SZ', '000651.SZ', '000725.SZ', '000768.SZ', '000786.SZ',
        '000792.SZ', '000825.SZ', '000831.SZ', '000858.SZ', '000877.SZ',
        '000895.SZ', '000932.SZ', '001979.SZ', '002027.SZ', '002074.SZ',
        '002154.SZ', '002230.SZ', '002266.SZ', '002271.SZ', '002311.SZ',
        '002352.SZ', '002415.SZ', '002475.SZ', '002493.SZ', '002508.SZ',
        '002555.SZ', '002572.SZ', '002594.SZ', '002697.SZ', '002714.SZ',
        '300070.SZ', '300144.SZ', '300251.SZ', '300308.SZ', '300413.SZ',
        '300498.SZ', '300750.SZ', '300760.SZ', '300957.SZ', '600008.SH',
        '600011.SH', '600018.SH', '600019.SH', '600028.SH',
    ],
    'b2': [  # 50 票
        '600030.SH', '600031.SH', '600036.SH', '600048.SH', '600150.SH',
        '600157.SH', '600177.SH', '600276.SH', '600309.SH', '600315.SH',
        '600340.SH', '600362.SH', '600398.SH', '600438.SH', '600519.SH',
        '600547.SH', '600566.SH', '600585.SH', '600588.SH', '600600.SH',
        '600660.SH', '600690.SH', '600754.SH', '600760.SH', '600795.SH',
        '600859.SH', '600886.SH', '600887.SH', '600888.SH', '600900.SH',
        '600938.SH', '600941.SH', '601001.SH', '601012.SH', '601088.SH',
        '601111.SH', '601116.SH', '601127.SH', '601225.SH', '601288.SH',
        '601318.SH', '601328.SH', '601390.SH', '601398.SH', '601600.SH',
        '601601.SH', '601618.SH', '601628.SH', '601633.SH', '601668.SH',
    ],
    'b3': [  # 20 票
        '601800.SH', '601808.SH', '601816.SH', '601857.SH', '601888.SH',
        '601898.SH', '601899.SH', '601933.SH', '601985.SH', '601988.SH',
        '601995.SH', '601998.SH', '603019.SH', '603195.SH', '603259.SH',
        '603501.SH', '603568.SH', '603605.SH', '603833.SH', '688111.SH',
    ],
}

# 验证 119 票
ALL_STOCKS = []
for b in BATCHES.values():
    ALL_STOCKS.extend(b)
assert len(ALL_STOCKS) == 119, f"Expected 119 stocks, got {len(ALL_STOCKS)}"


def get_count(conn, table):
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def cmd_check():
    """检查今天是否已更新 + 各表最新日期"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    log.info(f"=== 今日 ({TODAY}) 更新状态检查 ===\n")
    
    # 检查今天 stock_daily 记录数
    cur.execute("SELECT COUNT(*) FROM stock_daily WHERE trade_date = ?", (TODAY,))
    daily_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT stock_code) FROM stock_daily WHERE trade_date = ?", (TODAY,))
    daily_stocks = cur.fetchone()[0]
    
    # 检查 shnum
    cur.execute("SELECT COUNT(*) FROM shareholder_num WHERE end_date = ?", (TODAY,))
    shnum_today = cur.fetchone()[0]
    
    # 检查 margin
    cur.execute("SELECT COUNT(*) FROM margin_trading WHERE trade_date = ?", (TODAY,))
    margin_today = cur.fetchone()[0]
    
    # 各表最新日期
    cur.execute("SELECT MAX(trade_date) FROM stock_daily")
    daily_max = cur.fetchone()[0]
    cur.execute("SELECT MAX(end_date) FROM shareholder_num")
    shnum_max = cur.fetchone()[0]
    cur.execute("SELECT MAX(trade_date) FROM margin_trading")
    margin_max = cur.fetchone()[0]
    
    log.info(f"  stock_daily:    今天 {daily_today} 条 ({daily_stocks} 票) / 最新日期 {daily_max}")
    log.info(f"  shareholder_num: 今天 {shnum_today} 条 / 最新日期 {shnum_max}")
    log.info(f"  margin_trading:  今天 {margin_today} 条 / 最新日期 {margin_max}")
    log.info('')
    
    # 判定
    today_updated = daily_today > 0 and shnum_today > 0
    log.info(f"today_updated: {today_updated}")
    
    # 总记录
    log.info(f"\n=== 数据库总览 ===")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        if t in ('sqlite_sequence', 'sync_log', 'metadata'):
            continue
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        c = cur.fetchone()[0]
        log.info(f"  {t}: {c:,}")
    
    conn.close()
    return today_updated


def cmd_plan():
    """生成拉取计划（cron 按计划调 API）"""
    # 自动决定日期窗口
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT MAX(trade_date) FROM stock_daily")
    last_date = cur.fetchone()[0]
    conn.close()
    
    if last_date and last_date >= TODAY:
        # 已有今天数据，不需要拉
        begin_date = end_date = TODAY
    else:
        # 拉最近 5 天（含周末补数据）
        end_date = TODAY
        begin_date = (dt.date.today() - dt.timedelta(days=5)).strftime("%Y-%m-%d")
    
    plan = {
        "begin_date": begin_date,
        "end_date": end_date,
        "batches": {}
    }
    for batch_name, stocks in BATCHES.items():
        plan["batches"][batch_name] = stocks
    
    log.info(json.dumps(plan, indent=2, ensure_ascii=False))
    return plan


def import_daily_csv(conn, f, table_type):
    """导入每日 CSV 到对应表"""
    cur = conn.cursor()
    
    if table_type == 'daily':
        sql = """INSERT OR REPLACE INTO stock_daily
                 (stock_code, trade_date, open_price, high_price, low_price, close_price, 
                  prev_close, change_amount, change_pct, volume, turnover, turnover_rate, amplitude)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"""
        col_map = {
            'stock_code': '股票代码', 'trade_date': '交易日',
            'open_price': '今开盘（元）', 'high_price': '最高价（元）', 'low_price': '最低价（元）',
            'close_price': '收盘价（元）', 'prev_close': '前收盘（元）', 'change_amount': '涨跌（元）',
            'change_pct': '涨跌幅（%）', 'volume': '成交量（万股）', 'turnover': '成交额（万元）',
            'turnover_rate': '换手率（%）', 'amplitude': '振幅（%）',
        }
    elif table_type == 'shnum':
        sql = """INSERT OR REPLACE INTO shareholder_num
                 (stock_code, end_date, total_holders, holder_change, avg_hold_shares)
                 VALUES (?,?,?,?,?)"""
        col_map = {
            'stock_code': '股票代码', 'end_date': '截止日期',
            'total_holders': '股东总户数(户)', 'holder_change': '股东总户数较上期增减(户)',
            'avg_hold_shares': '股东平均持股数(股/户)',
        }
        # 兼容缺省字段：top10stockholdersamount
        # 强制字段顺序：stock_code, end_date, total_holders, holder_change, avg_hold_shares
        force_fields = {'stock_code', 'end_date', 'total_holders', 'holder_change', 'avg_hold_shares'}
    elif table_type == 'margin':
        sql = """INSERT OR REPLACE INTO margin_trading
                 (stock_code, trade_date, margin_balance, short_balance, margin_buy)
                 VALUES (?,?,?,?,?)"""
        col_map = {
            'stock_code': '股票代码', 'trade_date': '交易日期',
            'margin_balance': '融资余额(元)', 'short_balance': '融券余额(元)',
            'margin_buy': '融资净买入额(元)',
        }
        # 实际 API 返回 '交易日期' 字段；可能某些版本叫 tradingday
        if '交易日期' not in col_map.values() and 'tradingday' in (open(f, 'r', encoding='utf-8-sig').readline() if False else ''):
            pass  # 占位
    else:
        return 0
    
    def safe_float(v):
        try:
            return float(v) if v and v.strip() else None
        except:
            return None
    def safe_int(v):
        try:
            return int(v) if v and v.strip() else None
        except:
            return None
    
    def cast(col, v):
        if col in ('total_holders', 'holder_change'):
            return safe_int(v)
        if col in ('stock_code', 'trade_date', 'end_date'):
            return v if v else None  # 保留为字符串
        return safe_float(v)
    
    fields = list(col_map.keys())
    with open(f, encoding='utf-8-sig') as fh:
        reader = csv.DictReader(fh)
        rows = []
        for row in reader:
            values = []
            for field in fields:
                col_name = col_map[field]
                v = row.get(col_name, '')
                values.append(cast(field, v))
            # 自动给 stock_code 补后缀（首字段）
            if values and isinstance(values[0], str):
                sc = values[0]
                if sc and not (sc.endswith('.SH') or sc.endswith('.SZ')):
                    values[0] = sc + '.SH' if sc.startswith(('6', '9')) else sc + '.SZ'
            rows.append(tuple(values))
    if rows:
        cur.executemany(sql, rows)
        conn.commit()
    return len(rows)


def cmd_import():
    """导入 data/daily_*.csv 到数据库 + 自动健康度检查"""
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    
    stats = defaultdict(int)
    
    # 找 CSV：data/ + logs/archive/ 目录下的当日文件
    archive_dir = os.path.join(os.path.dirname(DATA), 'logs', 'archive')
    search_dirs = [DATA]
    if os.path.exists(archive_dir):
        search_dirs.append(archive_dir)
    
    csv_files = {'daily': [], 'shnum': [], 'margin': []}
    for search_dir in search_dirs:
        for prefix, key in [('daily_', 'daily'), ('shnum_', 'shnum'), ('margin_', 'margin')]:
            for f in sorted(glob.glob(f"{search_dir}/{prefix}*_b*.csv")):
                basename = os.path.basename(f)
                if key == 'margin' and not re.search(r'margin_\d{8}_b\d+\.csv', basename):
                    continue
                if f not in csv_files[key]:
                    csv_files[key].append(f)
    
    # 日 K
    for f in csv_files['daily']:
        basename = os.path.basename(f)
        n = import_daily_csv(conn, f, 'daily')
        stats['daily'] += n
        log.info(f"  ✅ {basename}: {n} rows")
    
    # 股东户数
    for f in csv_files['shnum']:
        basename = os.path.basename(f)
        n = import_daily_csv(conn, f, 'shnum')
        stats['shnum'] += n
        log.info(f"  ✅ {basename}: {n} rows")
    
    # 融资融券
    for f in csv_files['margin']:
        basename = os.path.basename(f)
        n = import_daily_csv(conn, f, 'margin')
        stats['margin'] += n
        log.info(f"  ✅ {basename}: {n} rows")
    
    conn.close()
    
    log.info(f"\n=== 导入完成 ===")
    for k, v in stats.items():
        if k != 'health':
            log.info(f"  {k}: {v:,} rows")
    
    # 自动跑健康度检查
    log.info(f"\n{'='*60}")
    log.info("  导入后自动健康度检查")
    log.info('='*60)
    try:
        from health_check import main as health_main
        ret = health_main()
        stats['health'] = 'pass' if ret == 0 else 'fail'
    except Exception as e:
        log.info(f"  ⚠️ 健康度检查失败: {e}")
        stats['health'] = 'error'
    
    return stats


def cmd_report():
    """生成今日更新报告"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    log.info(f"=== 增量更新报告 ({TODAY}) ===\n")
    
    for table, col in [
        ('stock_daily', 'trade_date'),
        ('shareholder_num', 'end_date'),
        ('margin_trading', 'trade_date'),
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (TODAY,))
        n = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(DISTINCT stock_code) FROM {table} WHERE {col} = ?", (TODAY,))
        s = cur.fetchone()[0]
        log.info(f"  {table}: {n:,} 条 ({s} 票)")
    
    # 数据库总大小
    sz = os.path.getsize(DB)
    log.info(f"\n数据库大小: {sz/1024/1024:.2f} MB")
    
    # 各表总数
    log.info(f"\n=== 各表累计 ===")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    total = 0
    for t in tables:
        if t in ('sqlite_sequence', 'sync_log', 'metadata'):
            continue
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        c = cur.fetchone()[0]
        total += c
        log.info(f"  {t}: {c:,}")
    log.info(f"  TOTAL: {total:,}")
    
    conn.close()


def main():
    if len(sys.argv) < 2:
        cmd_check()
        log.info("\n用法: python3 daily_update.py [check|plan|import|report|health|all]")
        return
    
    cmd = sys.argv[1]
    if cmd == 'check':
        cmd_check()
    elif cmd == 'plan':
        cmd_plan()
    elif cmd == 'import':
        cmd_import()
    elif cmd == 'report':
        cmd_report()
    elif cmd == 'health':
        from health_check import main as health_main
        sys.exit(health_main())
    elif cmd == 'all':
        log.info("=== 步骤 1: 检查 ===")
        updated = cmd_check()
        log.info('')
        if updated:
            log.info("✅ 今天已更新，无需重复拉取")
            return
        
        log.info("=== 步骤 2: 拉取计划 ===")
        plan = cmd_plan()
        log.info('')
        log.info("=== 步骤 3: 请按 plan 调 API（详见 cron prompt）===")
    else:
        log.info(f"未知命令: {cmd}")
        log.info("用法: python3 daily_update.py [check|plan|import|report|health|all]")


if __name__ == "__main__":
    main()
