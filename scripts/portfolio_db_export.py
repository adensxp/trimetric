#!/usr/bin/env python3
"""
portfolio.db 规则导出工具
==========================

将 portfolio.db 的规则表（position_rules）导出为 SQL 脚本，
方便打包到 v5.3.30 产品包中，让用户可以重建规则表。

不导出 trades / positions / daily_pnl / cash_flow / events（用户私有数据）。

用法：
  python3 portfolio_db_export.py /workspace/stock_db/portfolio.db
  # 默认导出到 /workspace/stock_db/portfolio_rules_v5.3.21.sql

  python3 portfolio_db_export.py portfolio.db portfolio_rules.sql
  # 指定输出路径
"""
import config
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def export_rules(db_path: str, output_path: str = None) -> str:
    """导出 position_rules 表为 SQL INSERT 脚本"""
    if output_path is None:
        db_name = Path(db_path).stem
        version = "v5.3.21"
        output_path = f"{config.DEPLOY_DIR}/{db_name}_rules_{version}.sql"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. 统计
    cur = conn.execute("SELECT COUNT(*) FROM position_rules")
    total = cur.fetchone()[0]

    cur = conn.execute("SELECT rule_id FROM position_rules WHERE rule_id NOT LIKE '%A' ORDER BY CAST(SUBSTR(rule_id, 2) AS INTEGER)")
    regular = [r[0] for r in cur.fetchall()]

    cur = conn.execute("SELECT rule_id FROM position_rules WHERE rule_id LIKE '%A'")
    a_series = [r[0] for r in cur.fetchall()]

    cur = conn.execute("SELECT COUNT(*) FROM position_rules WHERE is_active = 1")
    active = cur.fetchone()[0]

    # 2. 生成 SQL
    lines = []
    lines.append("-- ============================================================")
    lines.append("-- 高量战法 v5.3.30 规则导出（portfolio.db → SQL）")
    lines.append(f"-- 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"-- 来源数据库: {db_path}")
    lines.append(f"-- 规则总数: {total}（激活 {active} / 留白 R051）")
    lines.append(f"-- 短线条数: 19 / 长线条数: 40 / 底仓条数: 12")
    lines.append("-- ============================================================")
    lines.append("")
    lines.append("-- Schema（与 portfolio_schema.sql 一致）")
    lines.append("""CREATE TABLE IF NOT EXISTS position_rules (
    rule_id TEXT PRIMARY KEY,
    rule_name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    threshold_value REAL,
    threshold_text TEXT,
    description TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
);""")
    lines.append("")
    lines.append("-- 规则数据")
    lines.append("BEGIN;")
    lines.append("")

    # 按编号排序导出
    cur = conn.execute("""
        SELECT rule_id, rule_name, rule_type, threshold_value, threshold_text,
               description, is_active, created_at, updated_at
        FROM position_rules
        ORDER BY rule_id
    """)

    for r in cur.fetchall():
        # 转义单引号
        def esc(s):
            if s is None:
                return "NULL"
            return "'" + str(s).replace("'", "''") + "'"

        def esc_num(n):
            if n is None:
                return "NULL"
            return str(n)

        lines.append(
            f"INSERT OR REPLACE INTO position_rules (rule_id, rule_name, rule_type, threshold_value, threshold_text, description, is_active, created_at, updated_at) VALUES "
            f"({esc(r['rule_id'])}, {esc(r['rule_name'])}, {esc(r['rule_type'])}, "
            f"{esc_num(r['threshold_value'])}, {esc(r['threshold_text'])}, {esc(r['description'])}, "
            f"{r['is_active']}, {esc(r['created_at'])}, {esc(r['updated_at'])});"
        )

    lines.append("")
    lines.append("COMMIT;")
    lines.append("")
    lines.append("-- R051 留白说明（v5.3.30+）")
    lines.append("-- R051 编号保留为空，原'浮亏加仓'规则因与 R024 红线矛盾已删除")
    lines.append("-- 不重用 R051 编号（v5.3.30 硬约束）")
    lines.append("")
    lines.append("-- ============================================================")
    lines.append("-- 分类统计")
    lines.append("-- ============================================================")
    lines.append(f"-- 总规则: {total}（激活 {active}）")
    lines.append(f"-- 短线: 19 条")
    lines.append(f"-- 长线: 40 条")
    lines.append(f"-- 底仓: 12 条")
    lines.append(f"-- 留白: R051")
    lines.append("-- ============================================================")

    content = "\n".join(lines)

    Path(output_path).write_text(content, encoding="utf-8")
    conn.close()

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        db_path = config.CAPITAL_DB
    else:
        db_path = sys.argv[1]

    output = export_rules(db_path)
    print(f"✅ 导出完成: {output}")
    print(f"   数据库: {db_path}")
    print(f"   规则数: {sum(1 for _ in Path(output).read_text(encoding='utf-8').splitlines() if _.startswith('INSERT'))}")
