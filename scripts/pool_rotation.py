"""
高量战法 股票池年度轮替
- 沪深 300 半年调整（1 月 + 7 月）
- 申万 31 行业 Top 10 同步更新
- 流程：
  1. 拉最新沪深 300 + 申万 Top 10
  2. 对比新旧池子
  3. 持仓的票被剔除 → 预警 + 标记待清仓
  4. 备份旧池 + 替换 + 重跑分析
"""
import config
log = config.setup_logging("pool_rotation")
import sqlite3
import json
import os
import shutil
from datetime import datetime

STOCK_DB = config.MASTER_DB
POOL_FILE = config.STOCK_POOL_500
BACKUP_DIR = config.POOL_HISTORY_DIR

def fetch_hs300_constituents():
    """从恒生 MCP 拉最新沪深 300 成分股"""
    import subprocess
    import urllib.request
    log.info("📊 拉取最新沪深 300 成分股...")
    try:
        result = subprocess.run(
            ['mcode-tools', 'connector', 'call', 'connector__hengsheng__call_api',
             '--args', json.dumps({
                 'api_id': 'IndexConstituentStocks',
                 'params': {'indexObject': ['000300.SH'], 'pageSize': 500},
                 'format': 'json'
             })],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            log.info(f"⚠️ mcode-tools 失败: {result.stderr[:200]}")
            return None
        # 解析 mcp 返回的包装格式
        outer = json.loads(result.stdout)
        if isinstance(outer, list) and outer and 'text' in outer[0]:
            inner = json.loads(outer[0]['text'])
        elif isinstance(outer, dict):
            inner = outer
        else:
            inner = {}

        if not isinstance(inner, dict):
            log.info(f"⚠️ 数据格式异常: {type(inner)}")
            return None

        # 如果 truncated → 用 download 链接拉完整数据
        if inner.get('truncated_inline') and 'download' in inner:
            url = inner['download']['url']
            log.info(f"   preview {inner.get('preview_row_count')}/{inner.get('total_count')} → 下载完整数据...")
            with urllib.request.urlopen(url, timeout=120) as resp:
                full_data = json.loads(resp.read().decode('utf-8'))
            rows = full_data.get('rows', [])
        elif 'data' in inner and isinstance(inner['data'], dict) and 'rows' in inner['data']:
            rows = inner['data']['rows']
        else:
            log.info(f"⚠️ 找不到 rows")
            return None

        codes = []
        for r in rows:
            c = r.get('stockcode')
            if c:
                # 后缀化
                if '.' not in c:
                    if c.startswith(('60', '68', '90')):
                        c = f'{c}.SH'
                    else:
                        c = f'{c}.SZ'
                codes.append(c)
        log.info(f"✅ 沪深 300 拉取: {len(codes)} 只")
        return codes
    except Exception as e:
        log.info(f"⚠️ 恒生 MCP 拉取失败: {e}")
        import traceback
        traceback.print_exc()
        return None

def fetch_industry_top10(industry_name, top_n=10):
    """从 stock_info 拉某行业 Top N"""
    conn = sqlite3.connect(STOCK_DB)
    cur = conn.cursor()
    cur.execute('''
    SELECT stock_code FROM stock_info
    WHERE industry = ?
    ORDER BY rank_in_industry ASC
    LIMIT ?
    ''', (industry_name, top_n))
    codes = [r[0] for r in cur.fetchall()]
    conn.close()
    return codes

def load_current_pool():
    """读当前股票池"""
    with open(POOL_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    # 提取所有代码（batches 结构）
    if 'batches' in data:
        codes = []
        for batch in data['batches'].values():
            for s in batch:
                codes.append(s['code'])
        return codes
    return data if isinstance(data, list) else []

def save_pool(codes, version_note=''):
    """保存新股票池（保持 batches 结构）"""
    # 从 stock_info 拿名字
    conn = sqlite3.connect(STOCK_DB)
    cur = conn.cursor()
    name_map = {}
    cur.execute('SELECT stock_code, stock_name, industry, rank_in_industry FROM stock_info')
    for c, n, i, r in cur.fetchall():
        name_map[c] = {'name': n, 'industry': i, 'rank': r}
    conn.close()

    # 分 5 个 batch
    batch_size = 100
    batches = {}
    for i in range(0, len(codes), batch_size):
        batch_num = i // batch_size + 1
        batch = []
        for code in codes[i:i+batch_size]:
            info = name_map.get(code, {'name': code, 'industry': '', 'rank': 0})
            batch.append({
                'code': code,
                'name': info['name'],
                'industry': info['industry'],
                'rank': info['rank']
            })
        batches[f'batch_{batch_num}'] = batch

    data = {
        'batches': batches,
        'total': len(codes),
        'source': '高量战法_pool_rotation',
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    if version_note:
        data['note'] = version_note

    with open(POOL_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"✅ 股票池保存: {len(codes)} 只（{len(batches)} 个 batch）")

def save_pool_old(pool_data, version_note=''):
    """保存新股票池（保持原结构 - 备份用）"""
    pass  # 已替换为新版本

def backup_old_pool(version_note=''):
    """备份旧股票池到历史目录"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = os.path.join(BACKUP_DIR, f'pool_{timestamp}.json')
    if os.path.exists(POOL_FILE):
        shutil.copy2(POOL_FILE, backup_file)
        log.info(f"📦 旧股票池备份: {backup_file}")
    return backup_file

def diff_pool(old_pool, new_pool):
    """对比新旧池子，返回 removed/added/kept"""
    old_set = set(old_pool)
    new_set = set(new_pool)
    removed = list(old_set - new_set)  # 被剔除
    added = list(new_set - old_set)    # 新加入
    kept = list(old_set & new_set)     # 保留
    return removed, added, kept

def check_holdings_impact(removed_codes):
    """检查被剔除的票是否在持仓中"""
    conn = sqlite3.connect(config.POSITIONS_DB)
    cur = conn.cursor()
    cur.execute('''
    SELECT code, name, shares, cost_basis, strategy_type, entry_date, t30_deadline
    FROM positions WHERE active=1
    ''')
    holdings = cur.fetchall()
    conn.close()

    impact = []
    for code, name, shares, cost, strategy, entry, t30 in holdings:
        if code in removed_codes:
            impact.append({
                'code': code, 'name': name, 'shares': shares,
                'cost': cost, 'strategy': strategy, 'entry': entry, 't30': t30
            })
    return impact

def mark_position_for_clear(impact):
    """标记被剔除的持仓为'待清仓'"""
    if not impact:
        return
    conn = sqlite3.connect(config.CANDIDATES_DB)
    cur = conn.cursor()
    # 加一个 quality_alert_log 记录
    for item in impact:
        cur.execute('''
        INSERT INTO quality_alert_log
        (stock_code, stock_name, trade_date, alert_type, severity,
         prev_value, current_value, action)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (item['code'], item['name'], datetime.now().strftime('%Y-%m-%d'),
              'pool_rotation_removed', 'critical',
              f'在池', '被剔除',
              'clear_or_reduce_in_3_days'))
    conn.commit()
    conn.close()

def update_stock_info(new_pool):
    """更新 stock_info 表（删旧 + 加新）"""
    conn = sqlite3.connect(STOCK_DB)
    cur = conn.cursor()
    cur.execute('SELECT stock_code FROM stock_info')
    existing = {r[0] for r in cur.fetchall()}

    new_set = set(new_pool)
    # 删除不在新池的
    deleted = 0
    for code in existing:
        if code not in new_set:
            cur.execute('DELETE FROM stock_info WHERE stock_code = ?', (code,))
            deleted += 1
    # 新池中的代码：保留已有，新增的标记待拉
    added_count = sum(1 for code in new_pool if code not in existing)
    conn.commit()
    conn.close()
    log.info(f"✅ stock_info 更新: 删 {deleted} + 保留 {len(new_set) - added_count} + 新增待拉 {added_count}")

def rotate_pool(new_hs300=None, dry_run=False):
    """主轮替流程"""
    log.info("=" * 60)
    log.info(f"🔄 高量战法 股票池年度轮替 - {datetime.now().strftime('%Y-%m-%d')}")
    log.info("=" * 60)

    # 1. 读旧池
    old_pool = load_current_pool()
    log.info(f"📊 旧股票池: {len(old_pool)} 只")

    # 2. 拉新沪深 300（如果没传）
    if new_hs300 is None:
        new_hs300 = fetch_hs300_constituents()
    if not new_hs300:
        log.info("❌ 拉取沪深 300 失败，退出")
        return None

    # 3. 拉申万 31 行业 Top 10（用 stock_info 表里的行业名）
    conn_stock = sqlite3.connect(STOCK_DB)
    cur_stock = conn_stock.cursor()
    cur_stock.execute('SELECT DISTINCT industry FROM stock_info ORDER BY industry')
    industries = [r[0] for r in cur_stock.fetchall() if r[0]]
    conn_stock.close()
    log.info(f"📊 读取到 {len(industries)} 个申万行业")

    new_pool_set = set(new_hs300)
    for ind in industries:
        top10 = fetch_industry_top10(ind, 10)
        new_pool_set.update(top10)

    new_pool = list(new_pool_set)
    log.info(f"📊 新股票池: {len(new_pool)} 只 (沪深 300 {len(new_hs300)} + 行业 Top 10)")

    # 4. 对比
    removed, added, kept = diff_pool(old_pool, new_pool)
    log.info(f"   📉 被剔除: {len(removed)} 只")
    log.info(f"   📈 新加入: {len(added)} 只")
    log.info(f"   ✅ 保留: {len(kept)} 只")

    if removed:
        log.info(f"\n📉 被剔除的票:")
        for code in removed[:20]:
            log.info(f"   - {code}")
        if len(removed) > 20:
            log.info(f"   ... 还有 {len(removed) - 20} 只")

    if added:
        log.info(f"\n📈 新加入的票:")
        for code in added[:20]:
            log.info(f"   + {code}")
        if len(added) > 20:
            log.info(f"   ... 还有 {len(added) - 20} 只")

    # 5. 检查持仓影响
    impact = check_holdings_impact(removed)
    if impact:
        log.info(f"\n🚨 持仓影响: {len(impact)} 只持仓票被剔除")
        for item in impact:
            log.info(f"   ⚠️ {item['code']} {item['name']} ({item['strategy']}, 持仓 {item['shares']} 股 @ {item['cost']:.2f})")
        log.info(f"   建议: 3 个交易日内清仓")

    # 6. dry run 检查
    if dry_run:
        log.info("\n[DRY RUN] 不会实际修改文件")
        return {
            'old_count': len(old_pool),
            'new_count': len(new_pool),
            'removed': removed,
            'added': added,
            'kept': kept,
            'holdings_impact': impact
        }

    # 7. 备份 + 替换
    backup_file = backup_old_pool()
    save_pool(new_pool)

    # 8. 标记持仓
    if impact:
        mark_position_for_clear(impact)

    # 9. 更新 stock_info
    update_stock_info(new_pool)

    log.info(f"\n✅ 轮替完成: {len(old_pool)} → {len(new_pool)} 只")
    return {
        'backup_file': backup_file,
        'old_count': len(old_pool),
        'new_count': len(new_pool),
        'removed': removed,
        'added': added,
        'kept': kept,
        'holdings_impact': impact
    }

if __name__ == '__main__':
    import sys
    dry_run = '--dry-run' in sys.argv
    result = rotate_pool(dry_run=dry_run)
    if result:
        log.info(f"\n📊 总结: 旧 {result['old_count']} → 新 {result['new_count']}")
        log.info(f"   剔除 {len(result['removed'])} / 新增 {len(result['added'])} / 保留 {len(result['kept'])}")
        if result.get('holdings_impact'):
            log.info(f"   🚨 持仓影响 {len(result['holdings_impact'])} 只")
