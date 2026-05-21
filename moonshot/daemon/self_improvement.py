"""
Self-Improvement Module for Moonshot Daemon
Tracks strategy performance, optimizes parameters, and triggers Opencode feedback
"""

import json
import time
import logging
import os
import random
import copy
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict
import threading

logger = logging.getLogger(__name__)


@dataclass
class StrategyTradeRecord:
    strategy_name: str
    coin: str
    side: str
    pnl: float
    pnl_pct: float
    exit_reason: str
    duration: float
    risk_amount: float = 0.0
    r_multiple: float = 0.0
    regime: str = "unknown"
    timestamp: float = field(default_factory=time.time)


class StrategyPerformanceTracker:
    def __init__(self, data_dir: str = "state/moonshot"):
        self.data_dir = data_dir
        self.performance_file = os.path.join(data_dir, "strategy_performance.json")
        self.trades_file = os.path.join(data_dir, "strategy_trades.jsonl")
        self.trades: Dict[str, List[StrategyTradeRecord]] = defaultdict(list)
        self.parameter_snapshots: Dict[str, Dict] = {}
        self.max_trades_per_strategy = 5000

        # Priors for Bayesian online edge estimation.
        # Beta(alpha, beta) prior on win rate; alpha=beta=1 = uniform.
        self.wr_prior_alpha = 1.0
        self.wr_prior_beta = 1.0
        # Normal prior on per-trade R-multiple. mu0=0 (zero edge), sigma0
        # gives the prior some teeth so a single big winner does not blow
        # up the posterior.
        self.r_prior_mu = 0.0
        self.r_prior_kappa = 4.0
        self.r_prior_alpha = 4.0
        self.r_prior_beta = 0.25

    def record_trade(self, strategy_name: str, coin: str, side: str,
                     pnl: float, pnl_pct: float, exit_reason: str, duration: float,
                     risk_amount: float = 0.0, r_multiple: float = 0.0,
                     regime: str = "unknown"):
        record = StrategyTradeRecord(
            strategy_name=strategy_name,
            coin=coin,
            side=side,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            duration=duration,
            risk_amount=risk_amount,
            r_multiple=r_multiple,
            regime=regime,
        )
        self.trades[strategy_name].append(record)
        if len(self.trades[strategy_name]) > self.max_trades_per_strategy:
            self.trades[strategy_name] = self.trades[strategy_name][-self.max_trades_per_strategy:]

        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.trades_file, 'a') as f:
                f.write(json.dumps(asdict(record), default=str) + '\n')
        except Exception as e:
            logger.error(f"Error writing strategy trade: {e}")

    def get_performance(self, strategy_name: str, lookback: int = 100) -> Dict[str, Any]:
        trades = self.trades.get(strategy_name, [])
        if not trades:
            return {"name": strategy_name, "trades": 0, "win_rate": 0, "avg_pnl_pct": 0, "total_pnl": 0}

        recent = trades[-lookback:]
        wins = [t for t in recent if t.pnl > 0]
        losses = [t for t in recent if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in recent)
        avg_pnl_pct = sum(t.pnl_pct for t in recent) / len(recent) if recent else 0
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
        avg_duration = sum(t.duration for t in recent) / len(recent) if recent else 0

        sl_trades = [t for t in recent if t.exit_reason == "SL"]
        tp_trades = [t for t in recent if t.exit_reason == "TP"]
        trail_trades = [t for t in recent if t.exit_reason == "TRAIL"]
        timeout_trades = [t for t in recent if t.exit_reason in ("TIMEOUT", "STALE")]

        by_coin = defaultdict(list)
        for t in recent:
            by_coin[t.coin].append(t)
        coin_performance = {}
        for coin, coin_trades in by_coin.items():
            coin_wins = [t for t in coin_trades if t.pnl > 0]
            coin_performance[coin] = {
                "trades": len(coin_trades),
                "win_rate": len(coin_wins) / len(coin_trades) * 100 if coin_trades else 0,
                "avg_pnl_pct": sum(t.pnl_pct for t in coin_trades) / len(coin_trades) if coin_trades else 0,
                "total_pnl": sum(t.pnl for t in coin_trades),
            }

        return {
            "name": strategy_name,
            "trades": len(recent),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(recent) * 100 if recent else 0,
            "avg_pnl_pct": avg_pnl_pct,
            "total_pnl": total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": abs(sum(t.pnl for t in wins) / sum(abs(t.pnl) for t in losses)) if losses and sum(abs(t.pnl) for t in losses) > 0 else float('inf') if wins else 0,
            "avg_duration": avg_duration,
            "sl_count": len(sl_trades),
            "tp_count": len(tp_trades),
            "trail_count": len(trail_trades),
            "timeout_count": len(timeout_trades),
            "coin_performance": coin_performance,
        }

    def _filter_trades(
        self,
        strategy_name: str,
        regime: Optional[str] = None,
        coin: Optional[str] = None,
        lookback: int = 100,
    ) -> List["StrategyTradeRecord"]:
        trades = self.trades.get(strategy_name, [])
        if regime is not None:
            trades = [t for t in trades if (t.regime or "unknown") == regime]
        if coin is not None:
            trades = [t for t in trades if t.coin == coin]
        if lookback > 0:
            trades = trades[-lookback:]
        return trades

    def get_edge_estimate(
        self,
        strategy_name: str,
        regime: Optional[str] = None,
        coin: Optional[str] = None,
        lookback: int = 200,
    ) -> Dict[str, float]:
        """Posterior estimate of edge for a (strategy, regime[, coin]) slice.

        Returns a dict with:
          n               : number of trades in the slice
          wr_mean         : posterior mean of win probability
          wr_sample       : one Thompson sample from the win-rate posterior
          expected_R_mean : posterior mean of per-trade R-multiple
          expected_R_sample: one Thompson sample from the R-multiple posterior
          confidence      : 0..1 confidence proxy = n / (n + 20)
        """
        trades = self._filter_trades(strategy_name, regime=regime, coin=coin, lookback=lookback)
        n = len(trades)

        wins = sum(1 for t in trades if t.pnl > 0)
        losses = n - wins
        alpha_post = self.wr_prior_alpha + wins
        beta_post = self.wr_prior_beta + losses
        wr_mean = alpha_post / (alpha_post + beta_post)
        # Thompson sample on win rate.
        try:
            wr_sample = random.betavariate(alpha_post, beta_post)
        except (ValueError, OverflowError):
            wr_sample = wr_mean

        if n > 0:
            r_obs = [t.r_multiple if t.r_multiple else (t.pnl_pct / 100.0) for t in trades]
            mean_obs = sum(r_obs) / n
            var_obs = (sum((x - mean_obs) ** 2 for x in r_obs) / max(n - 1, 1)) if n > 1 else 0.0
        else:
            mean_obs = 0.0
            var_obs = 0.0

        # Normal-Inverse-Gamma conjugate update.
        kappa_n = self.r_prior_kappa + n
        mu_n = (self.r_prior_kappa * self.r_prior_mu + n * mean_obs) / kappa_n
        alpha_n = self.r_prior_alpha + n / 2.0
        beta_n = (
            self.r_prior_beta
            + 0.5 * n * var_obs
            + 0.5 * (n * self.r_prior_kappa / max(kappa_n, 1e-9))
            * (mean_obs - self.r_prior_mu) ** 2
        )
        # Posterior predictive variance of the mean R-multiple.
        if alpha_n > 1:
            var_mu = beta_n / ((alpha_n - 1) * max(kappa_n, 1e-9))
        else:
            var_mu = beta_n / max(kappa_n, 1e-9)
        std_mu = max(var_mu, 1e-12) ** 0.5
        expected_R_sample = random.gauss(mu_n, std_mu)

        return {
            "n": float(n),
            "wr_mean": wr_mean,
            "wr_sample": wr_sample,
            "expected_R_mean": mu_n,
            "expected_R_sample": expected_R_sample,
            "expected_R_std": std_mu,
            "confidence": n / (n + 20.0),
        }

    def edge_passes_hurdle(
        self,
        strategy_name: str,
        regime: Optional[str] = None,
        coin: Optional[str] = None,
        min_trades: int = 20,
        min_expected_R: float = 0.05,
        min_win_rate: float = 0.40,
        lookback: int = 200,
    ) -> Tuple[bool, Dict[str, float]]:
        edge = self.get_edge_estimate(strategy_name, regime=regime, coin=coin, lookback=lookback)
        passes = (
            edge["n"] >= min_trades
            and edge["expected_R_mean"] >= min_expected_R
            and edge["wr_mean"] >= min_win_rate
        )
        edge["passes"] = 1.0 if passes else 0.0
        return passes, edge

    def get_all_performance(self, lookback: int = 100) -> Dict[str, Dict]:
        result = {}
        for strategy_name in self.trades:
            result[strategy_name] = self.get_performance(strategy_name, lookback)
        return result

    def get_summary_str(self) -> str:
        parts = []
        for name in self.trades:
            perf = self.get_performance(name, lookback=50)
            if perf["trades"] > 0:
                parts.append(f"{name[:8]}:{perf['trades']}t|{perf['win_rate']:.0f}wr|{perf['avg_pnl_pct']:+.3f}%")
        return " | ".join(parts) if parts else "no trades yet"

    def save(self):
        try:
            data = {}
            for strategy_name, trade_list in self.trades.items():
                data[strategy_name] = [asdict(t) for t in trade_list[-self.max_trades_per_strategy:]]
            with open(self.performance_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving strategy performance: {e}")

    def load(self):
        try:
            if os.path.exists(self.performance_file):
                with open(self.performance_file, 'r') as f:
                    data = json.load(f)
                for strategy_name, trade_list in data.items():
                    self.trades[strategy_name] = []
                    for t in trade_list:
                        try:
                            record = StrategyTradeRecord(**t)
                        except TypeError:
                            allowed = {
                                "strategy_name", "coin", "side", "pnl", "pnl_pct",
                                "exit_reason", "duration", "risk_amount",
                                "r_multiple", "regime", "timestamp",
                            }
                            clean = {k: v for k, v in t.items() if k in allowed}
                            clean.setdefault("risk_amount", 0.0)
                            clean.setdefault("r_multiple", 0.0)
                            clean.setdefault("regime", "unknown")
                            record = StrategyTradeRecord(**clean)
                        self.trades[record.strategy_name].append(record)
                logger.info(f"Loaded strategy performance: {len(self.trades)} strategies, "
                            f"{sum(len(v) for v in self.trades.values())} trades")
        except Exception as e:
            logger.error(f"Error loading strategy performance: {e}")

    def snapshot_parameters(self, strategy_name: str, params: Dict):
        self.parameter_snapshots[strategy_name] = {
            "params": copy.deepcopy(params),
            "timestamp": time.time(),
        }

    def get_worst_strategies(self, min_trades: int = 5, limit: int = 3) -> List[Tuple[str, Dict]]:
        scored = []
        for name in self.trades:
            perf = self.get_performance(name, lookback=50)
            if perf["trades"] >= min_trades:
                score = perf["win_rate"] - abs(perf["avg_pnl_pct"]) * 10
                scored.append((name, perf, score))
        scored.sort(key=lambda x: x[2])
        return [(name, perf) for name, perf, _ in scored[:limit]]

    def get_best_strategies(self, min_trades: int = 5, limit: int = 3) -> List[Tuple[str, Dict]]:
        scored = []
        for name in self.trades:
            perf = self.get_performance(name, lookback=50)
            if perf["trades"] >= min_trades:
                score = perf["win_rate"] + perf["avg_pnl_pct"] * 10
                scored.append((name, perf, score))
        scored.sort(key=lambda x: x[2], reverse=True)
        return [(name, perf) for name, perf, _ in scored[:limit]]


class ParameterOptimizer:
    PARAM_RANGES = {
        "Quick_Momentum": {
            "ema_fast": [3, 5, 8, 12],
            "ema_slow": [10, 15, 20, 25],
            "rsi_period": [7, 10, 14],
            "rsi_ob": [60, 65, 70, 75],
            "rsi_os": [25, 30, 35, 40],
        },
        "RSI_Reversion": {
            "rsi_period": [7, 10, 14, 21],
            "rsi_ob": [68, 72, 76, 80],
            "rsi_os": [20, 24, 28, 32],
        },
        "Micro_Trend": {
            "lookback": [3, 5, 7, 10],
            "momentum_threshold": [0.0003, 0.0005, 0.0008, 0.001],
        },
        "ADX_Momentum_Scalper": {
            "adx_threshold": [15, 20, 25, 30],
            "ema_fast": [5, 9, 12],
            "ema_slow": [18, 21, 26],
            "risk_reward": [1.5, 2.0, 2.5, 3.0],
        },
        "Aggressive_Scalper": {
            "ema_fast": [2, 3, 5],
            "ema_slow": [6, 8, 10],
            "rsi_period": [5, 7, 10],
            "rsi_overbought": [65, 70, 75],
            "rsi_oversold": [25, 30, 35],
        },
        "Mean_Reversion_Bandit": {
            "bb_period": [15, 20, 25],
            "bb_std_dev": [1.5, 2.0, 2.5, 3.0],
            "rsi_period": [10, 14, 21],
            "rsi_overbought": [68, 72, 76],
            "rsi_oversold": [24, 28, 32],
        },
        "SMC_OrderFlow": {
            "ob_lookback": [15, 20, 25, 30],
            "fvg_min_size_pct": [0.001, 0.002, 0.003],
        },
        "Fibonacci_Retracement": {
            "swing_lookback": [20, 25, 30, 40],
            "confluence_threshold": [0.002, 0.003, 0.004],
        },
        "MACD_Divergence": {
            "macd_fast": [8, 10, 12],
            "macd_slow": [21, 26, 30],
            "macd_signal": [7, 9, 12],
        },
        "VWAP_Mean": {
            "vwap_std_mult": [1.5, 2.0, 2.5],
            "reversion_band": [1.0, 1.5, 2.0],
        },
        "Range_Scalper": {
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "rsi_period": [10, 14, 21],
        },
    }

    def __init__(self, strategy_tracker: StrategyPerformanceTracker):
        self.tracker = strategy_tracker

    def suggest_parameters(self, strategy_name: str, current_performance: Dict) -> Optional[Dict]:
        if strategy_name not in self.PARAM_RANGES:
            return None

        param_ranges = self.PARAM_RANGES[strategy_name]
        current_params = self.tracker.parameter_snapshots.get(strategy_name, {}).get("params", {})
        suggestions = {}

        win_rate = current_performance.get("win_rate", 50)
        avg_pnl = current_performance.get("avg_pnl_pct", 0)
        sl_count = current_performance.get("sl_count", 0)
        tp_count = current_performance.get("tp_count", 0)
        total = current_performance.get("trades", 0)

        if total < 5:
            return None

        sl_ratio = sl_count / total if total > 0 else 0
        tp_ratio = tp_count / total if total > 0 else 0

        for param, values in param_ranges.items():
            current_val = current_params.get(param)
            if current_val is None:
                try:
                    idx = len(values) // 2
                    suggestions[param] = values[idx]
                except (IndexError, TypeError):
                    pass
                continue

            if win_rate < 40 and sl_ratio > 0.5:
                if "stop" in param.lower() or "threshold" in param.lower():
                    try:
                        current_idx = values.index(current_val)
                        if current_idx > 0:
                            suggestions[param] = values[current_idx - 1]
                    except ValueError:
                        pass
                elif "take_profit" in param.lower() or "reward" in param.lower():
                    try:
                        current_idx = values.index(current_val)
                        if current_idx < len(values) - 1:
                            suggestions[param] = values[current_idx + 1]
                    except ValueError:
                        pass

            elif win_rate > 60 and tp_ratio > 0.3:
                if "threshold" in param.lower() or "period" in param.lower():
                    suggestions[param] = current_val
                elif "rsi_ob" in param.lower():
                    try:
                        current_idx = values.index(current_val)
                        if current_idx < len(values) - 1:
                            suggestions[param] = values[current_idx + 1]
                    except ValueError:
                        pass
                elif "rsi_os" in param.lower():
                    try:
                        current_idx = values.index(current_val)
                        if current_idx > 0:
                            suggestions[param] = values[current_idx - 1]
                    except ValueError:
                        pass

            else:
                try:
                    current_idx = values.index(current_val)
                    direction = random.choice([-1, 0, 1])
                    new_idx = max(0, min(len(values) - 1, current_idx + direction))
                    if new_idx != current_idx:
                        suggestions[param] = values[new_idx]
                except ValueError:
                    pass

        return suggestions if suggestions else None

    def random_search(self, strategy_name: str) -> Optional[Dict]:
        if strategy_name not in self.PARAM_RANGES:
            return None
        result = {}
        for param, values in self.PARAM_RANGES[strategy_name].items():
            result[param] = random.choice(values)
        return result


class StrategyLab:
    """
    Strategy Lab - performance-based strategy rotation
    Disables persistently losing strategies, re-enables after cooldown
    Tracks which strategy types work best per coin
    """

    def __init__(self, strategy_tracker: StrategyPerformanceTracker, data_dir: str = "state/moonshot"):
        self.tracker = strategy_tracker
        self.data_dir = data_dir
        self.disabled_strategies: Dict[str, float] = {}
        self.reenable_cooldown = 7200
        self.disable_threshold_winrate = 20.0
        self.disable_threshold_pnl = -0.5
        self.min_trades_to_disable = 20
        self.excluded_strategies = {"Auto_Trend"}
        self.lab_log_file = os.path.join(data_dir, "strategy_lab.jsonl")

    def evaluate_strategies(self, strategies: List[Any]) -> List[Any]:
        now = time.time()
        active = []

        for name, disabled_at in list(self.disabled_strategies.items()):
            if now - disabled_at > self.reenable_cooldown:
                del self.disabled_strategies[name]
                logger.info(f"StrategyLab: Re-enabling {name} after cooldown")

        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            if sname in self.disabled_strategies or sname in self.excluded_strategies:
                continue
            active.append(strategy)

        return active

    def check_and_disable(self):
        all_perf = self.tracker.get_all_performance(lookback=30)

        for name, perf in all_perf.items():
            if name in self.disabled_strategies:
                continue
            if perf["trades"] < self.min_trades_to_disable:
                continue

            if perf["win_rate"] < self.disable_threshold_winrate and perf["avg_pnl_pct"] < self.disable_threshold_pnl:
                self.disabled_strategies[name] = time.time()
                logger.info(
                    f"StrategyLab: Disabling {name} - WR={perf['win_rate']:.0f}%, "
                    f"avg_pnl={perf['avg_pnl_pct']:+.3f}% (min {self.min_trades_to_disable} trades evaluated)"
                )
                self._log_action(name, "disabled", perf)

    def _log_action(self, strategy_name: str, action: str, perf: Dict):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "strategy": strategy_name,
                "action": action,
                "performance": perf,
            }
            with open(self.lab_log_file, 'a') as f:
                f.write(json.dumps(entry, default=str) + '\n')
        except Exception as e:
            logger.error(f"Error logging strategy lab action: {e}")

    def get_status(self) -> Dict:
        return {
            "disabled": {name: f"disabled {((time.time() - t) / 60):.0f}min ago"
                         for name, t in self.disabled_strategies.items()},
            "reenable_cooldown_min": self.reenable_cooldown / 60,
        }


