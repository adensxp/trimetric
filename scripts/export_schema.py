#!/usr/bin/env python3
"""导出四库 schema 基线（在部署机执行）：

    python3 scripts/export_schema.py > docs/schema.sql
"""
import sqlite3

import config

log = config.setup_logging("export_schema")


def dump(path, name):
    print("-- ========== %s: %s ==========" % (name, path))
    try:
        conn = sqlite3.connect(path)
        rows = conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
            "ORDER BY type DESC, name").fetchall()
        for (sql,) in rows:
            print(sql + ";")
        conn.close()
    except sqlite3.Error as e:
        print("-- ❌ %s 打开失败: %s" % (name, e))


if __name__ == "__main__":
    dump(config.MASTER_DB, "① master 总数据库")
    dump(config.CANDIDATES_DB, "② candidates 候选库")
    dump(config.POSITIONS_DB, "③ positions 持仓库")
    dump(config.CAPITAL_DB, "④ capital 资金库")
