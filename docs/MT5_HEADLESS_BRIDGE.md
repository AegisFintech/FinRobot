# MT5 Headless Bridge

Status: installed and running under PM2 as `mt5-terminal`.

Active broker/demo symbols:

- `XAUUSD`
- `BTCUSD`

MT5 is the active execution and performance source.

## Paths

- Server/account: ICMarketsSC-Demo demo account, loaded from `.env`.
- Wine prefix: `/home/openclaw/.wine-mt5`
- Terminal symlink: `/home/openclaw/mt5/terminal/current`
- Start script: `scripts/start_mt5.sh`
- Status helper: `scripts/mt5_status.py`
- Trade report: `scripts/mt5_trade_report.py`
- Bridge EA source: `broker/mt5/FinRobotBridgeEA.mq5`
- Installed EA: `/home/openclaw/.wine-mt5/drive_c/ICMarketsSCOfficialMT5/MQL5/Experts/FinRobot/FinRobotBridgeEA.mq5`

## Common Files protocol

The EA writes to MT5 Common Files:

- `finrobot_status.json` — heartbeat, account, status per managed symbol.
- `finrobot_positions.csv` — open managed positions.
- `finrobot_deals.csv` — exported managed deal history.
- `finrobot_acks.csv` — command acknowledgements and auto-trade decisions.
- `finrobot_commands.csv` — optional external commands.

## Validation commands

```bash
cd /home/openclaw/FinRobot
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
pm2 list
```

The heartbeat should be fresh, `trade_allowed_terminal` and `trade_allowed_ea` should be `1`, and status should include both `XAUUSD` and `BTCUSD` under `symbols`.
