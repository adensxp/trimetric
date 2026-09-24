# V5.3.21 数据库使用指南

> portfolio.db 规则导出与重建

## 📍 数据库位置

**V5.3.21 数据库**：不在产品包中（用户私有数据）

| 类型 | 位置 |
|------|------|
| **主数据库（生产）** | `/workspace/stock_db/portfolio.db`（102 KB · 71 条规则 + 交易历史）|
| **导出工具** | `/workspace/stock_db/portfolio_db_export.py`（5 KB）|
| **规则导出文件** | `/workspace/stock_db/portfolio_rules_v5.3.21.sql`（26 KB）|
| **产品包内置** | `scripts/portfolio_db_export.py` + `scripts/portfolio_rules_v5.3.21.sql` |

## ⚠️ 重要：数据库不发布到产品包

portfolio.db 包含：
- ✅ `position_rules`（71 条规则）— **可发布**（已导出为 SQL）
- ❌ `trades`（交易记录）— **不发布**（用户私有）
- ❌ `positions`（当前持仓）— **不发布**
- ❌ `daily_pnl`（每日盈亏）— **不发布**
- ❌ `cash_flow`（资金流水）— **不发布**
- ❌ `events`（事件日志）— **不发布**

## 🛠️ 使用方法

### 方法 1：从 V5.3.21 zip 重建规则（推荐）

```bash
# 1. 解压 V5.3.21
unzip 高量战法_v5.3.21.zip -d 高量战法/

# 2. 创建空数据库
sqlite3 portfolio.db < portfolio_schema.sql  # 或新建空 db

# 3. 导入 71 条规则
sqlite3 portfolio.db < 高量战法/scripts/portfolio_rules_v5.3.21.sql

# 4. 验证
sqlite3 portfolio.db "SELECT COUNT(*) FROM position_rules"
# 应返回 71

sqlite3 portfolio.db "SELECT rule_id, rule_name FROM position_rules WHERE rule_id IN ('R067','R068','R069','R070')"
# 应返回 4 行
```

### 方法 2：从现有 portfolio.db 导出

```bash
# 用法 1：默认导出
python3 portfolio_db_export.py
# 输出: /workspace/stock_db/portfolio_rules_v5.3.21.sql

# 用法 2：指定路径
python3 portfolio_db_export.py /path/to/portfolio.db /path/to/output.sql
```

## 📊 V5.3.21 数据库 Schema

### position_rules 表

```sql
CREATE TABLE position_rules (
    rule_id TEXT PRIMARY KEY,         -- R001-R070（R051 留白）
    rule_name TEXT NOT NULL,          -- 规则名称
    rule_type TEXT NOT NULL,          -- 规则类型标识
    threshold_value REAL,             -- 数值阈值
    threshold_text TEXT,              -- 文字阈值
    description TEXT,                 -- 规则描述
    is_active INTEGER DEFAULT 1,      -- 是否激活
    created_at TEXT,                  -- 创建时间
    updated_at TEXT                   -- 更新时间
);
```

### V5.3.21 规则统计

| 类型 | 数量 | 规则 ID 范围 |
|------|------|--------------|
| 短线 V5.3.18 | 19 条 | R001-R010 + R005A + R027-R032 + R057-R058 |
| 长线 V5.3.18-LT | 40 条 | R011-R026 + R024A + R033-R036 + R049-R050 + R051 留白 + R052-R056 + R059-R070 |
| 底仓 V5.3.18-CH | 12 条 | R037-R048 |
| **合计** | **70 实际 + 1 留白** | R001-R070 |

## 🔄 V5.3.21 关键规则查询

```sql
-- 1. 短线红线（最重要）
SELECT * FROM position_rules
WHERE rule_id IN ('R005', 'R005A', 'R024', 'R024A', 'R057', 'R058');

-- 2. 长线微淼硬过滤
SELECT * FROM position_rules
WHERE rule_id BETWEEN 'R059' AND 'R070'
ORDER BY rule_id;

-- 3. 底仓核心
SELECT * FROM position_rules
WHERE rule_id IN ('R037', 'R038', 'R043', 'R044', 'R047', 'R048');

-- 4. R051 留白确认（不应有数据）
SELECT * FROM position_rules WHERE rule_id = 'R051';
-- 应返回 0 行
```

## 💡 实战工作流

```bash
# 1. 每日 16:00 review
python3 /workspace/stock_db/portfolio_manager.py review

# 2. 仓位建议
python3 /workspace/stock_db/position_sizer.py review

# 3. 财务硬过滤
python3 /workspace/stock_db/fundamental_filter.py score 600519.SH

# 4. 手续费计算
python3 -c "from fee_calculator import calc_fee; print(calc_fee(100, 55.0, 'SELL', '002475.SZ'))"
```

## 📁 文件清单

| 文件 | 大小 | 说明 |
|------|------|------|
| `portfolio.db` | 102 KB | 主数据库（生产）|
| `portfolio_rules_v5.3.21.sql` | 26 KB | 71 条规则 SQL（产品包内置）|
| `portfolio_db_export.py` | 5 KB | 导出工具（产品包内置）|
| `portfolio_schema.sql` | - | Schema 定义 |
| `portfolio_manager.py` | 32 KB | 交易管理 CLI |
| `position_sizer.py` | 24 KB | 仓位管理 |
| `fee_calculator.py` | 6 KB | 手续费计算 |
| `fundamental_filter.py` | 10 KB | 财务硬过滤 |

<deliver-assets>
<media type="file" src="/workspace/.skills/高量战法/scripts/portfolio_db_export.py" caption="数据库导出工具" />
<media type="file" src="/workspace/.skills/高量战法/scripts/portfolio_rules_v5.3.21.sql" caption="V5.3.21 71 条规则 SQL" />
</deliver-assets>
