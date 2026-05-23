# FinRobot Agent Guide

## Operating mandate

FinRobot is now an MT5-first autonomous demo-trading repo. Trade and optimize only:

- `XAUUSD`
- `BTCUSD`

## Source of truth

- Active EA: `broker/mt5/FinRobotBridgeEA.mq5`
- Runtime process list: `ecosystem.config.js`
- MT5 status/report tools: `scripts/mt5_status.py`, `scripts/mt5_trade_report.py`
- 6-hour Opencode loop: `scripts/autonomous_review_loop.py`
- Dashboard: `dashboard/app.py`
- State/logs are runtime artifacts and are intentionally gitignored.

## PM2 processes

Use only these active processes:

```bash
pm2 start ecosystem.config.js
pm2 restart mt5-terminal autonomous-review moonshot-dashboard --update-env
pm2 list
```

## MT5 bridge files

The EA uses MT5 Common Files:

- `finrobot_status.json` for heartbeat/account/symbol status.
- `finrobot_positions.csv` for open managed positions.
- `finrobot_deals.csv` for managed deal history.
- `finrobot_acks.csv` for fills/rejections/auto decisions.
- `finrobot_commands.csv` for optional external commands.

Use `python3 scripts/mt5_trade_report.py` before making strategy changes. It summarizes open MT5 positions and closed managed deal performance.

## Auto-improvement loop

`autonomous-review` runs every 6 hours. It:

1. Reads MT5 trade report and improvement memory.
2. On service restart, waits one full `AUTOREVIEW_INTERVAL_HOURS` period before the first review unless `AUTOREVIEW_RUN_ON_START=true`.
3. Skips if fewer than `AUTOREVIEW_MIN_TRADES` closed deals are available.
4. Calls Opencode with the current mandate.
4. Runs `compileall` and `scripts/mt5_trade_report.py` after successful Opencode changes.
5. Restarts `mt5-terminal` and `moonshot-dashboard` when checks pass.

Default minimum is 12 closed deals and default cadence is every 6 hours. Keep this evidence gate unless the owner asks for more aggressive changes.

## Change rules

- Make direct changes; repo is in git.
- Before editing, inspect `git status --short` and relevant logs/reports.
- After editing, run at least `python3 -m compileall -q moonshot finrobot scripts` and `python3 scripts/mt5_trade_report.py`.
- If EA source changes, copy it to the installed MT5 Experts path and compile with MetaEditor when available.
- Update `README.md` and this file when operating behavior changes.
- Do not print secrets from `.env`.

## Quick health checks

```bash
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
tail -n 120 logs/autonomous_review.log
pm2 list
```
