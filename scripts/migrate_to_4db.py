#!/usr/bin/env python3
"""老库 → 四库迁移（部署机一次性执行）

用法（输出跟随 GAOLIANG_* 环境变量）：
  python3 scripts/migrate_to_4db.py
  python3 scripts/migrate_to_4db.py --legacy-stock /old/stock_data.db --legacy-portfolio /old/portfolio.db --force

策略：master ← 老行情库整库复制；candidates/positions/capital ← 老持仓库按表拆分。
老文件不动（留档）；BSC 快照库退役（从未有更新链路，零损失）。
"""
import argparse
import os
import sqlite3

import config

log = config.setup_logging("migrate_4db")

CANDIDATES_TABLES = ["daily_pool_scores", "daily_prep_pool", "crown_jewels", "quality_alert_log", "watchlist"]
POSITIONS_TABLES = ["positions", "trades", "events", "daily_pnl"]
CAPITAL_TABLES = ["cash_flow", "meta", "position_rules"]


def copy_whole(src_path, dest_path):
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest_path)
    src.backup(dst)
    n = dst.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    dst.close()
    src.close()
    return n


def copy_tables(src_path, dest_path, tables, label):
    dest = sqlite3.connect(dest_path)
    dest.execute("ATTACH DATABASE ? AS src", (src_path,))
    copied, skipped = [], []
    for t in tables:
        row = dest.execute("SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
        if not row:
            skipped.append(t)
            continue
        dest.execute(row[0])
        dest.execute('INSERT INTO "%s" SELECT * FROM src."%s"' % (t, t))
        n = dest.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        copied.append("%s(%d行)" % (t, n))
    dest.commit()
    dest.execute("DETACH src")
    dest.close()
    log.info("✅ %s ← 老持仓库: %s%s", label, ", ".join(copied) or "无",
             ("；源库缺表跳过: " + ",".join(skipped)) if skipped else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy-stock", default="/workspace/stock_db/stock_data.db", help="老行情库")
    ap.add_argument("--legacy-portfolio", default="/workspace/stock_db/portfolio.db", help="老持仓库")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的目标库")
    args = ap.parse_args()

    for t in [config.MASTER_DB, config.CANDIDATES_DB, config.POSITIONS_DB, config.CAPITAL_DB]:
        if os.path.exists(t):
            if not args.force:
                log.warning("目标已存在 %s（用 --force 覆盖）", t)
                return
            os.remove(t)

    n = copy_whole(args.legacy_stock, config.MASTER_DB)
    log.info("✅ master.db ← %s 整库复制（%d 张表）", args.legacy_stock, n)
    copy_tables(args.legacy_portfolio, config.CANDIDATES_DB, CANDIDATES_TABLES, "candidates.db")
    copy_tables(args.legacy_portfolio, config.POSITIONS_DB, POSITIONS_TABLES, "positions.db")
    copy_tables(args.legacy_portfolio, config.CAPITAL_DB, CAPITAL_TABLES, "capital.db")
    log.info("迁移完成。老文件未动（留档）；BSC 快照库退役。")
    log.info("验证：python3 scripts/monitor.py && python3 tests/test_rules.py")


if __name__ == "__main__":
    main()
