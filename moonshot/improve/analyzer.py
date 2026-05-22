"""Performance analyzer.

Loads trades.jsonl + strategy_trades.jsonl + state.json and produces a
compact JSON-serialisable report grouped by strategy / regime / coin
that can be embedded in an LLM prompt.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _safe_load_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _bucket_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"n": 0}
    pnls = [t.get("pnl", 0.0) for t in trades]
    rs = [t.get("r_multiple", 0.0) for t in trades if "r_multiple" in t]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    win_rate = len(wins) / len(pnls)
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    durations = [t.get("exit_time", 0) - t.get("entry_time", 0) for t in trades]
    avg_dur_min = (sum(durations) / len(durations) / 60.0) if durations else 0.0
    reasons: Dict[str, int] = defaultdict(int)
    for t in trades:
        reasons[t.get("exit_reason", "?")] += 1
    out: Dict[str, Any] = {
        "n": len(trades),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "expectancy": round(expectancy, 6),
        "profit_factor": round(pf, 3) if pf is not None else None,
        "total_pnl": round(sum(pnls), 6),
        "avg_duration_min": round(avg_dur_min, 1),
        "exit_reasons": dict(reasons),
    }
    if rs:
        out["avg_r_multiple"] = round(statistics.mean(rs), 4)
        if len(rs) > 1:
            out["r_multiple_std"] = round(statistics.pstdev(rs), 4)
    return out


@dataclass
class PerformanceReport:
    generated_at: float
    overall: Dict[str, Any]
    by_strategy: Dict[str, Dict[str, Any]]
    by_coin: Dict[str, Dict[str, Any]]
    by_regime: Dict[str, Dict[str, Any]]
    by_strategy_regime: Dict[str, Dict[str, Dict[str, Any]]]
    recent_window_hours: float
    open_positions: List[Dict[str, Any]] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "recent_window_hours": self.recent_window_hours,
            "overall": self.overall,
            "by_strategy": self.by_strategy,
            "by_coin": self.by_coin,
            "by_regime": self.by_regime,
            "by_strategy_regime": self.by_strategy_regime,
            "open_positions": self.open_positions,
            "state": self.state,
            "notes": self.notes,
        }


def build_report(
    state_dir: Path,
    window_hours: float = 24.0,
    max_trades: int = 500,
) -> PerformanceReport:
    state_dir = Path(state_dir)
    trades_path = state_dir / "trades.jsonl"
    strat_trades_path = state_dir / "strategy_trades.jsonl"
    state_path = state_dir / "state.json"
    pos_path = state_dir / "positions.json"

    trades = _safe_load_jsonl(trades_path)
    strat_trades = _safe_load_jsonl(strat_trades_path)
    state = _safe_load_json(state_path) or {}
    positions = _safe_load_json(pos_path) or {}

    # Merge regime info from strat_trades into trades by best-effort match
    # on exit_time / strategy.  strat_trades was added in V14+.
    strat_index: Dict[float, Dict[str, Any]] = {}
    for s in strat_trades:
        ts = s.get("timestamp")
        if ts is not None:
            strat_index[round(float(ts), 1)] = s

    for t in trades:
        et = t.get("exit_time")
        if et is None:
            continue
        match = strat_index.get(round(float(et), 1))
        if match:
            for k in ("regime", "r_multiple", "risk_amount"):
                if k in match:
                    t.setdefault(k, match[k])

    cutoff = time.time() - window_hours * 3600.0
    recent = [t for t in trades if (t.get("exit_time") or 0) >= cutoff][-max_trades:]

    by_strategy: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_coin: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_regime: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_strategy_regime: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for t in recent:
        s = t.get("strategy", "unknown")
        c = (t.get("symbol", "")).replace("-PERP", "") or "?"
        r = t.get("regime", "unknown")
        by_strategy[s].append(t)
        by_coin[c].append(t)
        by_regime[r].append(t)
        by_strategy_regime[s][r].append(t)

    open_pos_list = []
    for sym, p in (positions or {}).items():
        open_pos_list.append({
            "symbol": sym,
            "side": p.get("side"),
            "entry_price": p.get("entry_price"),
            "size": p.get("size"),
            "leverage": p.get("leverage"),
            "stop_loss": p.get("stop_loss"),
            "take_profit": p.get("take_profit"),
            "age_min": round((time.time() - (p.get("open_time") or time.time())) / 60.0, 1),
        })

    return PerformanceReport(
        generated_at=time.time(),
        overall=_bucket_stats(recent),
        by_strategy={k: _bucket_stats(v) for k, v in sorted(by_strategy.items(), key=lambda kv: -len(kv[1]))},
        by_coin={k: _bucket_stats(v) for k, v in sorted(by_coin.items())},
        by_regime={k: _bucket_stats(v) for k, v in sorted(by_regime.items())},
        by_strategy_regime={
            s: {r: _bucket_stats(v) for r, v in sorted(rmap.items())}
            for s, rmap in sorted(by_strategy_regime.items())
        },
        recent_window_hours=window_hours,
        open_positions=open_pos_list,
        state={
            "balance": state.get("balance"),
            "equity": state.get("equity"),
            "free_margin": state.get("free_margin"),
            "daily_stats": state.get("daily_stats"),
            "timestamp": state.get("timestamp"),
        },
        notes=[],
    )


__all__ = ["PerformanceReport", "build_report"]
