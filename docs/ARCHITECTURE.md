# 《高量战法》架构说明（ARCHITECTURE）

> 本文档说明技能包的整体架构、模块依赖、数据流和扩展方式。
> **适用版本**：v5.3.30（70 条规则 + 1 留白 · 三策略架构 · 微淼完整方法论）
> **更新日期**：2026-09-06

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────┐
│  SKILL.md（主入口）                                  │
│  0-19 节核心规则 + 21.7/10 + 附录                   │
└────────────────┬────────────────────────────────────┘
                 │
       ┌─────────┼─────────┬──────────┬────────────┐
       │         │         │          │            │
       ▼         ▼         ▼          ▼            ▼
   ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────┐ ┌──────────┐
   │ P1-P9 │ │P10-P19│ │P20-P26│ │ P27-P32  │ │ P33-P36  │
   │ 基础  │ │ 扩展  │ │事件   │ │ 深度验证 │ │ 内部信号 │
   └──┬───┘ └──┬───┘ └──┬───┘ └────┬─────┘ └────┬─────┘
      └────┬───┴────────┴─────────┴────────────┘
           │
           ▼
   ┌──────────────────┐
   │  reference/  31 个文件  │
   │  按需加载（节省 token）│
   └──────────────────┘
           │
           ▼
   ┌──────────────────────────────────────────────┐
   │  scripts/  7 个工具脚本                       │
   │  - p9_engine         板块自适应引擎 + 决策卡  │
   │  - data_validator    V1-V10 数据校验         │
   │  - hengsheng_connector  恒生 API 封装        │
   │  - multi_source_validator  V9 3 源验证        │
   │  - stock_db          SQLite 数据层            │
   │  - batch_fetch_sws   申万行业批量             │
   │  - verify_p9         10 标的验证              │
   └──────────────────────────────────────────────┘
           │
           ▼
   ┌──────────────────────────────────────────────┐
   │  data/  数据存储（按需生成）                   │
   │  - stock_cache.db   SQLite 行情缓存           │
   └──────────────────────────────────────────────┘
```

---

## 2. 规则体系（36 条）

### 2.1 按版本演进

| 版本 | 规则数 | 核心补丁 |
|------|--------|---------|
| V3.5 | 5 节流程 | 5 节决策流程 + C/S/B/W/L |
| V5.1.x | 5 节流程 | 实战内化 + 6 档阈值 |
| V5.2.0 | P1-P9 | 长线机构票分支 + 板块自适应 |
| V5.2.1 | P1-P19 | 实战补强（10 个 P）|
| V5.2.2 | P1-P19 | 工程化修订 |
| V5.2.3 | P1-P22 | 科技股特化 |
| V5.2.4 | P1-P26 | 主力痕迹深化 |
| V5.2.5 | P1-P32 | 6 维数据全开 |
| **V5.2.6** | **P1-P36** | **24 维数据全开 + 内部信号** |

### 2.2 按主题分类

**基础流程**（P1-P9）
- P1-P8：长线机构票分支
- P9：板块自适应阈值

**实战补强**（P10-P19）
- P10-P15：阈值/双轨/分级/组合/量化
- P16-P19：决策卡/公告/风险/PE 分位

**事件驱动**（P20-P26）
- P20：科技股特化
- P21 v2：C3 三步验证
- P22：板块指数取数
- P23-P26：高量资金/大宗/户数/风险因子

**深度验证**（P27-P32）
- P27：融资融券
- P28：十大股东
- P29：质押
- P30：分红
- P31：主营构成
- P32：并购重组

**内部信号**（P33-P36，V5.2.6 新增）
- P33：内部人交易
- P34：限售解禁
- P35：业绩快报
- P36：机构调研

---

## 3. 数据源（24 个 API）

### 3.1 数据源分布

| 类别 | API 数 | API 列表 |
|------|--------|---------|
| **股价/资金** | 5 | AShareLiveQuote / AStockCashFlow / StockDailyQuote / StockRangeQuotation / StockSecuritiesMargin |
| **筹码/股东** | 4 | Top10ShareHolders / ShareholderNum / KeyHolderTrande / StockPledge |
| **基本面/财务** | 5 | StockValueAnalysis / PerformanceForecast / PerformanceExpress / MainOperIncData / BonusStock |
| **市场/行业** | 3 | IndustryIndexLiveQuote / IndustryDailyQuote / IndustryConstituentStocks |
| **事件/风险** | 7 | AShareAnnouncement / MergerRestructEvent / RestrictedStockLifting / StockBlockTrade / StockRiskFactorReport / DailyStockHeroDetails / InstitutionInvestigation |

### 3.2 优先级（实战调用顺序）

```
P18 风险股阻断（强制前置）
  ↓
