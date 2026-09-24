---
reference_for: 高量战法 v5.2.2
section: 20.13 V5.2.1 恒生 Connector 接入（V6 + P17 + P19）
parent: SKILL.md
---

> **本文件是《高量战法》SKILL.md 的 reference 章节**
> 详细规则、阈值表、实战案例见此文件
> 主文件仅保留核心规则，需要时按需加载本文件

---

## 20.13 V5.2.1 恒生 Connector 接入（V6 二次确认 + P17 + P19）

> **背景**：P10-P19 中 V6（除权日二次确认）、P17（公告事件）、P19（PE 历史分位）均需要恒生金融数据库的支撑数据。V5.2.1 新增 `hengsheng_connector.py` 模块，统一封装 3 个核心 API：
>
> - **BonusStock**：个股分红（用于 V6 二次确认）
> - **StockValueAnalysis**：价值分析（用于 P19 PE 历史分位）
> - **AShareAnnouncement**：A 股公告（用于 P17 公告事件）

### 20.13.1 模块设计

由于 connector 工具是 MCP 服务，不能直接 Python import，`hengsheng_connector.py` 采用"数据处理层"设计：

- **不直接调用 connector**：提供 `parse_*` 方法处理原始 JSON
- **Agent 工作流**：
  1. 用 `connector__hengsheng__call_api` 拉取原始数据
  2. 把 JSON dict 传给 `HengshengConnector` 的 `parse_*` 方法
  3. 获得标准化的 Python dict 输出
- **高层方法**：`compute_pe_quantile`（P19）、`filter_important_events`（P17）、`confirm_ex_right_days`（V6 二次确认）

### 20.13.2 三大 API 字段映射

#### BonusStock → V6 二次确认
| 字段 | 含义 | 用途 |
|---|---|---|
| `exdivdate` | 除权除息日 | 匹配 K 线日期 |
| `regdate` | 股权登记日 | 通常是除权日前一日 |
| `dividendpretax` | 每股股利(税前) | 计算理论跳空 |
| `dividendaftertax` | 每股股利(税后) | 参考 |
| `bonusscheme` | 分红方案 | 展示用 |
| `sharespershare` | 每股送股比例 | 计算理论跳空（含送股）|
| `bonuspershare` | 每股转增股比例 | 同上 |

#### StockValueAnalysis → P19 PE 分位
| 字段 | 含义 | 用途 |
|---|---|---|
| `tradingday` | 交易日 | 时间序列 |
| `pe` | PE-TTM | 当前 PE |
| `pelyr` | PE-LYR | 静态 PE |
| `pb` | PB-MRQ | 当前 PB |
| `dividendratio` | 滚动股息率 | 双引擎 Engine 2 |
| `peg` | PEG | 辅助 |
| `totalmv` | 总市值 | 参考 |
| `negotiablemv` | 流通市值 | P1 长线识别 |

**P19 计算逻辑**：
```
PE 分位 = (比当前 PE 小的天数 / 总天数) × 100
PE 区间 = [min(历史PE), max(历史PE)]
```

#### AShareAnnouncement → P17 公告事件
| 字段 | 含义 | 用途 |
|---|---|---|
| `publishDate` | 公告日期 | 时间锚定 |
| `title` / `sourceTitle` | 公告标题 | 关键词匹配 |
| `firstCategoryName` | 一级分类 | 筛选 |
| `secondCategoryName` | 二级分类 | 精细分类 |
| `sourceAddress` | PDF 地址 | 详情链接 |

**P17 重要事件分类**：
| 关键词 | event_type | 仓位调整 |
|---|---|---|
| 业绩预增/预盈 | 业绩预告 | +1 档 |
| 业绩预减/预亏 | 业绩预告 | -1 档 |
| 利润分配/分红 | 分红 | 关注除权日 |
| 资产重组/收购 | 重组并购 | 0（仅监控）|
| 重组**终止** | 重组并购 | 短期承压 |
| 减持 | 股东动作 | -0.5 档 |
| 回购 | 股东动作 | +0.5 档 |
| 解禁 | 解禁 | 短期供给冲击 |
| 增发/配股 | 再融资 | 摊薄影响 |
| 停牌/ST/退市 | 重大风险 | -1 档 |

### 20.13.3 V6 二次确认（关键实战价值）

**核心算法**：
```
已知 K 线是前复权数据（默认）
- actual_gap = (除权日收 - 前一日收) / 前一日收
- theoretical_gap = -(每股股利 + 送股×价格) / 前一日收
- **前复权下：actual_gap 已扣除除权效应 = 真实变化**
- 置信度: confirmed if |actual_gap| < 5%
```

