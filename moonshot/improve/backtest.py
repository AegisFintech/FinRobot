"""Shadow backtest: replay recent Hyperliquid candles with proposed knobs.

This is intentionally lightweight — we don't re-run the full daemon
strategy engine; we just simulate the effect of the most impactful exit
knobs (chandelier trail, partial-TP, ATR SL/TP) against the actual
trade entry timestamps recorded in `trades.jsonl`, then compare summary
metrics against the live baseline.

A proposal is considered "safe to deploy" if either:
  * it touches only knobs we don't simulate here (in which case we let
    the live shadow evaluator collect data), or
  * the simulated expectancy is no worse than the current baseline by
    more than a small tolerance (default 0.01 USDT / trade).
"""

from __future__ import annotations

import json
import logging
import statistics
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
SIMULATED_KEYS = {
    "chandelier_atr_mult",
    "partial_tp_rr",
    "partial_tp_pct",
    "atr_sl_mult",
    "atr_tp_mult",
    "atr_trail_mult",
}


def _fetch_candles(coin: str, interval: str = "1m", lookback_hours: int = 48) -> List[Dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - lookback_hours * 3600 * 1000
    body = json.dumps({
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": now_ms},
    }).encode("utf-8")
    req = urllib.request.Request(
        HYPERLIQUID_INFO_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for c in data:
            out.append({
                "open_time": int(c["t"]) // 1000,
                "open": float(c["o"]),
                "high": float(c["h"]),
                "low":  float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c.get("v", 0.0)),
            })
        return out
    except Exception as e:
        logger.warning("Candle fetch failed for %s: %s", coin, e)
        return []


def _atr(candles: List[Dict[str, Any]], idx: int, period: int = 14) -> float:
    if idx < period:
        return 0.0
    trs: List[float] = []
    for i in range(idx - period + 1, idx + 1):
        prev_close = candles[i - 1]["close"] if i > 0 else candles[i]["close"]
        high = candles[i]["high"]
        low = candles[i]["low"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


@dataclass
class SimulatedTrade:
    pnl: float
    duration_s: int
    reason: str


def _simulate_trade(
    candles: List[Dict[str, Any]],
    entry_idx: int,
    side: str,
    entry_price: float,
    size: float,
    knobs: Dict[str, Any],
) -> Optional[SimulatedTrade]:
    atr = _atr(candles, entry_idx, period=14)
    if atr <= 0:
        return None
    sl_mult = float(knobs.get("atr_sl_mult", 7.0))
    tp_mult = float(knobs.get("atr_tp_mult", 9.0))
    trail_mult = float(knobs.get("chandelier_atr_mult", knobs.get("atr_trail_mult", 2.5)))
    partial_rr = float(knobs.get("partial_tp_rr", 1.0))
    partial_pct = float(knobs.get("partial_tp_pct", 0.5))

    sl_dist = atr * sl_mult
    tp_dist = atr * tp_mult
    risk = sl_dist
    partial_tp_dist = risk * partial_rr
    is_long = side == "long"
    sign = 1 if is_long else -1

    initial_sl = entry_price - sign * sl_dist
    initial_tp = entry_price + sign * tp_dist
    partial_tp_price = entry_price + sign * partial_tp_dist

    extreme = entry_price
    chand_trail = initial_sl
    partial_done = False
    remaining_size = size
    realized = 0.0
    breakeven_armed = False

    cap = min(len(candles), entry_idx + 240)  # max 4h sim
    for i in range(entry_idx + 1, cap):
        c = candles[i]
        atr_i = _atr(candles, i, period=14) or atr
        high = c["high"]; low = c["low"]; close = c["close"]
        # Update extreme + chandelier
        if is_long:
            extreme = max(extreme, high)
            chand_trail = max(chand_trail, extreme - atr_i * trail_mult)
        else:
            extreme = min(extreme, low)
            chand_trail = min(chand_trail, extreme + atr_i * trail_mult)

        # Partial TP fill?
        if not partial_done and ((is_long and high >= partial_tp_price) or (not is_long and low <= partial_tp_price)):
            partial_pnl = (partial_tp_price - entry_price) * sign * (size * partial_pct)
            realized += partial_pnl
            remaining_size = size - size * partial_pct
            partial_done = True
            breakeven_armed = True
            chand_trail = max(chand_trail, entry_price) if is_long else min(chand_trail, entry_price)

        # SL hit?
        sl_now = chand_trail if breakeven_armed or chand_trail != initial_sl else initial_sl
        if (is_long and low <= sl_now) or (not is_long and high >= sl_now):
            exit_pnl = (sl_now - entry_price) * sign * remaining_size
            return SimulatedTrade(pnl=realized + exit_pnl, duration_s=(i - entry_idx) * 60, reason="SL/TRAIL")

        # TP hit (full)?
        if (is_long and high >= initial_tp) or (not is_long and low <= initial_tp):
            exit_pnl = (initial_tp - entry_price) * sign * remaining_size
            return SimulatedTrade(pnl=realized + exit_pnl, duration_s=(i - entry_idx) * 60, reason="TP")

    # Time-out exit at last close
    last = candles[cap - 1]["close"]
    exit_pnl = (last - entry_price) * sign * remaining_size
    return SimulatedTrade(pnl=realized + exit_pnl, duration_s=(cap - 1 - entry_idx) * 60, reason="EOD")


@dataclass
class BacktestResult:
    n: int
    expectancy: float
    avg_win: float
    avg_loss: float
    win_rate: float
    total_pnl: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n": self.n, "expectancy": round(self.expectancy, 6),
            "avg_win": round(self.avg_win, 6), "avg_loss": round(self.avg_loss, 6),
            "win_rate": round(self.win_rate, 4), "total_pnl": round(self.total_pnl, 6),
        }


def _summarise(trades: List[SimulatedTrade]) -> BacktestResult:
    if not trades:
        return BacktestResult(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls)
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return BacktestResult(
        n=len(trades),
        expectancy=expectancy,
        avg_win=avg_win,
        avg_loss=avg_loss,
        win_rate=win_rate,
        total_pnl=sum(pnls),
    )


def evaluate(
    trades_path: Path,
    proposed_overrides: List[Dict[str, Any]],
    current_overrides: Dict[str, Any],
    *,
    lookback_hours: int = 48,
    max_trades: int = 60,
) -> Dict[str, Any]:
    """Replay recent trades under (a) current and (b) proposed knobs."""
    affects_sim = any(o["key"] in SIMULATED_KEYS for o in proposed_overrides)
    cutoff = time.time() - lookback_hours * 3600.0
    trades: List[Dict[str, Any]] = []
    if trades_path.exists():
        for line in trades_path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            if t.get("entry_time", 0) >= cutoff:
                trades.append(t)
    trades = trades[-max_trades:]
    if not trades or not affects_sim:
        return {
            "ran": False,
            "reason": "no_simulated_keys" if not affects_sim else "no_trades",
            "n": len(trades),
        }

    coins = sorted({(t.get("symbol", "")).replace("-PERP", "") for t in trades if t.get("symbol")})
    candles_by_coin = {c: _fetch_candles(c, "1m", lookback_hours=lookback_hours + 4) for c in coins}

    current_knobs = {k: v for k, v in (current_overrides or {}).items()}
    proposed_knobs = dict(current_knobs)
    for o in proposed_overrides:
        proposed_knobs[o["key"]] = o["value"]

    sim_current: List[SimulatedTrade] = []
    sim_proposed: List[SimulatedTrade] = []

    for t in trades:
        coin = t.get("symbol", "").replace("-PERP", "")
        side = t.get("side", "long")
        entry_ts = int(t.get("entry_time", 0))
        entry_px = float(t.get("entry_price", 0))
        size = float(t.get("size", 0))
        if not coin or entry_ts <= 0 or entry_px <= 0 or size <= 0:
            continue
        candles = candles_by_coin.get(coin) or []
        if not candles:
            continue
        # locate entry candle index by minute alignment
        minute = (entry_ts // 60) * 60
        idx = None
        for i, c in enumerate(candles):
            if c["open_time"] == minute:
                idx = i; break
        if idx is None:
            # fallback: nearest minute by binary scan
            for i, c in enumerate(candles):
                if c["open_time"] >= minute:
                    idx = i; break
        if idx is None or idx < 15 or idx >= len(candles) - 2:
            continue
        cur = _simulate_trade(candles, idx, side, entry_px, size, current_knobs)
        new = _simulate_trade(candles, idx, side, entry_px, size, proposed_knobs)
        if cur: sim_current.append(cur)
        if new: sim_proposed.append(new)

    base = _summarise(sim_current)
    cand = _summarise(sim_proposed)
    return {
        "ran": True,
        "n_trades": min(base.n, cand.n),
        "current": base.to_dict(),
        "proposed": cand.to_dict(),
        "delta_expectancy": round(cand.expectancy - base.expectancy, 6),
    }


__all__ = ["evaluate"]