class SelfImprover:
    def __init__(
        self,
        strategy_tracker: StrategyPerformanceTracker,
        strategies: List[Any],
        data_dir: str = "state/moonshot",
        improvement_interval: float = 3600.0,
        min_trades_for_improvement: int = 10,
    ):
        self.tracker = strategy_tracker
        self.strategies = strategies
        self.data_dir = data_dir
        self.improvement_interval = improvement_interval
        self.min_trades_for_improvement = min_trades_for_improvement
        self.optimizer = ParameterOptimizer(strategy_tracker)
        self.improvement_log_file = os.path.join(data_dir, "improvement_log.jsonl")
        self.pending_updates: Dict[str, Dict] = {}
        self.improvement_history: List[Dict] = []
        self.strategy_lab = StrategyLab(strategy_tracker, data_dir)

        for strategy in strategies:
            sname = getattr(strategy, 'name', strategy.__class__.__name__)
            params = {k: v for k, v in strategy.__dict__.items()
                      if not k.startswith('_') and not callable(v)
                      and k not in ('name', 'indicators', 'last_signal_time',
                                    'last_trade_time', 'last_breakout_time', 'cooldown_period',
                                    'min_trade_interval')}
            self.tracker.snapshot_parameters(sname, params)

    def run_improvement_cycle(self, current_stats: Dict, trading_engine: Any) -> Dict:
        result = {
            "changes_made": [],
            "should_notify_opencode": False,
            "timestamp": time.time(),
        }

        all_performance = self.tracker.get_all_performance(lookback=100)
        win_rate = current_stats.get("win_rate", 0)
        total_trades = current_stats.get("total_trades", 0)
        return_pct = current_stats.get("total_return_pct", 0)
        drawdown = current_stats.get("max_drawdown_pct", 0)

        if total_trades < self.min_trades_for_improvement:
            logger.info(f"Not enough trades for improvement ({total_trades}/{self.min_trades_for_improvement})")
            return result

        if win_rate < 50 or drawdown > 3 or return_pct < -1:
            result["should_notify_opencode"] = True

        self.strategy_lab.check_and_disable()

        worst = self.tracker.get_worst_strategies(min_trades=3, limit=2)
        for strategy_name, perf in worst:
            suggestions = self.optimizer.suggest_parameters(strategy_name, perf)
            if suggestions:
                self.pending_updates[strategy_name] = suggestions
                change_desc = f"{strategy_name}: {suggestions}"
                result["changes_made"].append(f"Parameter update: {change_desc}")

                log_entry = {
                    "timestamp": datetime.now().isoformat(),
                    "type": "parameter_update",
                    "strategy": strategy_name,
                    "old_performance": perf,
                    "new_parameters": suggestions,
                    "trigger": f"win_rate={win_rate:.0f}%, return={return_pct:+.2f}%, dd={drawdown:.1f}%",
                }
                self._log_improvement(log_entry)
                self.improvement_history.append(log_entry)

        best = self.tracker.get_best_strategies(min_trades=3, limit=2)
        for strategy_name, perf in best:
            if perf["win_rate"] > 60:
                logger.info(f"Best strategy {strategy_name}: {perf['win_rate']:.0f}% win rate, "
                            f"{perf['avg_pnl_pct']:+.3f}% avg pnl - keeping params")

        return result

    def get_strategy_updates(self) -> Dict[str, Dict]:
        updates = self.pending_updates.copy()
        self.pending_updates.clear()
        return updates

    def _log_improvement(self, entry: Dict):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.improvement_log_file, 'a') as f:
                f.write(json.dumps(entry, default=str) + '\n')
        except Exception as e:
            logger.error(f"Error logging improvement: {e}")


