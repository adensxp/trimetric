# 度量衡 · trimetric

> **A股三层漏斗交易系统：衡（微淼质量筛选）→ 度（R090-R094 建仓时机）→ 量（高量锚定执行）**
> **最新版本**：V5.3.31（建仓时机判定系统 R090-R094 + 原始高量战法 B/S/C 完整规则 + 微淼完整集成）
> **基础版本**：高量战法（2026-08-27 N 字结构 + 分批加仓版：5 年 61 笔 / 胜率 49.2% / 回撤 -3.00%）

---

## 🎯 技能能做什么

V5.3.30 高量战法是**完整的 A 股短线 + 长线 + 底仓三策略系统**，结合**皇冠明珠财务过滤**、**股息税率优化**、**每日预备池轮动**和**盘中完整监控**：

| 维度 | 能力 |
|------|------|
| **三策略** | 短线（7-17% 仓位 T+30） + 长线（15-25% 仓位 1-3 年） + 底仓（30-50% 仓位 永久）|
| **皇冠明珠** | 16:30 自动跑微淼严苛财务过滤（5 年 ROE ≥ 20% STRICT / ≥ 15% RELAXED + 5 年负债 ≤ 60%），三策略评分加**阶梯式皇冠加权**（基础 < 5 不加权 / 5-6 +0.3 / 7-8 +1.0 / 9-10 +1.5，避免虚高）|
| **股息税率** | 短线 T+30 ≤ 1 月 → 20% 税（选股避税） / 长线底仓 > 1 年 → 0% 暂免（自动免税）|
| **商业判断** | 长线/底仓增加软指标（行业地位/ROE 趋势/护城河/管理层各 0.5-1.0 分）|
| **每日预备池** | 16:30 全量分析 494 只股票 → 找出三套预备池（短线 5 + 长线 3 + 底仓 2 = 10 个）|
| **盘中完整监控** | 每个时间点（9:35/10:00/11:00/13:30/14:30）跑完整检查（建仓 + 加仓 + 减仓 + 清仓）|
| **持仓跟踪** | 5 个 monitor cron 自动跟踪 5 规则（R005 强平 / R024 红线 / R005A 浮盈回撤 / R007-R008 加仓 / R027-R029 分批止盈）|
| **清仓后跟踪** | 自动回到股票池，第二晚被 daily-pool-analysis 评分（不建单独 cron）|
| **微淼硬过滤** | R059-R070 共 12 条财务硬过滤（ROE > 20% / 股息率 / 现金流 / 毛利率 / 资产负债率 / 分红比例 / 上市年限 / 大盘择时 / PE 绝对值 / TTM PE）|

---

## 📊 三策略对比

| 策略 | 框架代号 | 章节 | 规则数 | 仓位 | 周期 | 适用 | 包含版本 |
|------|----------|------|--------|------|------|------|----------|
| **短线** | V5.3.30 短线 | SKILL.md §1-19 | 19 条 + 股息税 | 7-17% | T+30 | 高量战法 | V5.3.30 框架 + R090-R094 |
| **长线** | V5.3.30 长线 | reference/43 | 40 条 + 商业判断 | 15-25% | 1-3 年 | 基本面 + 微淼 12 过滤 + 商业判断 | V5.3.30-LT |
| **底仓** | V5.3.30 底仓 | reference/44 | 12 条 + 商业判断 | 30-50% | 永久 | 高分红 + 永不清仓 + 商业判断 | V5.3.30-CH |

---

## 🔄 完整工作流

```
16:30 收盘后  →  daily-pool-analysis cron
                ↓ 评分 494 只股票（V5.3.30 + V5.3.30-LT + V5.3.30-CH 三策略）
                ↓ 找出三套预备池（5 + 3 + 2 = 10 个）
                ↓ 写入 daily_pool_scores + daily_prep_pool 表

9:00 开盘前   →  morning-prep-list cron
                ↓ 读取三套预备池 + 实时开盘价
                ↓ 用户根据当前策略挑选
                ↓ R057 9:30-9:45 不追涨

9:35 / 10:00 / 11:00 / 13:30 / 14:30
            →  monitor.py 完整检查
                ↓ 持仓的清仓/加仓/减仓规则
                ↓ 预备池前 3 名的建仓机会
                ↓ 输出所有触发的告警

16:00 收盘后  →  stock-db-daily-update cron
                ↓ 拉取增量数据
                ↓ 更新 positions.db / capital.db
                ↓ 持仓监控 + 7 规则触发

持仓中        →  5 个 monitor cron（已统一为完整检查）

清仓后       →  回到股票池
                ↓ 第二晚 daily-pool-analysis 自动覆盖（全量 155 只评分）
                ↓ 9:00 morning-prep-list 输出三套预备池
                ↓ 等清仓票在预备池前 N 名 → 重新建仓
```

---

## 🤖 10 个 cron 任务（全部 enabled）

