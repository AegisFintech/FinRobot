# FinRobot V14 Profitability Audit

## Executive summary

The bot was not making money for four technical reasons before strategy quality even matters:

1. **Live executor was likely broken**: `HyperliquidLiveTrading` passed raw strings into `Exchange(...)`, but the Hyperliquid SDK expects an `eth_account.LocalAccount`. Live mode could fail or behave incorrectly before orders were reliably placed.
2. **The bot traded on weak cold-start data**: it previously waited for only two fresh 1-minute candles, then many strategies needing 20–50 candles silently did nothing or used a tiny sample. V14 bootstraps recent Hyperliquid candles before trading.
3. **Risk accounting was misleading**: paper `total_trades` counted opens and closes, while PnL/win-rate only counted closes. This made metrics look different from actual trade outcomes.
4. **State could become stale**: closed positions were recorded to trade history but not removed from `StateManager.positions`, so `positions.json` could retain dead positions after closes/restarts.

The trading edge itself remains unproven. V14 makes the system safer and technically coherent; it does **not** guarantee profitability.

## Changes made

### Execution / live trading

- Fixed Hyperliquid SDK construction:
  - creates `eth_account.Account.from_key(HYPERLIQUID_PRIVATE_KEY)`
  - passes `Exchange(local_account, api_url, account_address=wallet_address)`
- Fixed `market_close(...)` call signature.
- Added live position sync on startup.
- Count live trade stats on fills/closes.
- Use fill price and fee data when available.

### Market data

- Added Hyperliquid `candleSnapshot` bootstrap for ~240 recent 1m candles per coin.
- Builds 5m candles from the 1m bootstrap.
- Warmup is now 10s when historical candles are loaded, instead of blindly waiting 120s.
- Trade WebSocket events now update candle volume.

### Risk / configuration

Added `.env` controls:

```env
MAX_LEVERAGE=3
RISK_PER_TRADE_PCT=0.005
MIN_CONFIDENCE=0.72
MAX_OPEN_POSITIONS=3
TRADE_COOLDOWN_SECONDS=15
MAX_POSITION_DURATION_SECONDS=0
DAILY_LOSS_LIMIT_PCT=1.0
MAX_DRAWDOWN_PCT=0.25
```

Safer defaults:

- Live leverage defaults to 3x.
- Risk per trade defaults to 0.5%.
- Daily loss pause defaults to 1% of starting balance.
- Position timeout is genuinely disabled by default.
- High-confidence boost is disabled in live mode.

### State / metrics

- Closed positions are now removed from `StateManager`.
- Existing `trades.jsonl` is loaded on startup.
- Paper trade stats now count completed trades rather than counting both entry and exit orders.
- Paper close PnL now uses the simulated close fill price, including spread/slippage effects.

### Test/runtime fixes

- Added missing runtime dependencies: `websocket-client`, `hyperliquid-python-sdk`, `eth-account`.
- Fixed syntax error in `finrobot/strategies/strategy_integration.py`.
- Restored compatibility imports expected by existing tests:
  - `finrobot.indicators`
  - `finrobot.backtesting`
  - `finrobot.hft`
- Fixed ADX bug in `finrobot/strategies/backtesting.py`.
- Updated Pandas compatibility (`ffill()` instead of removed `fillna(method=...)`).

## What still needs AWS-side verification

I did not have remote SSH details for the AWS Ubuntu instance, so I could not inspect its live logs or restart the service there.

On AWS, verify:

```bash
cd /path/to/FinRobot
git pull
python3 -m pip install -r requirements.txt
python3 -m compileall -q moonshot scripts finrobot tests
python3 -m pytest -q tests
systemctl --user restart moonshot-daemon.service
journalctl --user -u moonshot-daemon.service -f
```

If live trading is enabled, confirm `.env` has:

```env
TRADING_MODE=live
HYPERLIQUID_PRIVATE_KEY=...
HYPERLIQUID_WALLET_ADDRESS=...
MAX_LEVERAGE=3
RISK_PER_TRADE_PCT=0.005
DAILY_LOSS_LIMIT_PCT=1.0
```

## Opinionated next step

Run V14 in paper mode for 24–48 hours and judge only completed trades after costs. If profit factor is below 1.2 or average trade is near zero after fees, the strategy layer should be tightened further instead of increasing leverage.
