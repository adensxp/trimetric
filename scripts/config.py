"""
度量衡 trimetric v5.3.31 — 集中配置
位置无关设计：仓库克隆到任何目录都能跑。
- 数据四库默认在 ~/trimetric/（TRIMETRIC_DATA_ROOT 可整体重定向）
- 股票池文件自动从仓库内 data/ 读取
- 单个路径也可用 TRIMETRIC_MASTER_DB 等环境变量精细覆盖
优先级：单路径环境变量 > TRIMETRIC_DATA_ROOT > 默认值
"""
import os

def _p(env, default):
    return os.environ.get(env, default)

# —— 仓库根（本文件在 scripts/ 下）——
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# —— 数据总根：四库 + 日志的家（一个变量整体迁移）——
DATA_ROOT = _p("TRIMETRIC_DATA_ROOT", os.path.expanduser("~/trimetric"))

# —— 脚本目录（供 sys.path 注入，即本仓库 scripts/）——
DEPLOY_DIR = _p("TRIMETRIC_DEPLOY_DIR", os.path.join(BASE_DIR, "scripts"))

# ========== 四库架构（业务域分割，v5.3.31）==========
MASTER_DB     = _p("TRIMETRIC_MASTER_DB",     os.path.join(DATA_ROOT, "master.db"))      # 总数据库：K线+年报+财务+质量快照
CANDIDATES_DB = _p("TRIMETRIC_CANDIDATES_DB", os.path.join(DATA_ROOT, "candidates.db"))  # 候选库：评分/预备池/皇冠/警报（每日可重建）
POSITIONS_DB  = _p("TRIMETRIC_POSITIONS_DB",  os.path.join(DATA_ROOT, "positions.db"))   # 持仓库：持仓/流水/事件/每日盈亏
CAPITAL_DB    = _p("TRIMETRIC_CAPITAL_DB",    os.path.join(DATA_ROOT, "capital.db"))     # 资金库：现金流/本金/费率/规则存档

# —— 过渡期兼容别名（老变量→四库；仅存量代码沿用，新代码必须用上面四库）——
STOCK_DB = MASTER_DB
STOCK_DB_BSC = MASTER_DB
PORTFOLIO_DB = POSITIONS_DB
PORTFOLIO_DB_BSC = POSITIONS_DB

# ========== 数据文件 ==========
STOCK_POOL_500   = _p("TRIMETRIC_POOL_500", os.path.join(BASE_DIR, "data", "stocks_batches_500.json"))
STOCK_POOL_226   = _p("TRIMETRIC_POOL_226", os.path.join(DATA_ROOT, "stocks_batches_226.json"))
HOLDINGS_FILE    = _p("TRIMETRIC_HOLDINGS", os.path.join(DATA_ROOT, "holdings.json"))
CHART_FILE       = _p("TRIMETRIC_CHART",    os.path.join(DATA_ROOT, "portfolio_chart.html"))
DATA_DIR         = _p("TRIMETRIC_DATA_DIR", os.path.join(DATA_ROOT, "data"))
POOL_HISTORY_DIR = _p("TRIMETRIC_POOL_HISTORY", os.path.join(DATA_ROOT, "pool_history"))


# ========== 日志（工业级改造 Phase 4）==========
import logging
from datetime import datetime

LOG_DIR = _p("TRIMETRIC_LOG_DIR", os.path.join(DATA_ROOT, "logs"))


def setup_logging(name="trimetric", level=logging.INFO):
    """统一日志：控制台 + 文件（<LOG_DIR>/<name>_<日期>.log）。
    日志目录不可用时自动降级为仅控制台；重复调用幂等。"""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        fh = logging.FileHandler(
            LOG_DIR + "/" + name + "_" + datetime.now().strftime("%Y%m%d") + ".log",
            encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    return logger