| 任务 | task_id | 触发时间 | 功能 |
|------|---------|----------|------|
| morning-prep-list | 438632162218424 | 9:00 | 三套预备池挑选 |
| v5-3-30-monitor-935 | 435267725082961 | 9:35 | 完整检查（建仓+加仓+减仓+清仓）|
| v5-3-30-monitor-1000 | 435267725082962 | 10:00 | 完整检查 |
| v5-3-30-monitor-1100 | 435267725082963 | 11:00 | 完整检查 |
| v5-3-30-monitor-1330 | 435267725082964 | 13:30 | 完整检查 |
| v5-3-30-monitor-1430 | 435267725082965 | 14:30 | 完整检查 |
| stock-db-daily-update | 430676649251295 | 16:00 | 收盘数据 + 持仓监控 |
| daily-quality-check | （部署机回填） | 8:00 | 494 池质量降级扫描 |
| pool-rotation | （部署机回填） | 1/7月1日 9:00 | 股票池半年轮替（dry-run 确认制） |
| daily-pool-analysis | 439121248117120 | 16:30 | 三策略全量评分（短线+长线+底仓）|

---

## 💾 四库架构（v5.3.31）

| 库 | 表 | 用途 |
|----|-----|------|
| **① master.db** 总数据库 | stock_daily, stock_pe_ttm, stock_info, w19_18steps, w20_8indicators, fraud_redflags, stock_moats, financial_data, daily_company_quality, market_pe_history | K线+年报+财务+质量快照（494 股池，**唯一必须备份**）|
| **② candidates.db** 候选库 | daily_pool_scores, daily_prep_pool, crown_jewels, quality_alert_log | 每日评分/预备池/皇冠/警报（每日可重建）|
| **③ positions.db** 持仓库 | positions, trades, events, daily_pnl | 持仓/交易流水/事件/每日盈亏 |
| **④ capital.db** 资金库 | cash_flow, meta, position_rules | 现金流/本金/费率/规则存档 |

> 部署机从老库升级：`python3 scripts/migrate_to_4db.py`（老文件留档，BSC 快照库退役）

---

## 📂 文件结构

```
高量战法/
├── SKILL.md                    # 技能本身（规则 + 操作流程）
├── README.md                   # 功能介绍（本文档）
├── docs/
│   ├── CHANGELOG.md            # 版本演进史
│   └── ARCHITECTURE.md         # 架构图
├── reference/                  # 详细规则文档
│   ├── 42-v5418-n-structure.md
│   ├── 43-v5418-lt-long-term.md
│   ├── 44-v5418-ch-core-holding.md
│   ├── 45-v5419-practice-feedback.md
│   ├── 46-v5420-feng-yao-fundamentals.md
│   ├── 47-rule-index.md        # 71 条规则索引
│   ├── 48-v5421-miaoyao-pdf.md
│   ├── 49-database-export.md
│   ├── ~~50-v5422-daily-pool.md~~ (V5.3.30 已合并到 CHANGELOG.md §16)
│   ├── ~~51-v5423-three-strategy-pool.md~~ (V5.3.30 已合并到 CHANGELOG.md §16)
│   └── ~~52-v5424-full-monitor.md~~ (V5.3.30 已合并到 CHANGELOG.md §16)
├── archive/                   # 历史脚本归档（75 个回测/一次性数据脚本）
└── scripts/                    # 在用脚本（26 个，依赖闭包分析）
    ├── daily_pool_analysis_v3.py  # v5.3.30 三策略评分 + 建仓时机
    ├── monitor.py                 # V5.3.30 盘中完整监控
    ├── init_db.py                 # 一键建库（新用户安装第一步）
    ├── fetch_data.py              # 行情初始化/刷新（腾讯公开接口）
    ├── portfolio_manager.py
    ├── position_sizer.py
    ├── fee_calculator.py
    └── ...
```

---

## 🎯 核心规则速查

### 短线 V5.3.30（19 条）

| 规则 | 阈值 | 触发 |
|------|------|------|
| **R005 强平** | -5% | 🔴 立即清仓 |
| **R007 加仓 1** | +5% + 量比 1.5 + MA5 | 🟢 加仓 5% |
| **R008 加仓 2** | +10% + 量比 1.5 + MA5 | 🟢 加仓 5% |
| **R024 红线** | 浮盈变浮亏 | 🔴 立即清仓 |
| **R027** | +20% | 🟢 减仓 25% |
| **R028** | +30% | 🟢 减仓 50% |
| **R029** | +50% | 🟢 清仓 |
| **R057** | 9:30-9:45 | ⛔ 不追涨 |
| **R058** | review 30 分钟 | ⏰ 必须决策 |
| **R005A** | 浮盈回撤 30% | 🟠 减仓 50% |

### 长线 V5.3.30-LT（40 条）

