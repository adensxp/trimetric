#!/usr/bin/env python3
"""
v5.3.30 W22 6 护城河分类

6 护城河：
- 文化优势 (Cultural)
- 品牌优势 (Brand)
- 独特资源 (Unique Resource)
- 效率优势 (Efficiency)
- 强网络效应 (Network Effect)
- 高转换成本 (Switching Cost)

数据：需要人工标注（基于行业和公司分析）
"""
import config
import sqlite3

DB_PATH = config.STOCK_DB

# 行业映射（基于申万行业）
INDUSTRY_MOATS = {
    '食品饮料': ['品牌优势', '独特资源'],
    '医药生物': ['品牌优势', '效率优势', '独特资源'],
    '电子': ['效率优势', '独特资源'],
    '计算机': ['高转换成本', '网络效应'],
    '通信': ['网络效应'],
    '银行': ['品牌优势'],
    '非银金融': ['品牌优势'],
    '房地产': ['独特资源'],
    '建筑装饰': ['品牌优势'],
    '电力设备': ['效率优势'],
    '机械设备': ['效率优势'],
    '汽车': ['品牌优势', '效率优势'],
    '家用电器': ['品牌优势', '效率优势'],
    '纺织服饰': ['品牌优势'],
    '轻工制造': ['品牌优势'],
    '商贸零售': ['品牌优势'],
    '社会服务': ['品牌优势'],
    '传媒': ['网络效应'],
    '农林牧渔': ['独特资源'],
    '基础化工': ['效率优势'],
    '钢铁': ['效率优势'],
    '有色金属': ['独特资源'],
    '煤炭': ['独特资源'],
    '石油石化': ['独特资源'],
    '环保': ['效率优势'],
    '交通运输': ['效率优势'],
    '公用事业': ['品牌优势'],
    '综合': [],
    '美容护理': ['品牌优势'],
}


def init_db():
    """创建 moats 表"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS stock_moats (
            stock_code TEXT PRIMARY KEY,
            industry TEXT,
            moats TEXT,
            moat_count INTEGER DEFAULT 0,
            score REAL DEFAULT 0,
            updated_at TEXT
        )
    ''')
    conn.commit()
    conn.close()


def get_stock_industry(code):
    """获取一只票的行业（从 stock_info）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT industry FROM stock_info WHERE stock_code=?', (code,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_stock_moats(code):
    """获取一只票的护城河（自动基于行业判断）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute('SELECT industry, moats, moat_count, score FROM stock_moats WHERE stock_code=?', (code,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {'industry': row[0], 'moats': row[1].split(',') if row[1] else [], 'moat_count': row[2], 'score': row[3]}
    return None


def auto_classify_all():
    """自动为所有 226 只票分类护城河（基于行业）"""
    import json
    with open(config.STOCK_POOL_226) as f:
        all_codes = []
        for batch in json.load(f)['batches'].values():
            all_codes.extend(batch)
    
    conn = sqlite3.connect(DB_PATH)
    import time
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    for code in all_codes:
        industry = get_stock_industry(code)
        if not industry:
            continue
        # 简化：从 industry 提取前 2 字
        ind_key = industry[:4]
        moats = []
        for k, v in INDUSTRY_MOATS.items():
            if k in industry:
                moats = v
                break
        moat_count = len(moats)
        score = moat_count * 0.5  # 每个护城河 0.5 分
        conn.execute('INSERT OR REPLACE INTO stock_moats VALUES (?, ?, ?, ?, ?, ?)',
                    (code, industry, ','.join(moats), moat_count, score, now))
    conn.commit()
    print(f"✅ {len(all_codes)} 只票护城河已分类")
    conn.close()


if __name__ == '__main__':
    init_db()
    auto_classify_all()
    
    # 测试
    for code in ['601100.SH', '600519.SH', '000333.SZ', '600276.SH']:
        m = get_stock_moats(code)
        if m:
            print(f"{code}: {m['industry']} → {m['moats']} (+{m['score']} 分)")