P9 板块自适应（前提四）
  ↓
P20 科技股特化（条件性）
  ↓
P13 长线机构票（条件性）
  ↓
P19 PE 分位（双引擎）
  ↓
P10-P15 其他规则
  ↓
P23-P32 事件驱动 + 深度验证
  ↓
P33-P36 内部信号（V5.2.6）
```

---

## 4. 工具脚本依赖

```
p9_engine.py
  ├── data_validator.py
  │     └── stock_db.py
  ├── hengsheng_connector.py
  │     └── stock_db.py
  ├── multi_source_validator.py
  │     └── data_validator.py
  │         └── stock_db.py
  └── batch_fetch_sws.py
        └── stock_db.py

verify_p9.py
  └── p9_engine.py
        └── (同 p9_engine 依赖)
```

**核心依赖链**：
- `p9_engine.py` 是核心入口，整合所有数据校验
- `stock_db.py` 是数据层（SQLite 缓存）
- `hengsheng_connector.py` 是数据源（恒生 API）

---

## 5. Token 节省策略

### 5.1 按需加载

主文件（SKILL.md ~75KB）**已包含**：
- 0-19 节核心规则
- 21.7 P16 决策卡模板
- 21.10 P19 PE 分位 4 方案
- 附录执行检查清单

`reference/` 31 个文件**按需加载**：
- 加载时机见 SKILL.md 第 50-130 行
- 实战任意股票 ~14K tokens（vs 之前 ~22K，省 36%）

### 5.2 实战工作流（6 步）

```
Step 1: 拉取数据（hengsheng_connector + multi_source_validator）
Step 2: V1-V10 数据校验（data_validator）
Step 3: 套规则（p9_engine）
Step 4: 按 P16 决策卡模板输出报告
Step 5: V9 多源验证关键数据
Step 6: 写实战记录到 实战记录/（仅本地，发布时清空）
```

---

## 6. 扩展方式

### 6.1 新增规则（P_n）

1. 写 `reference/22-p{nn}-{name}.md`
2. 在 `SKILL.md` 第 21 章加载策略表加 1 行
3. 在 `SKILL.md` 附录执行检查清单加 1 项
4. 在 `docs/CHANGELOG.md` 加 V{version} 章节
5. （可选）写实战记录到 `实战记录/`，发布时清空

### 6.2 新增数据源

1. 在 `scripts/hengsheng_connector.py` 加新 API 封装函数
2. 在 `scripts/multi_source_validator.py` 加新源（如适用）
3. 在 `scripts/data_validator.py` 加新校验规则（如适用）
4. 更新 `scripts/p9_engine.py` 主引擎（如需调用新数据）

### 6.3 实战新标的

1. 跑 6 步工作流
2. 写 `实战记录/{代码}_{名称}_{日期}_V{version}.md`（本地归档）
3. 在 `实战记录/README.md` 速查表加 1 行
4. 在 `docs/CHANGELOG.md` V{version} 章节加实战发现
5. 评估是否需要新规则补丁

---

## 7. 版本兼容

| 版本 | SKILL.md | reference/ | scripts/ | data/ |
|------|----------|-----------|----------|-------|
| V5.2.3 | 1500+ 行 | 17 文件 | 7 .py | - |
| V5.2.4 | 1450 行 | 21 文件 | 7 .py | - |
| V5.2.5 | 1450 行 | 27 文件 | 7 .py | - |
| **V5.2.6** | **1399 行** | **31 文件** | **7 .py** | **(按需)** |

**向下兼容**：
- 旧版本实战记录仍可读（不发布到产品包）
- V5.2.3 P21 严格模式文档保留（被 P21 v2 替代）
- 所有 .py 脚本独立，可单独调用

---

## 8. 已知限制

1. **数据滞后**：恒生 API 部分数据滞后 5-10 日（如解禁/内部人）
2. **频率限制**：恒生 API 单次最多 30 主体，多主体需批量模式
3. **离线分析**：`p9_engine.py` 部分函数依赖 `stock_cache.db`，需先 `batch_fetch_sws` 拉数据
4. **申万行业**：P9 依赖申万 6 大类 34 行业，部分小行业可能无对应阈值
5. **实战数据隐私**：发布产品包时清空 `实战记录/` 目录

---

## 9. 下一版规划

V5.2.7 候选：
- 工具脚本集成更多 P33-P36 函数
- reference 按主题重新分类（foundation / extended / event / deep / internal）
- 实战监控看板（cron 定期跑 + 报告推送）
- 多源数据降级路径完善
