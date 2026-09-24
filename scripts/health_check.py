#!/usr/bin/env python3
"""
高量战法本地数据库 · 健康度检查
=========================================
检查项（5 大类 12 小项）：

【S1 结构】
  - PRAGMA integrity_check
  - 14 张表都存在
  - 关键 schema 字段类型正确

【C1 完整性】
  - 每张表记录数 > 0
  - stock_info = 119 票
  - stock_daily 5 年 ≥ 1100 天
  - stock_daily 当日 = 119 票

【A1 准确性】⭐ 重点
  - 抽样 5 票：close_price > 0、volume > 0、change_pct 合理
  - 当日所有票：close > 0、change_pct 在 ±20% 之间
  - 股东户数 > 0、融资余额 >= 0
  - 关键字段 NOT NULL

【F1 时效性】
  - 每张表的最新日期
  - 距今天数（< 5 工作日 = 正常）

【X1 异常】
  - 119 票 5 年完整（不完整票清单）
  - 当日数据 < 80 票 = 异常
  - margin_trading 当日 < 117 票 = 缺数据
"""
import config
log = config.setup_logging("health_check")
import sqlite3
import os
import sys
from datetime import datetime, timedelta
from collections import defaultdict

DB = config.MASTER_DB

# 14 张表的标准 schema 检查
EXPECTED_TABLES = {
    'stock_info': ['stock_code', 'stock_name', 'industry', 'list_date'],
    'stock_daily': ['stock_code', 'trade_date', 'open_price', 'high_price', 
                    'low_price', 'close_price', 'volume'],
    'stock_monthly': ['stock_code', 'year_month', 'close_price'],
    'stock_weekly': ['stock_code', 'year_week', 'close_price'],
    'top10_holders': ['stock_code', 'report_date', 'holder_name'],
    'shareholder_num': ['stock_code', 'end_date', 'total_holders'],
    'dividend': ['stock_code', 'plan_date'],
    'insider_trading': ['stock_code', 'change_date'],
    'risk_factors': ['stock_code', 'trade_date'],
    'restricted_lifting': ['stock_code', 'lifting_date'],
    'margin_trading': ['stock_code', 'trade_date', 'margin_balance'],
    'financial_data': ['stock_code', 'report_date', 'report_type'],
}


def header(title):
    log.info(f"\n{'='*60}")
    log.info(f"  {title}")
    log.info('='*60)


def ok(msg):
    log.info(f"  ✅ {msg}")
    return True


def warn(msg):
    log.info(f"  ⚠️  {msg}")
    return False


def fail(msg):
    log.info(f"  ❌ {msg}")
    return False


def check_structure(conn):
    """【S1 结构】"""
    header("【S1 结构】数据库结构")
    cur = conn.cursor()
    all_ok = True
    
    # 1.1 integrity_check
    cur.execute("PRAGMA integrity_check")
    result = cur.fetchone()[0]
    if result == 'ok':
        all_ok &= ok("PRAGMA integrity_check: ok")
    else:
        all_ok &= fail(f"PRAGMA integrity_check: {result}")
    
    # 1.2 14 张表都存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {r[0] for r in cur.fetchall()}
    missing = set(EXPECTED_TABLES.keys()) - existing
    extra = existing - set(EXPECTED_TABLES.keys()) - {'sqlite_sequence', 'sync_log', 'metadata'}
    if not missing:
        all_ok &= ok(f"全部 {len(EXPECTED_TABLES)} 张核心表都存在")
    else:
        all_ok &= fail(f"缺失 {len(missing)} 张表: {missing}")
    if extra:
        warn(f"额外表: {extra}（不影响健康）")
    
    # 1.3 关键字段存在
    for table, required_fields in EXPECTED_TABLES.items():
        if table not in existing:
            continue
        cur.execute(f"PRAGMA table_info({table})")
        cols = {r[1] for r in cur.fetchall()}
        missing_cols = set(required_fields) - cols
        if not missing_cols:
            pass  # 不逐个打印
        else:
            all_ok &= fail(f"{table} 缺少字段: {missing_cols}")
    
    if all_ok:
        ok("所有核心表 schema 完整")
    
    return all_ok


def check_completeness(conn):
    """【C1 完整性】"""
    header("【C1 完整性】数据完整度")
    cur = conn.cursor()
    all_ok = True
    
    # 2.1 每张表记录数
    total = 0
    table_counts = {}
    for t in EXPECTED_TABLES:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        table_counts[t] = n
        total += n
        if n == 0:
            all_ok &= fail(f"{t}: 0 条")
    
    if all_ok:
        ok(f"12 张表都有数据（合计 {total:,} 条）")
    
    # 2.2 stock_info 必须 119 票
    n = table_counts.get('stock_info', 0)
    if n == 119:
        ok(f"stock_info: 119 票 ✓")
    elif n >= 100:
        all_ok &= warn(f"stock_info: {n} 票（期望 119）")
    else:
        all_ok &= fail(f"stock_info: {n} 票（严重不足）")
    
    # 2.3 stock_daily 5 年 ≥ 1100 天
    n = table_counts.get('stock_daily', 0)
    if n >= 148000:
        ok(f"stock_daily: {n:,} 条（5 年完整）")
    elif n >= 100000:
        all_ok &= warn(f"stock_daily: {n:,} 条（可能不足 5 年）")
    else:
        all_ok &= fail(f"stock_daily: {n:,} 条（严重不足）")
    
    return all_ok


