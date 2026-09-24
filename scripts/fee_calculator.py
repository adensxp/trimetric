#!/usr/bin/env python3
"""
A 股手续费计算器
=================

支持不同交易所（上交所/深交所/北交所）不同费率配置。

费率结构：
1. 印花税：仅卖出收取（2023-08 降为 0.05%）
2. 过户费：双向收取（2022 沪深统一 0.001%）
3. 券商佣金：双向收取（默认万 2.5，最低 5 元）

交易所识别：
- SSE 上交所：60xxxx, 68xxxx, 90xxxx
- SZSE 深交所：00xxxx, 20xxxx, 30xxxx
- BSE 北交所：8xxxxx, 92xxxx

用法：
  from fee_calculator import calculate_fee
  fees = calculate_fee('002475.SZ', 'BUY', 16704.0)
  # {'commission': 5.0, 'stamp_tax': 0, 'transfer_fee': 0.17, 'total_fee': 5.17, 'net_amount': 16709.17, 'exchange': 'SZSE'}
"""
import config
import sqlite3
import json
import re

DB = config.PORTFOLIO_DB

# 默认费率（A 股通用）
DEFAULT_RATES = {
    'stamp_tax_rate': 0.0005,  # 0.05%（仅卖出）
    'transfer_fee_rate': 0.00001,  # 0.001%（买卖双向）
    'commission_rate': 0.00025,  # 万 2.5（买卖双向）
    'commission_min': 5.0,  # 最低 5 元
}

# 交易所识别
def identify_exchange(code):
    """根据股票代码识别交易所"""
    code_no_suffix = code.split('.')[0] if '.' in code else code
    # 上交所：60xxxx, 68xxxx, 90xxxx
    if re.match(r'^(60|68|90)\d{4}$', code_no_suffix):
        return 'SSE'
    # 深交所：00xxxx, 20xxxx, 30xxxx
    if re.match(r'^(00|20|30)\d{4}$', code_no_suffix):
        return 'SZSE'
    # 北交所：8xxxxx, 92xxxx
    if re.match(r'^(8|92)\d{4,5}$', code_no_suffix):
        return 'BSE'
    return 'UNKNOWN'


# 交易所特殊费率
EXCHANGE_RATES = {
    'SSE': DEFAULT_RATES.copy(),
    'SZSE': DEFAULT_RATES.copy(),
    'BSE': {
        'stamp_tax_rate': 0.0005,  # 北交所印花税也是 0.05%
        'transfer_fee_rate': 0.00001,  # 0.001%
        'commission_rate': 0.0003,  # 北交所佣金通常更高（约万 3）
        'commission_min': 5.0,
    },
}


def get_rates(exchange):
    """获取指定交易所的费率"""
    return EXCHANGE_RATES.get(exchange, DEFAULT_RATES)


def calculate_fee(code, action, amount):
    """
    计算手续费
    code: 股票代码（如 '002475.SZ'）
    action: 'BUY' / 'SELL'
    amount: 成交金额
    返回 dict: commission / stamp_tax / transfer_fee / total_fee / net_amount / exchange
    """
    exchange = identify_exchange(code)
    rates = get_rates(exchange)

    # 1. 印花税：仅卖出
    stamp_tax = amount * rates['stamp_tax_rate'] if action == 'SELL' else 0

    # 2. 过户费：双向
    transfer_fee = amount * rates['transfer_fee_rate']

    # 3. 券商佣金：双向，最低 5 元
    commission = max(amount * rates['commission_rate'], rates['commission_min'])

    total_fee = commission + stamp_tax + transfer_fee

    # 实际到账/支付
    if action == 'SELL':
        net_amount = amount - total_fee  # 卖出：减手续费
    else:  # BUY
        net_amount = amount + total_fee  # 买入：加手续费

    return {
        'exchange': exchange,
        'commission': round(commission, 2),
        'stamp_tax': round(stamp_tax, 2),
        'transfer_fee': round(transfer_fee, 2),
        'total_fee': round(total_fee, 2),
        'net_amount': round(net_amount, 2),
    }


def update_trade_fee(trade_id):
    """根据 trades 表的 code/action/amount 重新算手续费"""
    conn = sqlite3.connect(config.POSITIONS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
    trade = cur.fetchone()
    if not trade:
        conn.close()
        return None
    fees = calculate_fee(trade['code'], trade['action'], trade['amount'])
    conn.execute("""
        UPDATE trades SET
            commission=?, stamp_tax=?, transfer_fee=?,
            total_fee=?, net_amount=?, exchange=?
        WHERE id=?
    """, (fees['commission'], fees['stamp_tax'], fees['transfer_fee'],
          fees['total_fee'], fees['net_amount'], fees['exchange'], trade_id))
    conn.commit()
    conn.close()
    return fees


def save_rates_to_meta():
    """保存费率到 meta 表"""
    conn = sqlite3.connect(config.CAPITAL_DB)
    conn.row_factory = sqlite3.Row
    now = '2026-09-04 16:18:00'
    rates_str = json.dumps({
        'default': DEFAULT_RATES,
        'exchanges': EXCHANGE_RATES,
        'note': 'A 股交易费率 2024 标准：印花税 0.05%（仅卖出） + 过户费 0.001%（双向） + 佣金万 2.5（双向，最低 5 元）',
    }, ensure_ascii=False, indent=2)
    conn.execute("""
        INSERT OR REPLACE INTO meta (key, value, updated_at)
        VALUES ('a_stock_fee_rates', ?, ?)
    """, (rates_str, now))
    conn.commit()
    conn.close()
    print("✅ 费率配置已保存到 meta 表")


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 fee_calculator.py calc <code> <BUY|SELL> <amount>")
        print("  python3 fee_calculator.py update <trade_id>")
        print("  python3 fee_calculator.py save")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'calc':
        if len(sys.argv) < 5:
            print("用法: python3 fee_calculator.py calc <code> <BUY|SELL> <amount>")
            sys.exit(1)
        code, action, amount = sys.argv[2], sys.argv[3], float(sys.argv[4])
        fees = calculate_fee(code, action, amount)
        print(f"\n=== {code} {action} {amount:,.2f} 元 ===")
        print(f"  交易所: {fees['exchange']}")
        print(f"  佣金:   {fees['commission']:>6.2f} 元")
        print(f"  印花税: {fees['stamp_tax']:>6.2f} 元")
        print(f"  过户费: {fees['transfer_fee']:>6.2f} 元")
        print(f"  总费用: {fees['total_fee']:>6.2f} 元")
        print(f"  净金额: {fees['net_amount']:>10,.2f} 元")
    elif cmd == 'update':
        if len(sys.argv) < 3:
            print("用法: python3 fee_calculator.py update <trade_id>")
            sys.exit(1)
        trade_id = int(sys.argv[2])
        fees = update_trade_fee(trade_id)
        if fees:
            print(f"✅ Trade #{trade_id} 手续费已更新")
            for k, v in fees.items():
                print(f"  {k}: {v}")
    elif cmd == 'save':
        save_rates_to_meta()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
