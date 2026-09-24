# 高量战法定时任务（8 个 cron）

> v5.3.30 完整版。8 个 cron 任务的 prompt + 重建脚本都包含在压缩包中。

## 📁 文件结构

```
cron/
├── README.md                          # 本文档
├── cron_tasks_8.json                  # 8 个任务元数据（task_id + schedule + 用途）
├── rebuild_cron_tasks.sh              # ⭐ 批量重建脚本（新建环境时用）
└── prompts/                           # 8 个完整 prompt 文本
    ├── prompt_stock_db_daily_update.txt   # 16:00
    ├── prompt_daily_pool_analysis.txt     # 16:30
    ├── prompt_morning_prep_list.txt       # 9:00
    ├── prompt_monitor_935.txt             # 9:35
    ├── prompt_monitor_1000.txt            # 10:00
    ├── prompt_monitor_1100.txt            # 11:00
    ├── prompt_monitor_1330.txt            # 13:30
    └── prompt_monitor_1430.txt            # 14:30
```

## 8 个任务一览

| # | 任务名 | 时间 | 周期 | 作用 |
|---|--------|------|------|------|
| 1 | stock-db-daily-update | 16:00 | 周一-五 | 拉数据 + update-pnl + review |
| 2 | daily-pool-analysis | 16:30 | 周一-五 | 三策略全量评分 |
| 3 | morning-prep-list | 9:00 | 周一-五 | 早盘预备池 + 实时价 |
| 4 | v5-3-18-monitor-935 | 9:35 | 周一-五 | 早盘完整监控 |
| 5 | v5-3-18-monitor-1000 | 10:00 | 周一-五 | 盘中完整监控 |
| 6 | v5-3-18-monitor-1100 | 11:00 | 周一-五 | 盘中完整监控 |
| 7 | v5-3-18-monitor-1330 | 13:30 | 周一-五 | 午盘完整监控 |
| 8 | v5-3-18-monitor-1430 | 14:30 | 周一-五 | 尾盘完整监控 |

## 🔧 重建方法（迁移到新环境时）

### 方法 A：批量脚本（推荐）

```bash
cd 高量战法/cron
bash rebuild_cron_tasks.sh
```

脚本会：
1. 读取 `prompts/` 下的 8 个完整 prompt
2. 用 `mavis cron create` 逐个创建
3. 全部在主 session 跑（mode=sessionId, session_id=me）
4. 输出创建结果

**前置条件**：
- `mavis` CLI 可用
- 已登录（`mavis login`）
- `Mavis` agent 已创建

### 方法 B：手动逐个重建

如果脚本有问题，参考 `cron_tasks_8.json` 里的元数据，对照每个 `prompts/prompt_*.txt` 文件，手动调：

```bash
mavis cron create \
  --agent_name=Mavis \
  --cron_name=stock-db-daily-update \
  --schedule="0 16 * * 1-5" \
  --prompt_file=cron/prompts/prompt_stock_db_daily_update.txt \
  --timezone="Asia/Shanghai" \
  --session.mode=sessionId \
  --session.session_id=me
```

## ⚠️ v5.3.30 提示

- v5.3.30 在 daily_pool_analysis_v3.py / monitor.py 里集成了 W16/W4/W20/W22
- 8 个 cron 任务调的就是这两个脚本
- **不需要改 cron prompt**（v5.3.25 → v5.3.30 是脚本升级，不是任务升级）

## 📋 任务 ID 备份

如果保留原任务 ID，参见 `cron_tasks_8.json`。新建的任务会有新的 task_id（不影响功能）。