- **R011-R015**：5 信号建仓（ROE/营收/行业/龙头/估值）
- **R024A**：浮盈回撤 30% 减仓 50%
- **R033-R036**：长线分批止盈（+20%/+50%/+100%/+200%）
- **R049-R056**：长线加仓 / 减仓 / 基本面恢复加回
- **R059-R070**：微淼 12 硬过滤（**R068 大盘择时最重要**）

### 底仓 V5.3.30-CH（12 条）

- **R037-R039**：3 信号建仓（股息率 / 稳健 / 行业）
- **R040-R042**：底仓仓位（50%/80%/70%）
- **R043**：有闲钱买点（不定时）
- **R045**：永久恶化清仓
- **R047**：不设浮亏底线（分红票浮亏正常）
- **R048**：永不止盈（让复利）

---

## 📈 版本对比

| 版本 | 核心内容 | 实战验证 |
|------|----------|----------|
| V5.3.30 | **建仓时机 R090-R094** + 原始 B/S/C 规则 + 微淼完整集成 | 立讯精密/海信家电避坑验证 |

详见 [`docs/CHANGELOG.md`](./docs/CHANGELOG.md)。

---

## 🚀 5 分钟上手（全新安装）

> 环境要求：Linux + Python ≥ 3.8，纯标准库零依赖；数据源为腾讯公开行情接口，无需任何凭证。

**一行命令安装**（自动克隆 + 建库 + 链路验证 + 33 项自检）：

```bash
curl -fsSL https://raw.githubusercontent.com/adensxp/trimetric/main/install.sh | bash
```

或手动分步：

```bash
# 1) 一键安装脚本（或 python3 scripts/init_db.py 仅建库）
bash install.sh

# 2) 拉取行情（先 2 只验证链路，再全量）
python3 scripts/fetch_data.py --codes 600519.SH,002475.SZ
python3 scripts/fetch_data.py --pool data/stocks_batches_500.json --limit 494   # 全量约 3 分钟

# 3) 盘中监控（持仓 + 预备池信号判定）
python3 scripts/monitor.py

# 4) 安装自检：33 项单测全绿
python3 tests/test_rules.py && python3 tests/test_config.py
```

### 路径配置（可选，环境变量重定向）

默认路径为部署机 `/workspace/stock_db` 布局；任何机器可用环境变量迁移，零代码改动：

| 变量 | 默认 | 说明 |
|---|---|---|
| TRIMETRIC_MASTER_DB | /workspace/stock_db/master.db | ① 总数据库：K线+年报+财务+质量快照（唯一必须备份） |
| TRIMETRIC_CANDIDATES_DB | /workspace/stock_db/candidates.db | ② 候选库：评分/预备池/皇冠/警报（每日可重建） |
| TRIMETRIC_POSITIONS_DB | /workspace/stock_db/positions.db | ③ 持仓库：持仓/流水/事件/每日盈亏 |
| TRIMETRIC_CAPITAL_DB | /workspace/stock_db/capital.db | ④ 资金库：现金流/本金/费率/规则存档 |
| TRIMETRIC_POOL_500 | /workspace/stock_db/stocks_batches_500.json | 494 股池 |
| TRIMETRIC_LOG_DIR | <部署目录>/logs | 日志目录（自动创建） |

> **部署机老数据升级（一次性）**：`python3 scripts/migrate_to_4db.py` —— master ← 老行情库整库复制；candidates/positions/capital ← 老持仓库按表拆分；老文件留档；BSC 快照库退役。

示例：`TRIMETRIC_STOCK_DB=/x/stock.db TRIMETRIC_STOCK_DB_BSC=/x/stock.db python3 scripts/monitor.py`

### 定时运行（可选 crontab 模板）

```
35  9 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
 0 10 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
 0 11 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
30 13 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
30 14 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
30 16 * * 1-5  cd <项目路径> && python3 scripts/daily_pool_analysis_v3.py
```

> 部署机原有 mavis cron 体系（cron/cron_tasks_8.json）继续适用。

---

## 📞 详细文档

| 文档 | 路径 | 用途 |
|------|------|------|
| 技能本身 | `SKILL.md` | 规则 + 操作流程 |
| 功能介绍 | `README.md` | 本文 |
| 版本演进 | `docs/CHANGELOG.md` | V5.2.0 → V5.3.30 |
| 架构图 | `docs/ARCHITECTURE.md` | 系统设计 |
| 短线规则 | `SKILL.md §1-19` | V5.3.30 N 字结构 |
| 长线规则 | `reference/43` | V5.3.30-LT |
| 底仓规则 | `reference/44` | V5.3.30-CH |
| 71 条规则索引 | `reference/47-rule-index.md` | R001-R070 完整 |
| 每日预备池 | `reference/50` | V5.3.22 |
| 三策略预备池 | `reference/51` | V5.3.23 |
| 盘中完整监控 | `reference/52` | V5.3.30 |

---

## ⚠️ 免责声明

本技能由《高量战法》规则系统机械推演，**不构成投资建议**。投资有风险，入市需谨慎。最终决策由用户自行承担。
