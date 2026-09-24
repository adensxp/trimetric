#!/usr/bin/env bash
# 度量衡 trimetric v5.3.31 一键安装（Linux + Python >= 3.8，纯标准库零第三方依赖）
# 用法 1：git clone 本仓库后，在仓库目录执行 bash install.sh
# 用法 2：curl -fsSL https://raw.githubusercontent.com/adensxp/trimetric/main/install.sh | bash
set -e

# curl|bash 模式下自动克隆仓库
if [ ! -f scripts/monitor.py ]; then
  echo "📦 未检测到项目文件，开始克隆..."
  git clone https://github.com/adensxp/trimetric.git trimetric
  cd trimetric
fi
cd "$(dirname "$0")"
echo ""
echo "================ 度量衡 trimetric v5.3.31 安装 ================"
echo ""

# 1) Python 版本检查
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3（需要 >= 3.8）"; exit 1
fi
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" \
  || { echo "❌ Python 版本过低（需要 >= 3.8）"; exit 1; }
echo "✅ Python $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"

# 2) sqlite3 模块检查
python3 -c "import sqlite3" 2>/dev/null \
  || { echo "❌ 缺少 sqlite3 模块（Debian/Ubuntu: sudo apt install python3-sqlite）"; exit 1; }
echo "✅ sqlite3 就绪"

# 3) 初始化四库（数据默认 ~/gaoliang/，可用 TRIMETRIC_DATA_ROOT 重定向）
python3 scripts/init_db.py

# 4) 网络链路验证（拉 2 只真实行情）
echo ""
echo "—— 行情链路验证 ——"
python3 scripts/fetch_data.py --codes 600519.SH,000001.SZ

# 5) 33 项单元测试
echo ""
echo "—— 安装自检 ——"
python3 tests/test_rules.py > /dev/null && echo "✅ 规则单测 31 项全绿"
python3 tests/test_config.py > /dev/null && echo "✅ 配置单测 2 项全绿"

echo ""
echo "================ 安装完成 🎉 ================"
echo ""
echo "下一步："
echo "  1. 全量拉取 494 只股票行情（约 12 分钟）："
echo "     python3 scripts/fetch_data.py --limit 494"
echo "  2. 立即体验盘中监控："
echo "     python3 scripts/monitor.py"
echo "  3. 定时运行（可选），把以下内容加入 crontab -e："
cat << 'CRON'
35  9 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
 0 10 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
 0 11 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
30 13 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
30 14 * * 1-5  cd <项目路径> && python3 scripts/monitor.py
 0 16 * * 1-5  cd <项目路径> && python3 scripts/fetch_data.py --limit 494
30 16 * * 1-5  cd <项目路径> && python3 scripts/daily_pool_analysis_v3.py
CRON
echo ""
echo "详细文档见 README.md（5 分钟上手 / 环境变量表 / 四库架构）"
