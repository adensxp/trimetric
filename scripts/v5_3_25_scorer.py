#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V5.3.25 评分系统升级
====================
基于 V5.3.25 三策略评分系统，添加：
1. 皇冠明珠加权（财务严苛筛选）
2. 股息税率系统（短线 20% / 中线 10% / 长线底仓 0%）
3. 商业判断软指标（长线/底仓专属）

数据源：portfolio.db crown_jewels + stock_data.db
"""

import config
import sqlite3
import json
import os
from datetime import datetime

# ============ 路径配置 ============
STOCK_DB = config.MASTER_DB
PORTFOLIO_DB = config.CANDIDATES_DB
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ============ 1. 皇冠明珠加载 ============

def load_crown_jewels():
    """从 portfolio.db 加载皇冠明珠"""
    if not os.path.exists(PORTFOLIO_DB):
        return {'STRICT': [], 'RELAXED': []}
    conn = sqlite3.connect(PORTFOLIO_DB)
    cur = conn.execute("SELECT code, name, roe_5y_avg, debt_5y_max, level FROM crown_jewels")
    strict = []
    relaxed = []
    for code, name, roe, debt, level in cur.fetchall():
        info = {'code': code, 'name': name, 'roe_5y_avg': roe, 'debt_5y_max': debt}
        if level == 'STRICT':
            strict.append(info)
        else:
            relaxed.append(info)
    conn.close()
    return {'STRICT': strict, 'RELAXED': relaxed}


def crown_jewel_bonus(stock_code, crown_data, base_score=0):
    """
    V5.3.25 增强版皇冠加权（阶梯式）

    阶梯（基础分门槛）：
      基础 < 5:   +0   (硬过滤, 信号不足)
      基础 5-6:   +0.3 (轻量, 信号勉强)
      基础 7-8:   +1.0 (标准, 信号良好)
      基础 9-10:  +1.5 (完整, 信号强)

    STRICT 翻倍：上述 × 2
      基础 < 5:   +0
      基础 5-6:   +0.6
      基础 7-8:   +2.0
      基础 9-10:  +3.0

    逻辑：皇冠 = 锦上添花（不是雪中送炭）
    避免低分票通过皇冠加权虚高进预备池
    """
    is_strict = False
    roe = 0
    for j in crown_data['STRICT']:
        if j['code'] == stock_code:
            is_strict = True
            roe = j['roe_5y_avg']
            break
    if not is_strict:
        for j in crown_data['RELAXED']:
            if j['code'] == stock_code:
                roe = j['roe_5y_avg']
                break
        else:
            return 0, ""

    # 阶梯加权
    if base_score < 5:
        bonus = 0
    elif base_score < 7:
        bonus = 0.6 if is_strict else 0.3
    elif base_score < 9:
        bonus = 2.0 if is_strict else 1.0
    else:  # >= 9
        bonus = 3.0 if is_strict else 1.5

    level = "严苛" if is_strict else "放宽"
    msg = f"皇冠({level}) 基础{base_score:.1f}+{bonus} ROE 5y {roe:.1f}%"
    return bonus, msg


# ============ 2. 股息税率系统 ============

DIVIDEND_TAX = {
    'SHORT_TERM': {
        'holding_period': '≤ 1 个月（T+30 短线）',
        'tax_rate': 0.20,
        'description': '股息红利税 20%'
    },
    'MID_TERM': {
        'holding_period': '1 个月 - 1 年（中线）',
        'tax_rate': 0.10,
        'description': '股息红利税 10%'
    },
    'LONG_TERM': {
        'holding_period': '> 1 年（长线/底仓）',
        'tax_rate': 0.00,
        'description': '股息红利税 0%（暂免）'
    }
}


def short_term_dividend_penalty(stock_code, strategy, dividend_payout):
    """
    短线股息税惩罚
    短线 T+30 持股 < 1 个月 = 20% 股息税
    中线 1 月-1 年 = 10%
    长线/底仓 > 1 年 = 0%
    
    用于选股时避税
    """
    if strategy == 'SHORT_TERM' and dividend_payout > 0.02:  # 派息率 > 2%
        # 短线不投高派息股（避免 20% 税）
        return -2.0, f"⚠️ 高派息 {dividend_payout*100:.1f}% → 短线 T+30 税 20%"
    return 0, ""


# ============ 3. 商业判断软指标 ============

# 商业判断维度（软指标，不是硬指标）
SOFT_METRICS = {
    'industry_position': {
        'name': '行业地位',
        'weight': 1.0,
        'description': '龙头/隐形冠军加分'
    },
    'roe_trend': {
        'name': 'ROE 趋势',
        'weight': 1.0,
        'description': '5 年 ROE 上升/平稳/下降'
    },
    'business_moat': {
        'name': '业务护城河',
        'weight': 0.5,
        'description': '行业壁垒/品牌/技术'
    },
    'management_quality': {
        'name': '管理层品质',
        'weight': 0.5,
        'description': '分红/回购/无违规'
    }
}


def load_soft_scores(stock_code):
    """
    V5.3.26 商业判断软指标加载（基于 W21 9 要素 + W22 6 护城河）

    集成 soft_scores_v5326.get_soft_scores() 的 4 维：
    - 治理结构 (0.5)
    - 现金流 (0.5) - 占位
    - 系统稳定性 (0.5)
    - 行业地位 (0.5) - 占位
    """
    try:
        from soft_scores_v5326 import get_soft_scores as _get_soft
        result = _get_soft(stock_code)
        return result
    except Exception as e:
        return {}


# ============ 4. 评分函数（V5.3.25 升级版）============
# 短线三信号 (满分 10)
# 长线 5 信号 (满分 10)
# 底仓 4 信号 (满分 10)

# 短线：技术信号 + 量价
# 长线：财务 + 趋势 + 商业判断
# 底仓：极低波动 + 财务 + 商业判断

def score_short_term_v5_3_25(stock_code, daily_data, crown_data, dividend_payout=0):
    """
    短线 V5.3.25 评分（满分 10）
    - 三信号 (满分 10)
    - 皇冠明珠加权 (+0~3)
    - 股息税惩罚 (-0~2)
    """
    base_score = daily_data.get('tech_score', 5.0)
    bonus, msg = crown_jewel_bonus(stock_code, crown_data)
    penalty, pmsg = short_term_dividend_penalty(stock_code, 'SHORT_TERM', dividend_payout)
    final = base_score + bonus + penalty
    
    result = {
        'stock_code': stock_code,
        'strategy': 'SHORT_TERM',
        'version': 'V5.3.25',
        'base_score': base_score,
        'crown_bonus': bonus,
        'dividend_penalty': penalty,
        'final_score': min(max(final, 0), 10),
        'notes': [msg, pmsg] if msg or pmsg else []
    }
    return result


def score_long_term_v5_3_25(stock_code, daily_data, crown_data, dividend_payout=0):
    """
    长线 V5.3.25 评分（满分 10）
    - 5 信号 (满分 10)
    - 皇冠明珠加权 (+0~3)
    - 商业判断软指标 (+0~2)
    - 长线持股 > 1 年股息税 0% 免
    """
    base_score = daily_data.get('lt_score', 5.0)
    bonus, msg = crown_jewel_bonus(stock_code, crown_data)
    soft = load_soft_scores(stock_code)
    soft_bonus = min(sum(s.get('score', 0) for s in soft.values()), 2.0)
    final = base_score + bonus + soft_bonus
    
    result = {
        'stock_code': stock_code,
        'strategy': 'LONG_TERM',
        'version': 'V5.3.25',
        'base_score': base_score,
        'crown_bonus': bonus,
        'soft_bonus': soft_bonus,
        'final_score': min(max(final, 0), 10),
        'notes': [msg] if msg else []
    }
    return result


def score_core_holding_v5_3_25(stock_code, daily_data, crown_data, dividend_payout=0):
    """
    底仓 V5.3.25 评分（满分 10）
    - 4 信号 (满分 10)
    - 皇冠明珠加权 (+0~3)
    - 商业判断软指标 (+0~2)
    - 底仓永久持有股息税 0% 免
    """
    base_score = daily_data.get('ch_score', 5.0)
    bonus, msg = crown_jewel_bonus(stock_code, crown_data)
    soft = load_soft_scores(stock_code)
    soft_bonus = min(sum(s.get('score', 0) for s in soft.values()), 2.0)
    final = base_score + bonus + soft_bonus
    
    result = {
        'stock_code': stock_code,
        'strategy': 'CORE_HOLDING',
        'version': 'V5.3.25',
        'base_score': base_score,
        'crown_bonus': bonus,
        'soft_bonus': soft_bonus,
        'final_score': min(max(final, 0), 10),
        'notes': [msg] if msg else []
    }
    return result


# ============ 5. 完整流程 ============

def score_all_stocks(stocks, daily_data_map, dividend_map=None):
    """
    对所有股票跑 V5.3.25 三策略评分
    """
    if dividend_map is None:
        dividend_map = {}
    
    crown_data = load_crown_jewels()
    
    results = {'SHORT_TERM': [], 'LONG_TERM': [], 'CORE_HOLDING': []}
    
    for code in stocks:
        d = daily_data_map.get(code, {})
        div = dividend_map.get(code, 0)
        
        st = score_short_term_v5_3_25(code, d, crown_data, div)
        lt = score_long_term_v5_3_25(code, d, crown_data, div)
        ch = score_core_holding_v5_3_25(code, d, crown_data, div)
        
        results['SHORT_TERM'].append(st)
        results['LONG_TERM'].append(lt)
        results['CORE_HOLDING'].append(ch)
    
    # 排序
    for k in results:
        results[k] = sorted(results[k], key=lambda x: -x['final_score'])
    
    return results


if __name__ == '__main__':
    # 单元测试
    print("="*60)
    print("V5.3.25 评分系统测试")
    print("="*60)
    
    # 加载皇冠明珠
    crown = load_crown_jewels()
    print(f"\n🏆 皇冠明珠：严苛 {len(crown['STRICT'])} 只 / 放宽 {len(crown['RELAXED'])} 只")
    
    for j in crown['STRICT'][:5]:
        print(f"  严苛: {j['code']} {j['name']} ROE {j['roe_5y_avg']:.1f}%")
    for j in crown['RELAXED'][:5]:
        print(f"  放宽: {j['code']} {j['name']} ROE {j['roe_5y_avg']:.1f}%")
    
    # 测试评分
    print()
    print("="*60)
    print("测试评分：")
    print("="*60)
    
    # 测试 600871.SH 石化油服（9-8 9:35 触发 5/5）
    test_data = {'tech_score': 5.0, 'lt_score': 5.0, 'ch_score': 5.0}
    st = score_short_term_v5_3_25('600871.SH', test_data, crown)
    print(f"短线 600871.SH: {st['final_score']}/10 - {st['notes']}")
    
    # 测试 601100.SH 恒立液压（皇冠明珠放宽）
    test_data2 = {'tech_score': 5.0, 'lt_score': 5.0, 'ch_score': 5.0}
    lt = score_long_term_v5_3_25('601100.SH', test_data2, crown)
    print(f"长线 601100.SH: {lt['final_score']}/10 - {lt['notes']}")
    
    print()
    print("股息税率系统:")
    for k, v in DIVIDEND_TAX.items():
        print(f"  {k}: {v['holding_period']} → {v['tax_rate']*100:.0f}% 税")
