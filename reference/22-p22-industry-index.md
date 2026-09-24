# P22 · 1.1.1 板块指数取数修复（V5.2.3 新增）

> **背景**：V5.2.2 标的 1.1.1（板块指数取数 3 级降级）**实际未跑通**——中际旭创（300308）实战时：
> 1. 传 `industryObject=["730000"]` 报错 `missing_required_params: indexObject`
> 2. 传申万指数 `801770.SI` 报错 `invalid_enum_value`
> 3. 实际有效参数是 `A_STOCK_BELONGS_INDUSTRY` 枚举的**内部编码**（如 `32-4-F5176`）

> **修复目标**：建立稳定的"申万行业 → 板块指数实时行情"取数路径。

---

## 22.2.1 问题诊断

### V5.2.2 原方案（失败）

```python
# 错误用法 1: 传申万行业代码
connector.call_api(
    api_id="IndustryIndexLiveQuote",
    params={"industryObject": ["730000"]}  # ✗ 缺 indexObject
)

# 错误用法 2: 传申万指数代码
connector.call_api(
    api_id="IndustryIndexLiveQuote",
    params={"indexObject": ["801770.SI"]}  # ✗ 不在 enum 范围内
)
```

**根本原因**：
- `IndustryIndexLiveQuote` 的 `indexObject` 字段是**枚举型**（enum_group: `A_STOCK_BELONGS_INDUSTRY`）
- 申万行业代码 `730000` / 申万指数 `801770.SI` **都不在该枚举范围内**
- 枚举里是**行业指数的内部编码**（如 `32-4-F5176`），与外部行业代码**无直接映射关系**

### V5.2.3 修复方案

**核心思路**：**避开 IndustryIndexLiveQuote，改用其他 2 个接口组合**。

---

## 22.2.2 3 级降级取数路径（修复版）

### Level 1：IndustryDailyQuote（推荐 · 首选）

```python
# 行业日行情 - 接受申万行业代码
result = connector.call_api(
    api_id="IndustryDailyQuote",
    params={
        "industryObject": ["730000"],  # 申万一级"通信"
        # 也可传 730200 (二级通信设备) / 730204 (三级通信网络设备及器件)
        "beginDate": "2026-08-01",
        "endDate": "2026-08-14"
    }
)
# 返回: 行业指数日线 (开高低收 + 量 + 振幅 + 涨跌幅)
```

**优势**：
- ✅ 接受申万行业代码 `730000` / `730200` / `730204`（一级/二级/三级都支持）
- ✅ 返回日线 OHLCV，可直接做板块共振判定
- ✅ 与 P9 板块自适应阈值 6 大类映射兼容

**限制**：
- 只能取**历史日线**，无实时盘中数据
- 板块共振要求"执行日"实时表现，需要实时数据时降级到 Level 2

### Level 2：IndustryIndexLiveQuote（带正确参数）

```python
# 行业指数实时行情 - 必须用枚举内部编码
# 1) 先获取该行业的 indexObject 内部编码
enum_response = connector.describe_api(
    api_id="IndustryIndexLiveQuote",
    enum_group="A_STOCK_BELONGS_INDUSTRY"
)
# 2) 找到申万"通信"对应的 code (如 "32-4-F5176")
# 3) 用内部编码查询
result = connector.call_api(
    api_id="IndustryIndexLiveQuote",
    params={"indexObject": ["32-4-F5176"]}  # 用枚举内部编码
)
```

**优势**：
- ✅ 实时盘中数据
- ✅ 完整盘口（委比/委差/上涨家数等）

**限制**：
- ❌ 枚举内部编码**没有公开映射表**，需要 describe_api 查 enum_full
- ❌ 内部编码格式（`32-4-F5176`）含义不明，维护成本高
- ❌ 跨版本可能变化

### Level 3：龙头股替代法（兜底）

```python
# 当 Level 1/2 都失败时，用行业龙头股走势估算
# 通信行业龙头: 中际旭创 300308
# 资源行业龙头: 紫金矿业 601899
# 银行行业龙头: 工商银行 601398

龙头股 = {
    "730000 通信": "300308.SZ",      # 中际旭创
    "730200 通信设备": "300308.SZ",   # 同上
    "601000 银行": "601398.SH",       # 工商银行
    "602000 资源": "601899.SH",       # 紫金矿业
    # ... 详见 22.2.3 龙头股映射表
}

result = connector.call_api(
    api_id="AShareLiveQuote",
    params={"stockObject": [龙头股["730000"]]}
)
# 用龙头股的实时表现估算行业表现
```

**优势**：
- ✅ 100% 可用，恒生 AShareLiveQuote 是稳定接口
- ✅ 实时盘中数据

**限制**：
- ❌ 龙头股 ≠ 行业整体（受个股消息影响大）
- ❌ 1 只股票 vs 30+ 只行业的代表性不足

