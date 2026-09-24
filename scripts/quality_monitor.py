"""
高量战法 好公司变坏监控（修复版 - 用实际表字段）
- 每日扫描 494 只池子
- 对比今日 vs 30 日前评级
- 触发规则：
  - W19 健康度降级（A→C 减 50%，A→B 减 30%，B→C 减 30%）
  - W16 红旗新触发（之前没有 → 立即清）
  - W22 护城河丢失（>= 2 减 50%）
"""
import config
log = config.setup_logging("quality_monitor")
import sqlite3
import os
from datetime import datetime, timedelta

STOCK_DB = config.MASTER_DB
PORTFOLIO_DB = config.CANDIDATES_DB

def get_w19_grade(stock_code, cur_stock):
    """W19 健康度：基于现金流质量（OCF/收入比、净利、负债率、ROE、ROA）"""
    cur_stock.execute('''
        SELECT op_cash_flow, revenue, net_profit, total_liability, total_equity, total_assets
        FROM w19_18steps WHERE stock_code = ?
        ORDER BY year DESC LIMIT 1
    ''', (stock_code,))
    row = cur_stock.fetchone()
    if not row:
        return 'C', 0.0
    ocf, rev, np_v, tl, te, ta = row
    score = 0
    # 1. 净利为正
    if np_v and np_v > 0: score += 1
    # 2. 经营现金流为正
    if ocf and ocf > 0: score += 1
    # 3. OCF/收入比 > 8%
    if ocf and rev and rev > 0 and (ocf / rev) > 0.08: score += 1
    # 4. 资产负债率 < 60%
    if tl and ta and ta > 0 and (tl / ta) < 0.6: score += 1
    # 5. ROE > 10%（用 net_profit / equity）
    if np_v and te and te > 0 and (np_v / te) > 0.1: score += 1
    # 6. ROA > 5%
    if np_v and ta and ta > 0 and (np_v / ta) > 0.05: score += 1
    grade = 'A' if score >= 5 else ('B' if score >= 3 else 'C')
    return grade, score

def get_w22_moats(stock_code, cur_stock):
    """W22 护城河数"""
    cur_stock.execute('SELECT moat_count FROM stock_moats WHERE stock_code = ?', (stock_code,))
    row = cur_stock.fetchone()
    return row[0] if row else 0

def has_w16_redflag(stock_code, cur_stock):
    """W16 红旗 0/1"""
    cur_stock.execute('''
        SELECT r071_triggered, r072_triggered, r073_triggered, r074_triggered
        FROM fraud_redflags WHERE stock_code = ?
        ORDER BY year DESC LIMIT 1
    ''', (stock_code,))
    row = cur_stock.fetchone()
    if not row:
        return 0
    return 1 if any(row) else 0

def get_div_yield(stock_code, cur_stock):
    """股息率 %"""
    cur_stock.execute('SELECT dividend_ratio FROM stock_pe_ttm WHERE stock_code = ?', (stock_code,))
    row = cur_stock.fetchone()
    return row[0] if row and row[0] else 0

def get_pe_ttm(stock_code, cur_stock):
    cur_stock.execute('SELECT pe_ttm FROM stock_pe_ttm WHERE stock_code = ?', (stock_code,))
    row = cur_stock.fetchone()
    return row[0] if row and row[0] else 0

def get_roe_5y(stock_code, cur_stock):
    """5 年 ROE 平均 %（用 5 年 net_profit / equity 平均）"""
    cur_stock.execute('''
        SELECT net_profit, total_equity FROM w19_18steps
        WHERE stock_code = ? ORDER BY year DESC LIMIT 5
    ''', (stock_code,))
    rows = cur_stock.fetchall()
    if len(rows) < 3:
        return 0
    roes = [r[0] / r[1] * 100 for r in rows if r[0] and r[1] and r[1] > 0]
    return sum(roes) / len(roes) if roes else 0

def get_stock_name(stock_code, cur_stock):
    cur_stock.execute('SELECT stock_name FROM stock_info WHERE stock_code = ?', (stock_code,))
    row = cur_stock.fetchone()
    return row[0] if row else stock_code

