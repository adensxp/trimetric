---
reference_for: 高量战法 v5.2.2
section: 21.0 数据校验模块（V1-V10 详细）
parent: SKILL.md
---

> **本文件是《高量战法》SKILL.md 的 reference 章节**
> 详细规则、阈值表、实战案例见此文件
> 主文件仅保留核心规则，需要时按需加载本文件

---

### 21.0 数据校验模块（最高优先级 · 强制前置）

> **核心理念**：**数据错了什么都白干**——所有 P10-P19 规则的前提都是"数据本身正确"。前复权错误、除权日未识别、接口错位、字段缺失等都会让所有信号失效。

#### 21.0.1 数据校验检查项（10 条）

| 编号 | 检查项 | 检查方法 | 失败处理 |
|---|---|---|---|
| V1 | 接口数据完整性 | 必须返回至少 60 个交易日 | **拒绝分析** |
| V2 | OHLC 关系正确 | `low ≤ min(open, close) ≤ max(open, close) ≤ high` | **拒绝该 K 线** |
| V3 | 量价符号合理 | 收盘价 > 0，成交量 ≥ 0 | **拒绝该 K 线** |
| V4 | 日期连续性 | 下一交易日 = 上交易日 + 1-5 个日历日（排除周末/节假日） | **记录跳空** |
| V5 | 复权状态标注 | 必须明确是"前复权"/"后复权"/"不复权" | **拒绝分析** |
| V6 | 除权除息日识别 | 检测到价格跳变 + 跳变比例与公告分红匹配 | **标注 + 重新计算支撑压力** |
| V7 | 实时价合理性 | 实时价应在 [近 5 日最低, 近 5 日最高 × 1.1] 区间 | **警告：可能盘中剧变** |
| V8 | 数据基准日标注 | 必须明确"基于 YYYY-MM-DD HH:MM" | **拒绝分析** |
| V9 | 至少 2 个数据源交叉验证 | 同一日 K 线从 2 个接口取值，差异 > 0.5% 报警 | **优先用主流接口** |
| V10 | 字段命名一致性 | open/close/high/low/vol 字段必须存在 | **拒绝分析** |

#### 21.0.2 除权除息日识别算法

```python
# 算法：除权日识别（适用于前复权数据）
def detect_ex_right_days(klines):
    """
    检测除权除息日
    前复权数据特征：
      - 价格从前一日到除权日会出现明显跳空（向下跳空 = 除权）
      - 但跳空比例应与"每股分红 × 除权前总股本 / 流通市值"匹配
    实战识别：
      1. 检测日线收盘价与前一日收盘价差异 > 5%（非涨跌停）
      2. 同时该日出现明显放量（>5日均量 × 1.5）
      3. → 标记为疑似除权日，待人工确认
    """
    ex_right_days = []
    for i in range(1, len(klines)):
        prev_close = klines[i-1]['close']
        curr_close = klines[i]['close']
        chg_ratio = (curr_close - prev_close) / prev_close
        if abs(chg_ratio) > 0.05 and abs(chg_ratio) < 0.30:
            # 非涨跌停范围
            if klines[i]['vol'] > sum([klines[j]['vol'] for j in range(max(0,i-4), i)]) / 5 * 1.5:
                ex_right_days.append({
                    'date': klines[i]['date'],
                    'prev_close': prev_close,
                    'curr_close': curr_close,
                    'gap_pct': chg_ratio * 100,
                    'note': '疑似除权除息日'
                })
    return ex_right_days
```

**实战案例**：
- 紫金矿业 2026-06-26：前收 25.54 → 收 25.10 = -1.72%，但同日公告 10派3.8元（除权日）→ 这是**除权后效应**，不是真跌
- 中际旭创 2026-04-30：前收 845.00 → 收 857.50 = +1.48%，但同日公告 10派10元 → **不匹配，需复核**
- 伊利股份 2026-06-05：前收 25.67 → 收 25.67 = 0%，同日公告 10派9元 → **价格未调整，需查复权状态**

#### 21.0.3 V1-V10 自动校验脚本

**代码实现**：`data_validator.py` 包含 10 个校验函数

```python
def validate_klines(klines, stock_code, data_source):
    """
    输入：K线列表
    输出：(是否通过, 警告列表, 阻断原因)
    """
    warnings = []
    
    # V1: 完整性
    if len(klines) < 60:
        return False, [], f"V1 失败: 仅 {len(klines)} 条 K 线，需 ≥ 60 条"
    
    for i, k in enumerate(klines):
        # V2: OHLC 关系
        if not (k['low'] <= min(k['open'], k['close']) <= max(k['open'], k['close']) <= k['high']):
            return False, [], f"V2 失败: 第 {i} 条 OHLC 关系错误"
        
        # V3: 量价符号
        if k['close'] <= 0 or k['vol'] < 0:
            return False, [], f"V3 失败: 第 {i} 条 量价符号错误"
    
    # V4: 日期连续性（提示性）
    # V5: 复权状态（数据源自带）
    # V6: 除权日识别
    ex_right_days = detect_ex_right_days(klines)
    if ex_right_days:
        warnings.append(f"V6: 检测到 {len(ex_right_days)} 个疑似除权日: {[d['date'] for d in ex_right_days]}")
    
    # V7-V10: 实时价 + 数据基准日 + 多源验证
    # ...
    
    return True, warnings, None
```

#### 21.0.4 校验失败的处理流程

```
数据校验
  ├─ V1/V2/V3 失败 → 立即拒绝，不进入 P1-P19
  ├─ V4 失败 → 警告，继续但标注"数据可能不全"
  ├─ V5/V10 失败 → 拒绝，必须明确数据状态
  ├─ V6 检测到除权日 → 标注，重新计算受影响的高量支撑/压力
  ├─ V7 失败 → 警告"实时价异常，可能数据未更新"
  ├─ V8 失败 → 拒绝，必须明确数据基准日
  └─ V9 失败 → 多源对比后选择主流接口
```

