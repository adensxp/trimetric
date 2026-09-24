#!/bin/bash
# 高量战法 10 个 cron 任务批量重建脚本
# 用途：在新的 Mavis 环境里重建所有定时任务
# 用法：bash rebuild_cron_tasks.sh
# 前置：mavis CLI 可用，已登录

set -e

echo "======================================"
echo "高量战法 10 个 cron 任务重建"
echo "======================================"

# 通用参数
AGENT_NAME="Mavis"
SESSION_MODE="sessionId"
SESSION_ID="me"  # 默认主 session
TIMEZONE="Asia/Shanghai"

# 重建函数
create_cron() {
  local name="$1"
  local schedule="$2"
  local prompt="$3"
  local prompt_file="$4"

  echo ""
  echo "📌 重建任务: $name (schedule: $schedule)"

  if [ -n "$prompt_file" ] && [ -f "$prompt_file" ]; then
    mavis cron create \
      --agent_name="$AGENT_NAME" \
      --cron_name="$name" \
      --schedule="$schedule" \
      --prompt_file="$prompt_file" \
      --timezone="$TIMEZONE" \
      --session.mode="$SESSION_MODE" \
      --session.session_id="$SESSION_ID"
  else
    mavis cron create \
      --agent_name="$AGENT_NAME" \
      --cron_name="$name" \
      --schedule="$schedule" \
      --prompt="$prompt" \
      --timezone="$TIMEZONE" \
      --session.mode="$SESSION_MODE" \
      --session.session_id="$SESSION_ID"
  fi
}

# 1) stock-db-daily-update (16:00)
create_cron "stock-db-daily-update" "0 16 * * 1-5" "" "$(dirname "$0")/prompts/prompt_stock_db_daily_update.txt"

# 2) daily-pool-analysis (16:30)
create_cron "daily-pool-analysis" "30 16 * * 1-5" "" "$(dirname "$0")/prompts/prompt_daily_pool_analysis.txt"

# 3) morning-prep-list (9:00)
create_cron "morning-prep-list" "0 9 * * 1-5" "" "$(dirname "$0")/prompts/prompt_morning_prep_list.txt"

# 4) v5-3-30-monitor-935 (9:35)
create_cron "v5-3-30-monitor-935" "35 9 * * 1-5" "" "$(dirname "$0")/prompts/prompt_monitor_935.txt"

# 5) v5-3-30-monitor-1000 (10:00)
create_cron "v5-3-30-monitor-1000" "0 10 * * 1-5" "" "$(dirname "$0")/prompts/prompt_monitor_1000.txt"

# 6) v5-3-30-monitor-1100 (11:00)
create_cron "v5-3-30-monitor-1100" "0 11 * * 1-5" "" "$(dirname "$0")/prompts/prompt_monitor_1100.txt"

# 7) v5-3-30-monitor-1330 (13:30)
create_cron "v5-3-30-monitor-1330" "30 13 * * 1-5" "" "$(dirname "$0")/prompts/prompt_monitor_1330.txt"

# 8) v5-3-30-monitor-1430 (14:30)
create_cron "v5-3-30-monitor-1430" "30 14 * * 1-5" "" "$(dirname "$0")/prompts/prompt_monitor_1430.txt"

# 9) daily-quality-check (8:00，质量降级监控)
create_cron "daily-quality-check" "0 8 * * 1-5" "" "$(dirname "$0")/prompts/prompt_daily_quality_check.txt"

# 10) pool-rotation (每年 1/7 月 1 日 9:00，股票池半年轮替)
create_cron "pool-rotation" "0 9 1 1,7 *" "" "$(dirname "$0")/prompts/prompt_pool_rotation.txt"

echo ""
echo "======================================"
echo "✅ 10 个任务重建完成！"
echo "======================================"
echo ""
echo "验证任务列表："
mavis cron list --agent_name="$AGENT_NAME" | grep -E "cron_name|schedule" | head -20
