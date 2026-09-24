---
reference_for: 高量战法 v5.2.2
section: 20.14 V5.2.1 V2.2 V9 多源验证 + 决策卡自动输出
parent: SKILL.md
---

> **本文件是《高量战法》SKILL.md 的 reference 章节**
> 详细规则、阈值表、实战案例见此文件
> 主文件仅保留核心规则，需要时按需加载本文件

---

## 20.14 V5.2.1 V2.2 V9 多源验证 + 决策卡自动输出

> **背景**：V9 强制 ≥ 2 个数据源交叉验证，P16 决策卡每次实战必须输出。V5.2.1 V2.2 新增 `multi_source_validator.py` 模块，统一封装这两个功能。

### 20.14.1 模块组成

`multi_source_validator.py` 提供两个类：
- `MultiSourceValidator` - V9 多源验证器
- `DecisionCardGenerator` - P16 决策卡生成器

### 20.14.2 V9 多源验证设计

**支持数据源**：
1. **腾讯证券**（`web.ifzq.gtimg.cn`） - HTTP 直接拉，含完整 OHLCV
2. **恒生 StockMultiPeriodQuote** - 通过 connector 工具，含 OHLCV + 换手率

**核心算法**：
```
V9 多源对比：
  对每一天 k:
    price_a = close of source_a
    price_b = close of source_b
    diff_pct = |price_a - price_b| / price_a
    if diff_pct < threshold (0.5%): PASS
    else: FAIL
  整体 PASS = 所有天都 PASS
```

**4 只股票实测结果**：
| 股票 | 对比天数 | 一致天数 | 最大差异 | 通过 |
|---|---|---|---|---|
| 工商银行 601398 | 2 | 2 | 0.000% | ✅ |
| 伊利股份 600887 | — | — | — | 跳过（无恒生）|
| 紫金矿业 601899 | — | — | — | 跳过（无恒生）|
| 中际旭创 300308 | — | — | — | 跳过（无恒生）|

### 20.14.3 P16 决策卡自动输出

**集成函数**：`p9_engine.py` 的 `validate_then_query_full()`

**决策卡内容**（10 段信息）：
1. 标题 + 数据基准日
2. 风险股阻断状态
3. 长线机构票等级 + 板块 + 品种分类 + 阈值 + 窗口期
4. 当前价 + 昨收 + 60 日高/低 + 关键支撑压力
5. 信号状态（L/B/S/C 4 个分组）
6. 关键监控点（最多 3 条）
7. 建议操作 + 仓位
8. 止损位 + 目标位

**4 只股票实测**：全部自动生成决策卡，30 秒可看完关键信息

### 20.14.4 完整工作流（Agent 实战）

```python
# 步骤 1: 拉腾讯 K 线（HTTP 直接）
tencent_klines = msv.fetch_tencent_klines("601398", count=100)

# 步骤 2: 用 connector 拉恒生 K 线
hengsheng_raw = connector__hengsheng__call_api(
    api_id="StockMultiPeriodQuote",
    params={"stockObject": ["601398.SH"], "beginDate": "2026-08-01", "endDate": "2026-08-14",
            "restorationStatus": "1", "statisticsPeriod": "6"}
)
hengsheng_klines = msv.parse_hengsheng_klines(hengsheng_raw)

# 步骤 3: V9 多源验证
v9_result = msv.validate_v9(tencent_klines, hengsheng_klines)
# → {"compared_days": 2, "matched_days": 2, "max_diff_pct": 0.0, "pass": True}

# 步骤 4: 完整实战（V1-V10 + 战法分析 + 决策卡）
result = validate_then_query_full(
    code="601398.SH",
    dividends=[...],          # parse_dividends 输出
    value_raw={...},          # StockValueAnalysis 原始响应
    announcements={...},      # AShareAnnouncement 原始响应
    hengsheng_raw=hengsheng_raw,
    sws_code="480000",
)
# → result['card'] 是自动生成的决策卡字符串
```

### 20.14.5 沙箱环境限制

在沙箱环境（开发机/受限网络）下：
- **可用数据源**：腾讯 K 线 + 恒生 connector
- **不可用**：东方财富 / 新浪（被沙箱屏蔽）
- **生产环境**：可加入东方财富、新浪、和讯等多个源，V9 验证更严格

---