class OpencodeFeedback:
    RATE_LIMIT_SECONDS = 1800
    OPENCODE_PATH = "/home/openclaw/.npm-global/bin/opencode"

    def __init__(self, data_dir: str = "state/moonshot"):
        self.data_dir = data_dir
        self.feedback_file = os.path.join(data_dir, "opencode_feedback.jsonl")
        self.last_feedback_time = 0.0

    def request_improvement(self, stats: Dict, improvement_result: Dict,
                            strategy_performance: Dict) -> Optional[str]:
        if time.time() - self.last_feedback_time < self.RATE_LIMIT_SECONDS:
            remaining = self.RATE_LIMIT_SECONDS - (time.time() - self.last_feedback_time)
            logger.info(f"Opencode feedback rate-limited, {remaining:.0f}s remaining")
            return None

        report = self._build_report(stats, improvement_result, strategy_performance)

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "opencode_feedback_request",
            "report": report,
            "status": "submitted",
        }
        try:
            with open(self.feedback_file, 'a') as f:
                f.write(json.dumps(log_entry, default=str) + '\n')
        except Exception as e:
            logger.error(f"Error logging Opencode feedback: {e}")

        self.last_feedback_time = time.time()

        self._invoke_opencode(report)

        logger.info("Opencode feedback submitted and invoked")
        return report

    def _invoke_opencode(self, report: str):
        prompt = (
            "The Moonshot trading daemon is underperforming. Analyze the performance report below "
            "and suggest concrete code improvements to the strategy files in moonshot/strategies/ and "
            "moonshot/daemon/main.py. Focus on: 1) Improving win rate and profitability, "
            "2) Adding or improving strategy indicators, 3) Fixing position management. "
            "Implement the changes directly.\n\n"
            f"{report}"
        )
        try:
            import subprocess
            result = subprocess.run(
                [self.OPENCODE_PATH, "--prompt", prompt],
                capture_output=True,
                text=True,
                timeout=300,
                cwd="/home/openclaw/FinRobot",
            )
            if result.returncode == 0:
                logger.info(f"Opencode invocation succeeded, output length: {len(result.stdout)}")
            else:
                logger.warning(f"Opencode invocation returned {result.returncode}: {result.stderr[:500]}")
        except FileNotFoundError:
            logger.warning(f"Opencode binary not found at {self.OPENCODE_PATH}, writing feedback to file only")
        except subprocess.TimeoutExpired:
            logger.warning("Opencode invocation timed out after 300s")
        except Exception as e:
            logger.error(f"Error invoking opencode: {e}")

    def _build_report(self, stats: Dict, improvement_result: Dict,
                      strategy_performance: Dict) -> str:
        lines = [
            "MOONSHOT DAEMON PERFORMANCE REPORT",
            "=" * 40,
            f"Balance: ${stats.get('balance', 0):.2f}",
            f"Return: {stats.get('total_return_pct', 0):+.2f}%",
            f"Win Rate: {stats.get('win_rate', 0):.0f}%",
            f"Total Trades: {stats.get('total_trades', 0)}",
            f"Max Drawdown: {stats.get('max_drawdown_pct', 0):.1f}%",
            "",
            "STRATEGY PERFORMANCE:",
        ]
        for name, perf in strategy_performance.items():
            lines.append(
                f"  {name}: {perf.get('trades', 0)} trades, "
                f"{perf.get('win_rate', 0):.0f}% WR, "
                f"{perf.get('avg_pnl_pct', 0):+.3f}% avg"
            )

        if improvement_result.get("changes_made"):
            lines.append("")
            lines.append("AUTO-IMPROVEMENTS MADE:")
            for change in improvement_result["changes_made"]:
                lines.append(f"  - {change}")

        lines.append("")
        lines.append("RECOMMENDED ACTIONS FOR OPENCODE:")
        win_rate = stats.get('win_rate', 0)
        if win_rate < 50:
            lines.append("  - Analyze losing trade patterns and suggest strategy code changes")
            lines.append("  - Consider new indicator combinations (MACD, VWAP, Order Blocks)")
            lines.append("  - Try Smart Money Concepts: order blocks, fair value gaps, breaker blocks")
            lines.append("  - Try Fibonacci retracement confluence zones for entries")
        if stats.get('max_drawdown_pct', 0) > 3:
            lines.append("  - Improve risk management: reduce position sizes, tighten stops")
        if stats.get('total_trades', 0) < 10:
            lines.append("  - Loosen signal thresholds to generate more trading opportunities")
            lines.append("  - Add more coin pairs or reduce cooldown periods")
        if return_pct < -1:
            lines.append("  - System is losing money - consider replacing worst-performing strategies")
            lines.append("  - Try VWAP-based entries for better fill prices")
            lines.append("  - Try MACD divergence for trend reversal detection")

        return "\n".join(lines)