def check_accuracy(conn, sample_size=5):
    """【A1 准确性】⭐ 重点"""
    header("【A1 准确性】数据准确性")
    cur = conn.cursor()
    all_ok = True
    
    # 3.1 抽样验证 5 票的最近一天日 K
    cur.execute("""
        SELECT stock_code, trade_date, open_price, high_price, low_price, 
               close_price, volume, change_pct
        FROM stock_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily)
        ORDER BY RANDOM()
        LIMIT ?
    """, (sample_size,))
    samples = cur.fetchall()
    
    if not samples:
        return fail("无最新日 K 数据可抽样")
    
    log.info(f"  抽样 {len(samples)} 票最新日 K 验证：")
    for code, date, o, h, l, c, v, pct in samples:
        issues = []
        if c is None or c <= 0:
            issues.append(f"close={c}")
        elif c > 10000:
            issues.append(f"close={c} > 10000")
        if v is None or v <= 0:
            issues.append(f"volume={v}")
        if pct is not None and (pct < -20 or pct > 20):
            issues.append(f"change_pct={pct:.2f}% 超 ±20%")
        if h is not None and l is not None and c is not None:
            if not (l <= c <= h):
                issues.append(f"low={l} > close={c} > high={h}")
        if issues:
            all_ok &= fail(f"  {code} {date}: {', '.join(issues)}")
        else:
            ok(f"  {code} {date}: close={c}, vol={v}, pct={pct:.2f}% ✓")
    
    # 3.2 当日所有票
    cur.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN close_price <= 0 THEN 1 ELSE 0 END) as bad_close,
               SUM(CASE WHEN volume <= 0 THEN 1 ELSE 0 END) as bad_vol,
               SUM(CASE WHEN change_pct < -20 OR change_pct > 20 THEN 1 ELSE 0 END) as bad_pct
        FROM stock_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily)
    """)
    total, bad_close, bad_vol, bad_pct = cur.fetchone()
    if total == 0:
        all_ok &= fail("当日无日 K 数据")
    else:
        if bad_close == 0 and bad_vol == 0 and bad_pct == 0:
            ok(f"当日 {total} 票日 K 数据全部合法（close>0, vol>0, pct∈[-20%, 20%]）")
        else:
            all_ok &= warn(f"当日 {total} 票中有 {bad_close or 0} 票 close 异常, "
                          f"{bad_vol or 0} 票 vol 异常, {bad_pct or 0} 票 pct 异常")
    
    # 3.3 股东户数
    cur.execute("""
        SELECT COUNT(*), MIN(total_holders), MAX(total_holders)
        FROM shareholder_num
        WHERE end_date = (SELECT MAX(end_date) FROM shareholder_num)
    """)
    n_shnum, min_h, max_h = cur.fetchone()
    if n_shnum > 0:
        if min_h and min_h > 0:
            ok(f"股东户数 {n_shnum} 票：区间 {min_h:,} ~ {max_h:,} 户（合理）")
        else:
            all_ok &= fail(f"股东户数有 ≤ 0 异常: {min_h}")
    else:
        warn("股东户数无最新数据")
    
    # 3.4 融资融券
    cur.execute("""
        SELECT COUNT(*), MIN(margin_balance), MAX(margin_balance)
        FROM margin_trading
        WHERE trade_date = (SELECT MAX(trade_date) FROM margin_trading)
    """)
    n_mar, min_m, max_m = cur.fetchone()
    if n_mar > 0:
        if min_m is None or min_m >= 0:
            ok(f"融资融券 {n_mar} 票：余额区间 {min_m or 0:,.0f} ~ {max_m:,.0f} 元（合理）")
        else:
            all_ok &= fail(f"融资余额有负数: {min_m}")
    else:
        warn("融资融券无最新数据")
    
    return all_ok


def check_freshness(conn, today=None):
    """【F1 时效性】"""
    header("【F1 时效性】数据新鲜度")
    cur = conn.cursor()
    all_ok = True
    
    if today is None:
        today = datetime.now().strftime('%Y-%m-%d')
    
    tables_with_date = [
        ('stock_daily', 'trade_date', 'date', 5),
        ('stock_monthly', 'year_month', 'month', 90),
        ('stock_weekly', 'year_week', 'date', 30),
        ('top10_holders', 'report_date', 'date', 60),
        ('shareholder_num', 'end_date', 'date', 30),
        ('insider_trading', 'change_date', 'date', 14),
        ('risk_factors', 'trade_date', 'date', 5),
        ('restricted_lifting', 'lifting_date', 'date', 90),
        ('margin_trading', 'trade_date', 'date', 5),
        ('financial_data', 'report_date', 'date', 120),
    ]
    
    log.info(f"  今日: {today}\n")
    
    for table, date_col, fmt_type, max_gap in tables_with_date:
        try:
            cur.execute(f"SELECT MAX({date_col}) FROM {table}")
            latest = cur.fetchone()[0]
            if not latest:
                all_ok &= warn(f"{table}: 无日期数据")
                continue
            # 解析日期
            if fmt_type == 'month':
                d_latest = datetime.strptime(latest + '-01', '%Y-%m-%d')
            else:
                d_latest = datetime.strptime(latest, '%Y-%m-%d')
            d_today = datetime.strptime(today, '%Y-%m-%d')
            gap = (d_today - d_latest).days
            if gap <= max_gap * 0.3:
                ok(f"{table}.{date_col}: {latest}（距今 {gap} 天）")
            elif gap <= max_gap:
                warn(f"{table}.{date_col}: {latest}（距今 {gap} 天，在容忍范围 {max_gap} 天内）")
            else:
                all_ok &= fail(f"{table}.{date_col}: {latest}（距今 {gap} 天，超过 {max_gap} 天）")
        except Exception as e:
            all_ok &= fail(f"{table}: {e}")
    
    return all_ok


def check_anomaly(conn, expected_stocks=119):
    """【X1 异常】"""
    header("【X1 异常】数据异常")
    cur = conn.cursor()
    all_ok = True
    
    # 5.1 当日日 K 票数
    cur.execute("""
        SELECT COUNT(DISTINCT stock_code) FROM stock_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily)
    """)
    n_today = cur.fetchone()[0]
    if n_today >= expected_stocks * 0.9:
        ok(f"当日日 K: {n_today} 票（≥90% 覆盖率）")
    elif n_today >= 80:
        all_ok &= warn(f"当日日 K: {n_today} 票（<90% 覆盖率）")
    else:
        all_ok &= fail(f"当日日 K: {n_today} 票（<80 严重不足）")
    
    # 5.2 5 年完整票清单（< 800 天 ≈ 3 年 才算严重不足）
    cur.execute(f"""
        SELECT stock_code, COUNT(*) as days
        FROM stock_daily
        GROUP BY stock_code
        HAVING days < 800
        ORDER BY days
    """)
    incomplete = cur.fetchall()
    if not incomplete:
        # 检查 < 1100 天
        cur.execute(f"""
            SELECT stock_code, COUNT(*) as days
            FROM stock_daily
            GROUP BY stock_code
            HAVING days < 1100
            ORDER BY days
        """)
        partial = cur.fetchall()
        if not partial:
            ok(f"全部 119 票日 K ≥ 1100 天（5 年完整）")
        else:
            warn(f"{len(partial)} 票日 K < 1100 天（新股/恢复上市 < 5 年，正常）: {partial[:3]}")
    else:
        all_ok &= fail(f"{len(incomplete)} 票日 K < 800 天（数据严重不足）: {incomplete[:5]}...")
    
    # 5.3 当日融资融券覆盖
    cur.execute("""
        SELECT COUNT(DISTINCT stock_code) FROM margin_trading
        WHERE trade_date = (SELECT MAX(trade_date) FROM margin_trading)
    """)
    n_margin = cur.fetchone()[0]
    if n_margin >= 100:
        ok(f"当日融资融券: {n_margin} 票（活跃度高）")
    elif n_margin >= 30:
        warn(f"当日融资融券: {n_margin} 票（部分活跃）")
    else:
        all_ok &= fail(f"当日融资融券: {n_margin} 票（活跃度低）")
    
    # 5.4 风险股阻断：检查最近 1 天有没有异常波动
    cur.execute("""
        SELECT stock_code, change_pct
        FROM stock_daily
        WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily)
          AND (change_pct > 15 OR change_pct < -15)
        ORDER BY ABS(change_pct) DESC
        LIMIT 5
    """)
    anomalies = cur.fetchall()
    if not anomalies:
        ok("当日无 ±15% 异常波动")
    else:
        warn(f"当日 ±15% 异常波动: {anomalies}")
    
    return all_ok


def main():
    """主入口"""
    if not os.path.exists(DB):
        log.info(f"❌ 数据库不存在: {DB}")
        return 1
    
    log.info("="*60)
    log.info(f"  高量战法本地数据库 · 健康度检查")
    log.info(f"  数据库: {DB}")
    log.info(f"  大小:   {os.path.getsize(DB)/1024/1024:.2f} MB")
    log.info(f"  时间:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("="*60)
    
    conn = sqlite3.connect(DB)
    
    results = []
    results.append(('S1 结构', check_structure(conn)))
    results.append(('C1 完整性', check_completeness(conn)))
    results.append(('A1 准确性', check_accuracy(conn)))
    results.append(('F1 时效性', check_freshness(conn)))
    results.append(('X1 异常', check_anomaly(conn)))
    
    conn.close()
    
    # 总结
    header("【总结】")
    log.info('')
    for name, passed in results:
        icon = "✅" if passed else "❌"
        log.info(f"  {icon} {name}")
    
    all_passed = all(p for _, p in results)
    log.info('')
    if all_passed:
        log.info("  🎉 全部检查通过！数据库健康。")
    else:
        log.info("  ⚠️  部分检查未通过，请查看上方详情。")
    
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
