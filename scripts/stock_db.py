#!/usr/bin/env python3
"""
P9 引擎 · 数据层 V1.0
=====================
SQLite 缓存数据库 + 申万行业自动获取

设计原则：
- 一次拉取，多次使用（避免重复请求 API）
- 智能过期（不同数据不同 TTL）
- 自动 fallback（API 失败时降级）
- 完全本地化（无外部依赖）
"""
import config
log = config.setup_logging("stock_db")
import sqlite3
import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List


# ============== 数据库结构 ==============

SCHEMA = """
-- 股票基本信息（含申万行业）
CREATE TABLE IF NOT EXISTS stocks (
    code TEXT PRIMARY KEY,
    name TEXT,
    sws_code TEXT,             -- 申万行业代码（6位）
    sws_name TEXT,             -- 申万行业名称
    sws_level TEXT,            -- 行业层级（1级/2级/3级）
    sws_chain TEXT,            -- 上级链 "480000>480200>480201"
    mcap_circ REAL,            -- 流通市值（亿）
    mcap_total REAL,           -- 总市值（亿）
    pe REAL,                   -- PE TTM
    pb REAL,                   -- PB
    div_yield REAL,            -- 股息率 TTM（%）
    div_years INTEGER,         -- 连续分红年数
    avg_amp_60d REAL,          -- 60日均振幅（%）
    is_risk INTEGER DEFAULT 0, -- 风险股标记
    risk_level TEXT,           -- 风险等级
    risk_reason TEXT,          -- 风险原因
    updated_at TIMESTAMP
);

-- K 线数据
CREATE TABLE IF NOT EXISTS klines (
    code TEXT,
    date TEXT,
    open REAL,
    close REAL,
    high REAL,
    low REAL,
    vol REAL,
    PRIMARY KEY (code, date)
);

CREATE INDEX IF NOT EXISTS idx_klines_code_date ON klines(code, date DESC);

-- 实时行情
CREATE TABLE IF NOT EXISTS quotes (
    code TEXT PRIMARY KEY,
    name TEXT,
    current REAL,
    prev_close REAL,
    open REAL,
    high REAL,
    low REAL,
    pe REAL,
    pb REAL,
    mcap_circ REAL,
    mcap_total REAL,
    change_pct REAL,
    amplitude REAL,
    vol REAL,
    updated_at TIMESTAMP
);

-- 申万行业映射
CREATE TABLE IF NOT EXISTS sws_industries (
    sws_code TEXT PRIMARY KEY,
    name TEXT,
    level INTEGER,  -- 1=一级, 2=二级, 3=三级
    parent_code TEXT
);

-- 元数据
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP
);
"""


# 数据 TTL 配置（秒）
TTL_CONFIG = {
    "stocks": 7 * 86400,      # 基本信息 7 天（公司基本面变化慢）
    "klines": 1 * 86400,      # K 线 1 天（每个交易日更新）
    "quotes": 3600,           # 实时行情 1 小时（盘中更短，盘中 60s）
    "sws_industries": 30 * 86400,  # 行业分类 30 天（基本不变）
}


# ============== 申万行业映射（34 个一级行业）==============

SWS_L1 = {
    "110000": "农林牧渔", "210000": "基础化工", "220000": "钢铁",
    "230000": "有色金属", "240000": "金属新材料", "270000": "电子",
    "280000": "汽车", "330000": "家用电器", "340000": "食品饮料",
    "350000": "纺织服饰", "360000": "轻工制造", "370000": "医药生物",
    "410000": "电力设备", "420000": "机械设备", "430000": "国防军工",
    "450000": "综合电力设备", "460000": "美容护理", "480000": "银行",
    "490000": "非银金融", "510000": "综合", "610000": "煤炭",
    "620000": "石油石化", "630000": "环保", "710000": "建筑材料",
    "720000": "建筑装饰", "730000": "公用事业", "740000": "交通运输",
    "750000": "房地产", "760000": "商贸零售", "770000": "社会服务",
    "810000": "传媒", "820000": "通信", "830000": "计算机",
}