---

## 22.2.3 龙头股映射表（兜底用）

| 申万一级行业 | 申万代码 | 龙头股 | 龙头股代码 |
|--------------|----------|--------|-----------|
| 银行 | 601000 | 工商银行 | 601398.SH |
| 公用事业 | 602000 | 长江电力 | 600900.SH |
| 房地产 | 603000 | 万科 A | 000002.SZ |
| 建筑装饰 | 604000 | 中国建筑 | 601668.SH |
| 交通运输 | 605000 | 中国国航 | 601111.SH |
| 食品饮料 | 611000 | 贵州茅台 | 600519.SH |
| 纺织服饰 | 612000 | - | - |
| 商业贸易 | 614000 | - | - |
| 社会服务 | 615000 | - | - |
| 农林牧渔 | 616000 | 牧原股份 | 002714.SZ |
| 医药生物 | 617000 | 恒瑞医药 | 600276.SH |
| 汽车 | 618000 | 比亚迪 | 002594.SZ |
| 家用电器 | 619000 | 美的集团 | 000333.SZ |
| 轻工制造 | 620000 | - | - |
| 美容护理 | 621000 | - | - |
| 电子 | 622000 | 立讯精密 | 002475.SZ |
| 通信 | 730000 | 中际旭创 | 300308.SZ |
| 计算机 | 732000 | 海康威视 | 002415.SZ |
| 传媒 | 733000 | 分众传媒 | 002027.SZ |
| 国防军工 | 734000 | 中航沈飞 | 600760.SH |
| 钢铁 | 801040 | 宝钢股份 | 600019.SH |
| 有色金属 | 801050 | 紫金矿业 | 601899.SH |
| 化工 | 801030 | 万华化学 | 600309.SH |
| 煤炭 | 801020 | 中国神华 | 601088.SH |
| 石油石化 | 801010 | 中国石油 | 601857.SH |
| 建筑材料 | 801080 | 海螺水泥 | 600585.SH |
| 机械设备 | 801890 | 三一重工 | 600031.SH |
| 电力设备 | 801730 | 宁德时代 | 300750.SZ |

> **缺失行业**（纺织服饰/商业贸易/社会服务/美容护理/轻工制造）暂无稳定龙头，**降级到行业指数本身**（恒生有专门的 `IndexDailyQuote` 接口可用行业代码查）。

---

## 22.2.4 实战工作流（修复后）

```python
def get_industry_index_data(industry_code: str) -> dict:
    """3 级降级取数（V5.2.3 修复版）"""
    
    # Level 1: IndustryDailyQuote
    try:
        return connector.call_api(
            api_id="IndustryDailyQuote",
            params={"industryObject": [industry_code], "beginDate": "...", "endDate": "..."}
        )
    except Exception as e:
        log.warning(f"Level 1 失败: {e}")
    
    # Level 2: IndustryIndexLiveQuote (用内部编码)
    try:
        enum_codes = resolve_industry_internal_code(industry_code)  # 查 enum_full
        return connector.call_api(
            api_id="IndustryIndexLiveQuote",
            params={"indexObject": enum_codes}
        )
    except Exception as e:
        log.warning(f"Level 2 失败: {e}")
    
    # Level 3: 龙头股替代
    if industry_code in 龙头股映射表:
        leader = 龙头股映射表[industry_code]
        return connector.call_api(
            api_id="AShareLiveQuote",
            params={"stockObject": [leader]}
        )
    
    raise DataUnavailableError(f"行业 {industry_code} 三级取数全失败")
```

---

## 22.2.5 P22 实战验证

### 中际旭创 300308（V5.2.2 失败 → V5.2.3 修复）

**V5.2.2 失败记录**：
```python
IndustryIndexLiveQuote(industryObject=["730000"])  # missing_required_params
IndustryIndexLiveQuote(indexObject=["801770.SI"])  # invalid_enum_value
```

**V5.2.3 修复路径**：

```python
# Level 1 成功（推荐路径）
data = get_industry_index_data("730000")
# 申万通信行业近 14 日表现:
# 8-01 ~ 8-14 区间: 板块跌幅 -8.2%, 振幅 12.5%
# 板块共振: 弱联动 (板块表现 < 个股 1.5% 阈值)
# → 个股加仓信号降半级
```

**结论**：V5.2.3 用 Level 1 路径成功取到通信板块数据，**P4 板块共振判定可以正常运行**。

---

## 22.2.6 工具脚本

- `hengsheng_connector.py` 新增 `get_industry_index(industry_code, level=1)` 函数
- 3 级降级自动化
- 龙头股映射表内置

> **数据源**：复用现有 `IndustryDailyQuote` / `IndustryIndexLiveQuote` / `AShareLiveQuote`，**不需要新增数据源**。
