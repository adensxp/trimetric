#!/usr/bin/env python3
"""行情数据初始化/刷新

用法：
  python3 scripts/fetch_data.py --codes 600519.SH,002475.SZ
  python3 scripts/fetch_data.py --limit 494                     # 从仓库池文件全量

数据源：K 线双源（腾讯 → 东方财富自动降级，字段序一致）+ PE 走腾讯行情。
鲁棒性：fetch 自带重试；腾讯 K 线连续 10 败熔断；PE 拉取失败不影响 K 线入库
（置空并在成功时才覆盖，失败保留旧值，可重跑后补）。
幂等：同代码重复拉取先清旧再写。--sleep 限速默认 1.0s（防 IP 封禁）。
"""
import argparse
import json
import re
import sqlite3
import time
import urllib.request
from datetime import datetime

import config

log = config.setup_logging("fetch_data")


def fetch(url, retries=2):
    """带重试的 HTTP GET（应对偶发断连，本机网络抖动时尤其重要）"""
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read()
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(2)
    raise last


def qcode_of(code):
    """'600519.SH' -> 'sh600519'；裸 6 位自动推断市场"""
    if '.' in code:
        sym, mkt = code.split('.')
        return ('sh' if mkt.upper() == 'SH' else 'sz') + sym
    return ('sh' if code.startswith('6') else 'sz') + code


def fetch_kline_em(code, days):
    """东方财富日 K（前复权）。返回 [date, open, close, high, low, volume] —— 与腾讯同序。"""
    sym = code[:6]
    if '.' in code:
        secid = ('1.' if code.upper().endswith('.SH') else '0.') + sym
    else:
        secid = ('1.' if sym.startswith('6') else '0.') + sym
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=' + secid +
           '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56'
           '&klt=101&fqt=1&end=20500101&lmt=' + str(days))
    kl = json.loads(fetch(url).decode('utf-8'))
    return [a.split(',') for a in kl['data']['klines']]


def load_codes(args):
    if args.codes:
        return [c.strip() for c in args.codes.split(',') if c.strip()]
    with open(args.pool) as f:
        data = json.load(f)
    codes = []
    batches = data.get('batches', {})
    seq = batches.values() if isinstance(batches, dict) else batches
    for b in seq:
        for item in b:
            # 池文件条目为 {'code': '600519.SH', 'name': '贵州茅台'} 对象（兼容纯字符串）
            codes.append(item['code'] if isinstance(item, dict) else item)
    return codes[:args.limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--codes', help='逗号分隔，如 600519.SH,002475.SZ')
    ap.add_argument('--pool', default=config.STOCK_POOL_500, help='股票池 JSON')
    ap.add_argument('--limit', type=int, default=30, help='从池中取前 N 只')
    ap.add_argument('--days', type=int, default=80, help='K 线天数')
    ap.add_argument('--sleep', type=float, default=1.0, help='限速间隔秒（防 IP 封禁，全量建议 >=1.0）')
    args = ap.parse_args()

    codes = load_codes(args)
    if not codes:
        log.warning('未获取到任何代码')
        return
    log.info('开始拉取 %d 只（K线双源+重试，限速 %s 秒）', len(codes), args.sleep)

    conn = sqlite3.connect(config.STOCK_DB)
    tencent_kline_fail = 0  # 熔断计数：连续 10 次失败后本次运行不再尝试腾讯 K 线
    ok = pe_ok = 0
    today = datetime.now().strftime('%Y-%m-%d')
    for code in codes:
        qc = qcode_of(code)
        # —— K 线（核心数据，双源）——
        rows = None
        if tencent_kline_fail < 10:
            try:
                kl = json.loads(fetch(
                    f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={qc},day,,,{args.days},qfq',
                    retries=1).decode('utf-8'))
                rows = kl['data'][qc].get('qfqday') or kl['data'][qc]['day']
                tencent_kline_fail = 0
            except Exception:
                tencent_kline_fail += 1
                if tencent_kline_fail == 10:
                    log.warning('腾讯 K 线连续 10 次失败，本次运行余下全部走东方财富')
        if rows is None:
            try:
                rows = fetch_kline_em(code, args.days)
            except Exception as e:
                log.warning('❌ %s K线双源均失败: %s', code, e)
                time.sleep(args.sleep)
                continue
        # —— PE/名称（增强数据，失败容忍，不覆盖旧值）——
        pe_ttm = name = price_now = None
        try:
            quote = fetch(f'http://qt.gtimg.cn/q={qc}', retries=1).decode('gbk', errors='ignore')
            parts = re.search(r'"([^"]+)"', quote).group(1).split('~')
            pe_ttm, name, price_now = float(parts[39]), parts[1], parts[3]
        except Exception:
            pass
        conn.execute('DELETE FROM stock_daily WHERE stock_code=?', (code,))
        # 字段序：date, open, close, high, low, volume
        conn.executemany('INSERT INTO stock_daily VALUES (?,?,?,?,?,?,?)',
                         [(a[0], code, float(a[1]), float(a[3]), float(a[4]), float(a[2]), float(a[5])) for a in rows])
        if pe_ttm is not None:
            conn.execute('INSERT OR REPLACE INTO stock_pe_ttm VALUES (?,?,?,?,?)',
                         (code, pe_ttm, today, None, today))
        conn.commit()
        ok += 1
        if pe_ttm is not None:
            pe_ok += 1
        log.info('✅ %s %s: %d 日K线 · 现价 %s · PE(TTM) %s',
                 code, name or code, len(rows), price_now or '-',
                 pe_ttm if pe_ttm is not None else '缺失(可后补)')
        time.sleep(args.sleep)
    conn.close()
    log.info('完成：K线 %d/%d · PE %d/%d（PE 缺失可重跑后补）', ok, len(codes), pe_ok, len(codes))


if __name__ == '__main__':
    main()