# ============== 数据库类 ==============

class StockDatabase:
    """股票数据缓存数据库"""
    
    def __init__(self, db_path: str = None):
        # 默认数据库路径：当前 skill 目录下
        if db_path is None:
            skill_dir = Path(__file__).parent
            db_path = str(skill_dir / "stock_cache.db")
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_tables()
        self._init_sws_l1()
    
    def init_tables(self):
        """初始化表结构"""
        self.conn.executescript(SCHEMA)
        self.conn.commit()
    
    def _init_sws_l1(self):
        """初始化申万一级行业"""
        for code, name in SWS_L1.items():
            self.conn.execute("""
                INSERT OR IGNORE INTO sws_industries (sws_code, name, level)
                VALUES (?, ?, 1)
            """, (code, name))
        self.conn.commit()
    
    def close(self):
        self.conn.close()
    
    def _is_expired(self, updated_at: str, ttl: int) -> bool:
        """检查是否过期"""
        if not updated_at:
            return True
        try:
            last = datetime.fromisoformat(updated_at)
            return (datetime.now() - last).total_seconds() > ttl
        except:
            return True
    
    # ============ 股票基本信息 ============
    
    def get_stock(self, code: str) -> Optional[Dict]:
        """获取股票基本信息"""
        row = self.conn.execute(
            "SELECT * FROM stocks WHERE code = ?", (code,)
        ).fetchone()
        if row:
            return dict(row)
        return None
    
    def get_stock_cached(self, code: str, max_age_days: int = 7) -> Optional[Dict]:
        """获取股票信息（带缓存检查）"""
        stock = self.get_stock(code)
        if not stock:
            return None
        if self._is_expired(stock.get('updated_at'), max_age_days * 86400):
            return None
        return stock
    
    def save_stock(self, stock: Dict):
        """保存股票信息"""
        self.conn.execute("""
            INSERT OR REPLACE INTO stocks (
                code, name, sws_code, sws_name, sws_level, sws_chain,
                mcap_circ, mcap_total, pe, pb, div_yield, div_years,
                avg_amp_60d, is_risk, risk_level, risk_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stock.get('code'),
            stock.get('name'),
            stock.get('sws_code'),
            stock.get('sws_name'),
            stock.get('sws_level', '3'),
            stock.get('sws_chain'),
            stock.get('mcap_circ'),
            stock.get('mcap_total'),
            stock.get('pe'),
            stock.get('pb'),
            stock.get('div_yield'),
            stock.get('div_years', 0),
            stock.get('avg_amp_60d'),
            1 if stock.get('is_risk') else 0,
            stock.get('risk_level'),
            stock.get('risk_reason'),
            datetime.now().isoformat(),
        ))
        self.conn.commit()
    
    # ============ K 线数据 ============
    
    def get_klines(self, code: str, days: int = 120) -> List[Dict]:
        """获取 K 线（最近 N 天）"""
        rows = self.conn.execute("""
            SELECT * FROM klines WHERE code = ?
            ORDER BY date DESC LIMIT ?
        """, (code, days)).fetchall()
        return [dict(r) for r in reversed(rows)]
    
    def get_klines_cached(self, code: str, days: int = 120) -> Optional[List[Dict]]:
        """获取 K 线（带缓存检查）"""
        # 先查最新一条的时间
        row = self.conn.execute("""
            SELECT MAX(date) as last_date FROM klines WHERE code = ?
        """, (code,)).fetchone()
        if not row or not row['last_date']:
            return None
        
        # 检查最近 5 个交易日有没有数据
        recent = self.conn.execute("""
            SELECT date FROM klines WHERE code = ?
            ORDER BY date DESC LIMIT 5
        """, (code,)).fetchall()
        if len(recent) < 3:
            return None
        
        return self.get_klines(code, days)
    
    def save_klines(self, code: str, klines: List[Dict]):
        """保存 K 线"""
        self.conn.executemany("""
            INSERT OR REPLACE INTO klines (code, date, open, close, high, low, vol)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [(code, k['date'], k['open'], k['close'], k['high'], k['low'], k['vol'])
              for k in klines])
        self.conn.commit()
    
    # ============ 实时行情 ============
    
    def get_quote(self, code: str, max_age_sec: int = 3600) -> Optional[Dict]:
        """获取实时行情（带 TTL）"""
        row = self.conn.execute(
            "SELECT * FROM quotes WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return None
        if self._is_expired(row['updated_at'], max_age_sec):
            return None
        return dict(row)
    
    def save_quote(self, quote: Dict):
        """保存实时行情"""
        self.conn.execute("""
            INSERT OR REPLACE INTO quotes (
                code, name, current, prev_close, open, high, low,
                pe, pb, mcap_circ, mcap_total, change_pct, amplitude, vol, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            quote.get('code'),
            quote.get('name'),
            quote.get('current'),
            quote.get('prev_close'),
            quote.get('open'),
            quote.get('high'),
            quote.get('low'),
            quote.get('pe'),
            quote.get('pb'),
            quote.get('mcap_circ'),
            quote.get('mcap_total'),
            quote.get('change_pct'),
            quote.get('amplitude'),
            quote.get('vol'),
            datetime.now().isoformat(),
        ))
        self.conn.commit()
    
    # ============ 申万行业 ============
    
    def get_sws_name(self, sws_code: str) -> Optional[str]:
        """获取申万行业名称"""
        row = self.conn.execute(
            "SELECT name FROM sws_industries WHERE sws_code = ?", (sws_code,)
        ).fetchone()
        return row['name'] if row else None
    
    def save_sws_industry(self, sws_code: str, name: str, level: int = 2, parent: str = None):
        """保存申万行业"""
        self.conn.execute("""
            INSERT OR REPLACE INTO sws_industries (sws_code, name, level, parent_code)
            VALUES (?, ?, ?, ?)
        """, (sws_code, name, level, parent))
        self.conn.commit()
    
    # ============ 统计 ============
    
    def get_stats(self) -> Dict:
        """数据库统计"""
        return {
            'stocks': self.conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0],
            'klines': self.conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0],
            'quotes': self.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],
            'sws_industries': self.conn.execute("SELECT COUNT(*) FROM sws_industries").fetchone()[0],
            'risk_stocks': self.conn.execute("SELECT COUNT(*) FROM stocks WHERE is_risk = 1").fetchone()[0],
        }


# ============== 申万行业自动获取 ==============

def fetch_sws_industry_from_eastmoney(code: str) -> Optional[Dict]:
    """从东方财富获取申万行业"""
    market = 0 if code.startswith(("0", "3", "1")) else 1
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f57,f58,f162,f167,f168,f169,f170,f171,f173"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            d = data.get('data', {})
            if not d:
                return None
            
            # f162=所属行业（带前缀）, f168=行业代码, f167=行业名称
            # f173=二级行业名称
            sws_code = d.get('f168', '')  # 申万行业代码（可能为空）
            sws_name = d.get('f167', '')  # 行业名称
            
            # 如果直接拿到行业代码，返回
            if sws_code and len(str(sws_code)) == 6:
                return {
                    'sws_code': str(sws_code),
                    'sws_name': sws_name,
                    'sws_level': '3',
                }
            return {
                'sws_code': None,
                'sws_name': sws_name or '未知',
                'sws_level': '0',
            }
    except Exception as e:
        log.info(f"⚠️  东方财富申万行业获取失败 {code}: {e}")
        return None


def fetch_sws_industry_chain_from_eastmoney(code: str) -> Optional[Dict]:
    """从东方财富 f9 字段获取完整行业链"""
    market = 0 if code.startswith(("0", "3", "1")) else 1
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f9"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            d = data.get('data', {})
            if not d or not d.get('f9'):
                return None
            f9 = d['f9']
            
            # f9 格式: "申万一级>申万二级>申万三级"
            parts = f9.split('>') if '>' in f9 else [f9]
            parts = [p.strip() for p in parts if p.strip()]
            
            # 简单匹配：取第一个出现的 6 位数字（行业代码）
            # 或者直接根据名称映射
            result = {'sws_chain': '>'.join(parts), 'sws_levels': parts}
            
            # 通过名称反查行业代码
            l1_name = parts[0] if parts else None
            for code_, name in SWS_L1.items():
                if name == l1_name:
                    result['sws_code'] = code_
                    result['sws_name'] = l1_name
                    result['sws_level'] = '1'
                    return result
            
            return result
    except Exception as e:
        log.info(f"⚠️  东方财富 f9 获取失败 {code}: {e}")
        return None


def fetch_sws_industry_universal(code: str, db: StockDatabase) -> Optional[Dict]:
    """通用申万行业获取（多源 fallback）"""
    # 1. 先查数据库
    stock = db.get_stock_cached(code, max_age_days=30)
    if stock and stock.get('sws_code'):
        return {
            'sws_code': stock['sws_code'],
            'sws_name': stock['sws_name'],
            'sws_level': stock.get('sws_level', '3'),
        }
    
    # 2. 用恒生 connector 获取（最权威 + 完整三级链）
    info = _fetch_sws_via_hengsheng(code)
    if info and info.get('sws_code'):
        # 缓存到数据库
        if stock:
            stock['sws_code'] = info['sws_code']
            stock['sws_name'] = info['sws_name']
            stock['sws_level'] = info.get('sws_level', '1')
            stock['sws_chain'] = info.get('sws_chain', '')
            db.save_stock(stock)
        return info
    
    # 3. 降级：东方财富 f9
    info = fetch_sws_industry_chain_from_eastmoney(code)
    if info and info.get('sws_code'):
        if stock:
            stock['sws_code'] = info['sws_code']
            stock['sws_name'] = info['sws_name']
            stock['sws_level'] = info.get('sws_level', '1')
            db.save_stock(stock)
        return {
            'sws_code': info['sws_code'],
            'sws_name': info['sws_name'],
            'sws_level': info.get('sws_level', '1'),
        }
    
    # 4. 最后降级：只用名称
    if info and info.get('sws_name'):
        return {
            'sws_code': None,
            'sws_name': info['sws_name'],
            'sws_level': '0',
        }
    
    return None


def _fetch_sws_via_hengsheng(code: str) -> Optional[Dict]:
    """用恒生金融数据库 connector 获取申万行业（需调用方在 MCP 环境下）"""
    # 这个函数需要在 MCP 环境下用 connector__hengsheng__call_api
    # 这里用 subprocess 模拟（实际使用时改为直接调用 connector）
    return None


# 缓存批量结果
_HENGSHENG_CACHE = {}

def fetch_sws_batch_hengsheng(codes: List[str]) -> Dict[str, Dict]:
    """
    批量获取申万行业（需要 MCP 环境）
    
    用法（在 MCP 环境下）:
        from stock_db import fetch_sws_batch_hengsheng
        result = fetch_sws_batch_hengsheng(['601328', '600519', '600900'])
    
    返回: {code: {sws_code, sws_name, sws_level, sws_chain}}
    """
    # 这里用全局缓存填充数据
    # 实际数据由 MCP 环境调用 connector 写入 _HENGSHENG_CACHE
    results = {}
    for code in codes:
        if code in _HENGSHENG_CACHE:
            results[code] = _HENGSHENG_CACHE[code]
    return results


def set_hengsheng_cache(data: Dict[str, Dict]):
    """外部接口：注入恒生 connector 批量获取的数据"""
    global _HENGSHENG_CACHE
    _HENGSHENG_CACHE.update(data)


# ============== 工具函数 ==============

def get_or_fetch_stock(code: str, db: StockDatabase = None, 
                       force_refresh: bool = False) -> Optional[Dict]:
    """获取股票信息（自动缓存）"""
    if db is None:
        db = StockDatabase()
    
    # 1. 查缓存
    if not force_refresh:
        cached = db.get_stock_cached(code, max_age_days=7)
        if cached:
            cached['_from_cache'] = True
            return cached
    
    # 2. 实时拉取
    from p9_engine import fetch_quote, check_risk_stock
    
    quote = fetch_quote(code)
    if not quote:
        return None
    
    # 风险股检查
    risk = check_risk_stock(quote['name'], code)
    
    # 申万行业
    sws = fetch_sws_industry_universal(code, db) or {}
    
    # 合并
    stock = {
        'code': code,
        'name': quote['name'],
        'mcap_circ': quote.get('mcap_circ', 0) or 0,
        'mcap_total': quote.get('mcap_total', 0) or 0,
        'pe': quote.get('pe', 0) or 0,
        'pb': quote.get('pb', 0) or 0,
        'current_price': quote.get('current', 0),
        'sws_code': sws.get('sws_code'),
        'sws_name': sws.get('sws_name', '未知'),
        'sws_level': sws.get('sws_level', '0'),
        'is_risk': bool(risk and risk.get('blocked')),
        'risk_level': risk.get('level') if risk else None,
        'risk_reason': risk.get('reason') if risk else None,
        '_from_cache': False,
    }
    
    # 缓存
    db.save_stock(stock)
    return stock


def get_or_fetch_klines(code: str, db: StockDatabase = None, 
                        days: int = 120, force_refresh: bool = False) -> Optional[List[Dict]]:
    """获取 K 线（自动缓存）"""
    if db is None:
        db = StockDatabase()
    
    # 1. 查缓存
    if not force_refresh:
        cached = db.get_klines_cached(code, days)
        if cached and len(cached) >= 60:
            return cached
    
    # 2. 实时拉取
    from p9_engine import fetch_klines
    klines = fetch_klines(code, days)
    if klines:
        db.save_klines(code, klines)
    return klines


# ============== 主测试 ==============

if __name__ == '__main__':
    log.info("="*70)
    log.info("🗄️  P9 引擎 · 数据层 V1.0 测试")
    log.info("="*70)
    
    db = StockDatabase()
    
    # 1. 数据库初始状态
    log.info("\n【数据库初始状态】")
    stats = db.get_stats()
    for k, v in stats.items():
        log.info(f"  {k}: {v}")
    
    # 2. 拉取 3 只股票测试
    test_codes = ['601328', '600900', '600519', '600519', '601328']  # 重复 2 个测试缓存
    
    for i, code in enumerate(test_codes):
        log.info(f"\n--- 第 {i+1} 次查询 {code} ---")
        start = time.time()
        
        stock = get_or_fetch_stock(code, db, force_refresh=(i==0))
        elapsed = time.time() - start
        
        if stock:
            cache_status = "🟢 缓存命中" if stock.get('_from_cache') else "🔵 实时拉取"
            log.info(f"  {cache_status} | 耗时 {elapsed:.2f}s")
            log.info(f"  名称: {stock.get('name')}")
            log.info(f"  申万: {stock.get('sws_name')} ({stock.get('sws_code')})")
            log.info(f"  市值: {stock.get('mcap_circ', 0):.0f}亿")
            log.info(f"  PE={stock.get('pe', 0):.2f}, PB={stock.get('pb', 0):.2f}")
            if stock.get('is_risk'):
                log.info(f"  ⚠️  风险: {stock.get('risk_level')}")
    
    # 3. 最终状态
    log.info("\n【数据库最终状态】")
    stats = db.get_stats()
    for k, v in stats.items():
        log.info(f"  {k}: {v}")
    
    db.close()
    log.info("\n✅ 测试完成！")