def scan_quality_today(trade_date):
    """扫描今日 494 只 + 写入 daily_company_quality + 触发警报"""
    conn_stock = sqlite3.connect(STOCK_DB)
    cur_stock = conn_stock.cursor()
    conn_port = sqlite3.connect(PORTFOLIO_DB)
    cur_port = conn_port.cursor()

    cur_stock.execute('''
        SELECT s.stock_code, s.stock_name, s.industry
        FROM stock_info s
        WHERE s.industry IS NOT NULL
        ORDER BY s.industry, s.rank_in_industry
    ''')
    pool = cur_stock.fetchall()
    log.info(f"📊 高量战法 每日质量扫描 - {trade_date}")
    log.info(f"   股票池: {len(pool)} 只")

    alerts = []
    scan_count = 0
    degrade_stats = {'A_to_C': 0, 'A_to_B': 0, 'B_to_C': 0}

    for code, name, industry in pool:
        w19_grade, w19_score = get_w19_grade(code, cur_stock)
        w22_moats = get_w22_moats(code, cur_stock)
        redflag = has_w16_redflag(code, cur_stock)
        div_yield = get_div_yield(code, cur_stock)
        roe_5y = get_roe_5y(code, cur_stock)
        pe_ttm = get_pe_ttm(code, cur_stock)

        is_core = 1 if (
            w19_grade in ['A', 'B'] and w22_moats >= 2 and roe_5y >= 12 and div_yield >= 3
        ) else 0

        # 查 30 日前评级（在写入今日之前）
        cur_stock.execute('''
            SELECT w19_grade, w22_moats_count, w16_redflag, roe_5y
            FROM daily_company_quality
            WHERE stock_code = ? AND trade_date < ?
            ORDER BY trade_date DESC LIMIT 1
        ''', (code, trade_date))
        prev = cur_stock.fetchone()
        prev_w19 = prev[0] if prev else None
        prev_w22 = prev[1] if prev else None
        prev_rf = prev[2] if prev else None
        prev_roe = prev[3] if prev else None

        # 判断质量变化
        quality_change = 'stable'
        if prev_w19 and w19_grade:
            if prev_w19 == 'A' and w19_grade == 'C':
                quality_change = 'degrade_critical'
                degrade_stats['A_to_C'] += 1
            elif prev_w19 == 'A' and w19_grade == 'B':
                quality_change = 'degrade_mild'
                degrade_stats['A_to_B'] += 1
            elif prev_w19 == 'B' and w19_grade == 'C':
                quality_change = 'degrade_mild'
                degrade_stats['B_to_C'] += 1
            elif prev_w19 == 'C' and w19_grade == 'A':
                quality_change = 'upgrade'

        # 写入今日评级
        cur_stock.execute('''
            INSERT OR REPLACE INTO daily_company_quality
            (stock_code, trade_date, w19_grade, w19_score,
             w22_moats_count, w16_redflag, div_yield, roe_5y,
             is_core_holding, quality_change, prev_w19_grade, prev_w22_moats,
             data_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (code, trade_date, w19_grade, w19_score,
              w22_moats, redflag, div_yield, roe_5y,
              is_core, quality_change, prev_w19, prev_w22,
              '高量战法_daily_scan'))

        # 触发警报
        # 1. W19 严重降级（A→C）
        if quality_change == 'degrade_critical':
            alerts.append({
                'code': code, 'name': name, 'industry': industry,
                'type': 'w19_A_to_C',
                'severity': 'critical',
                'prev': f'W19=A',
                'curr': f'W19=C',
                'action': 'reduce_50_or_clear'
            })

        # 2. W19 轻度降级（A→B / B→C）
        elif quality_change == 'degrade_mild':
            alerts.append({
                'code': code, 'name': name, 'industry': industry,
                'type': 'w19_degrade_mild',
                'severity': 'high',
                'prev': f'W19={prev_w19}',
                'curr': f'W19={w19_grade}',
                'action': 'reduce_30'
            })

        # 3. W16 红旗新触发
        if redflag == 1 and prev_rf == 0:
            alerts.append({
                'code': code, 'name': name, 'industry': industry,
                'type': 'w16_new_redflag',
                'severity': 'critical',
                'prev': 'W16=0',
                'curr': 'W16=1',
                'action': 'clear_all_immediately'
            })

        # 4. W22 护城河丢失
        if prev_w22 is not None and (prev_w22 - w22_moats) >= 2:
            alerts.append({
                'code': code, 'name': name, 'industry': industry,
                'type': 'w22_moat_lost',
                'severity': 'high',
                'prev': f'W22={prev_w22}',
                'curr': f'W22={w22_moats}',
                'action': 'reduce_50'
            })

        # 5. 5 年 ROE 跌破 12%
        if prev_roe is not None and prev_roe >= 12 and roe_5y < 12:
            alerts.append({
                'code': code, 'name': name, 'industry': industry,
                'type': 'roe_break_below_12',
                'severity': 'medium',
                'prev': f'5y ROE={prev_roe:.1f}%',
                'curr': f'5y ROE={roe_5y:.1f}%',
                'action': 'reduce_core_holding'
            })

        scan_count += 1

    conn_stock.commit()
    conn_stock.close()

    # 写警报到 portfolio db
    for a in alerts:
        cur_port.execute('''
            INSERT INTO quality_alert_log
            (stock_code, stock_name, trade_date, alert_type, severity,
             prev_value, current_value, action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (a['code'], a['name'], trade_date, a['type'], a['severity'],
              a['prev'], a['curr'], a['action']))

    conn_port.commit()
    conn_port.close()

    log.info(f"✅ 扫描完成: {scan_count} 只")
    log.info(f"⚠️  质量降级统计: A→C {degrade_stats['A_to_C']} / A→B {degrade_stats['A_to_B']} / B→C {degrade_stats['B_to_C']}")
    log.info(f"🚨 总触发警报: {len(alerts)} 条")
    log.info('')
    if alerts:
        # 按严重度分组
        for sev in ['critical', 'high', 'medium']:
            sev_alerts = [a for a in alerts if a['severity'] == sev]
            if sev_alerts:
                log.info(f"  === {sev.upper()} ({len(sev_alerts)} 条) ===")
                for a in sev_alerts[:10]:
                    log.info(f"  {a['code']} {a['name']} ({a['industry']}): {a['type']}")
                    log.info(f"     {a['prev']} → {a['curr']}  操作: {a['action']}")

    return alerts

if __name__ == '__main__':
    import sys
    trade_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y-%m-%d')
    alerts = scan_quality_today(trade_date)
    log.info(f"\n📊 总计: {len(alerts)} 条警报")
