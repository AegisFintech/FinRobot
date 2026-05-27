# FinRobot

FinRobot is an autonomous MT5 demo-trading system for exactly two instruments:

- `XAUUSD`
- `BTCUSD`

## Current runtime

| PM2 process | Purpose |
|---|---|
| `mt5-terminal` | Headless Wine/Xvfb MetaTrader 5 terminal logged into ICMarketsSC-Demo. |
| `autonomous-review` | Every 6 hours, reviews MT5 XAUUSD/BTCUSD results and asks Opencode to patch the repo when enough evidence exists. |
| `moonshot-dashboard` | Streamlit read-only dashboard and log viewer. |

## Trading path

```text
FinRobotBridgeEA.mq5 inside MT5
→ broker demo fills/spread/commission/slippage
→ Common Files heartbeat, positions, deals, and acks
→ Python reports/dashboard/autonomous-review
→ Opencode improvements when enough closed trades exist
```

Important files:

- `broker/mt5/FinRobotBridgeEA.mq5` — active EA source for XAUUSD/BTCUSD.
- `scripts/start_mt5.sh` — starts the MT5 terminal under Wine.
- `scripts/mt5_status.py` — heartbeat/status check.
- `scripts/mt5_trade_report.py` — open positions and closed-deal performance report.
- `scripts/autonomous_review_loop.py` — 6-hour Opencode review loop.
- `ecosystem.config.js` — canonical PM2 process list.
- `AGENTS.md` — operating notes for future agents.

## Management commands

```bash
cd /home/openclaw/FinRobot
pm2 list
pm2 restart mt5-terminal autonomous-review moonshot-dashboard --update-env
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
tail -f logs/autonomous_review.log logs/pm2_mt5.out.log logs/pm2_mt5.err.log
```

## MT5 common files

The EA writes these files under the MT5 Common Files directory:

- `finrobot_status.json` — heartbeat, account, symbol prices, and last signal per symbol.
- `finrobot_positions.csv` — open managed positions for XAUUSD/BTCUSD.
- `finrobot_deals.csv` — managed deal history exported from MT5 history.
- `finrobot_acks.csv` — command acknowledgements and auto-trade decisions.
- `finrobot_commands.csv` — optional command input consumed by the EA.

## Improvement policy

The autonomous review loop runs every 6 hours. On restart it waits one full interval before the first review unless `AUTOREVIEW_RUN_ON_START=true`. It checks closed MT5 deals first, appends decisions to `state/moonshot/improver_journal.jsonl`, updates `state/moonshot/improver_memory.json`, shows recent memory to Opencode, and lets Opencode patch code/docs directly only when enough trade evidence exists. The default minimum is 12 closed deals.

## Guardrails

- Demo-only unless the owner explicitly says otherwise.
- Trade only `XAUUSD` and `BTCUSD`.
- Keep PM2 simple; do not add systemd services.
- Never print or commit `.env` secrets.

## Money management

The MT5 EA uses daily risk-based lot sizing. At the start of each broker day it records an equity snapshot, sizes new trades from `DailyRiskPerTradePct` and the actual SL distance, halves risk after the day's closed PnL turns negative, caps lots with `MaxLotPerTrade`, and stops opening new trades if `DailyLossLimitPct` is hit. The status JSON includes a `money_management` block so `scripts/mt5_trade_report.py` can verify the current daily snapshot, risk setting, and closed PnL.

The May 2026 audit also disabled weak live signals: BTC RSI reversion is blocked after poor closed-trade expectancy, while XAUUSD keeps only the better-performing momentum setup. The EA also makes one throttled close attempt per broker day for old managed-symbol probe positions that have the FinRobot magic number but no SL/TP, so failures cannot spam acknowledgements.

## Current loss diagnosis

The live MT5 report shows the 2026-05-25 drawdown is mostly XAUUSD. BTCUSD is near-flat to slightly positive over the closed sample, but XAUUSD closed with strongly negative expectancy and several same-day stop clusters. The old entry model was chasing EMA/momentum continuation without asking whether price was in discount/premium or reacting from a useful liquidity imbalance; it also allowed up to three positions per symbol, which amplified correlated losses.

## Smart-money filters

`FinRobotBridgeEA.mq5` now defaults to `EnableSmartMoneyGates=true`, `EnableXauAutoTrading=false`, and `MaxAutoPositionsPerSymbol=1`. BTC entries must pass a lightweight SMC gate before order placement:

- Longs need discount pricing plus a 3+ point confluence score from bullish FVG, reclaimed bullish order block, liquidity sweep, structure shift, and deep discount.
- Shorts need premium pricing plus a 3+ point confluence score from bearish FVG, rejected bearish order block, liquidity sweep, structure shift, and deep premium.
- High-confluence entries (score 5+) can size up through the risk model via `HighConfluenceLotMultiplier`, still capped by `MaxLotPerTrade` and daily loss controls.
- Status messages expose `pda=`, `smc=`, and `smc_reject score=` so the dashboard/performance logs show why trades were accepted or rejected.

XAUUSD is paused by default until a fresh closed-deal sample justifies re-enabling it or replacing it with stricter gold-specific SMC logic.