**4 只股票实战结果**：

| 股票 | 除权日 | 真实变化 | 含义 |
|---|---|---|---|
| 工行 601398 | 2026-05-13 | -1.11% | 除权后第一天微跌 |
| 伊利 600887 | 2026-06-05 | +0.04% | 除权后第一天基本平开 |
| 紫金 601899 | 2026-06-26 | -1.72% | 除权后第一天小跌 |
| 紫金 601899 | 2026-08-21 | — | K 线未覆盖（未来） |
| 中际旭创 300308 | 2026-04-30 | +1.48% | 除权后第一天小涨 |

**重要发现**：
- **修正前（V5.2.0）**：紫金 6/25 -6.34% 误以为是"除权效应"导致的"假摔"
- **修正后（V5.2.1）**：6/25 是登记日，**全是真的跌 -6.34%**，触发 C7 是真信号
- 6/26 才跌 -1.72%（除权后第一天，真实变化）

### 20.13.4 P19 PE 历史分位（自动计算）

**4 只股票双引擎判定**：

| 股票 | 当前 PE | 历史分位 | Engine 1 | 股息率 | Engine 2 | 综合档位 |
|---|---|---|---|---|---|---|
| 工行 601398 | 8.36 | 60% | 中 | 4.09% | 高 | 提升一档 |
| 伊利 600887 | 13.39 | **5%** | **极低** | 5.39% | **极高** | **提升两档** |
| 紫金 601899 | 13.88 | 28% | 低 | 1.86% | 中 | 提升一档 |
| 中际旭创 300308 | 73.34 | 80% | 高 | 0.14% | 低 | **降一档** |

**实战价值**：
- **伊利**：PE 历史 5% 分位 + 5.39% 股息率 → 极低估，可提升两档仓位（→ 70%）
- **中际旭创**：PE 历史 80% 分位 + 0.14% 股息率 → 高估，需降一档

### 20.13.5 P17 公告事件（实战示例）

**紫金矿业 7-8 月公告事件**：
| 日期 | 事件 | 仓位调整 |
|---|---|---|
| 2026-07-10 | 业绩预增公告 | +1 档 |
| 2026-07-11 | 中期利润分配方案 | 关注 8/21 除权 |
| 2026-07-30 | 终止收购 Allied Gold | 短期承压 |
| 2026-08-13 | 中期权益分派实施 | 关注 8/21 除权 |

**综合调整**：+1.0 档（业绩预增加分被重组终止部分抵消）

### 20.13.6 实战工作流

```python
# Agent 实战工作流（伪代码）

# 1. 用 connector 拉数据
divs_raw = connector__hengsheng__call_api(
    api_id="BonusStock",
    params={"stockObject": ["601899.SH"], "beginDate": "2026-01-01", "endDate": "2026-08-14"}
)
value_raw = connector__hengsheng__call_api(
    api_id="StockValueAnalysis",
    params={"stockObject": ["601899.SH"], "beginDate": "2025-08-15", "endDate": "2026-08-14"}
)
ann_raw = connector__hengsheng__call_api(
    api_id="AShareAnnouncement",
    params={"stockObject": ["601899.SH"], "beginDate": "2026-07-01", "endDate": "2026-08-14"}
)

# 2. 用 hengsheng_connector 处理
from hengsheng_connector import HengshengConnector
conn = HengshengConnector()

# V6 二次确认
divs = conn.parse_dividends(divs_raw)
confirmed = conn.confirm_ex_right_days(klines, divs, is_forward_adjusted=True)

# P19 PE 分位
values = conn.parse_value_analysis(value_raw)
pe_q = conn.compute_pe_quantile(values, days=250)
p19 = conn.check_double_engine(pe_q)

# P17 公告事件
anns = conn.parse_announcements(ann_raw)
important = conn.filter_important_events(anns)
```

### 20.13.7 与 p9_engine.py / data_validator.py 的集成

**data_validator.py**：
- V6 校验函数 `_check_v6_ex_right` 升级，接受 `dividends` 参数
- 有 `dividends` 时进行二次确认
- 无 `dividends` 时仅预警

**p9_engine.py**：
- 新增 `apply_p17_announcements(announcements)` 函数
- 新增 `apply_p19_pe_quantile(pe_quantile)` 函数
- 与原有 `apply_p10/p11/p12/p13/p15` 协同

---

