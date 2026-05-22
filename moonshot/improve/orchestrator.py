"""Self-improvement orchestrator loop.

Runs forever (or for --cycles N) doing:
  analyze -> propose -> backtest -> promote -> sleep
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from moonshot.improve.analyzer import build_report
from moonshot.improve.backtest import evaluate as backtest_evaluate
from moonshot.improve.llm import LLMClient
from moonshot.improve.promoter import append_journal, load_current, promote
from moonshot.improve.proposer import propose
from typing import Optional, Dict, Any

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "improver.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("improver")


def cycle(
    state_dir: Path,
    overrides_path: Path,
    journal_path: Path,
    proposals_dir: Path,
    *,
    model: str = "",
    window_hours: float = 24.0,
    min_trades: int = 8,
    dry_run: bool = False,
) -> Dict[str, Any]:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    logger.info("--- improver cycle start ---")
    report = build_report(state_dir, window_hours=window_hours)
    overall_n = (report.overall or {}).get("n", 0)
    logger.info(
        "Report: window=%.1fh n=%s win_rate=%s expectancy=%s",
        window_hours, overall_n,
        (report.overall or {}).get("win_rate"),
        (report.overall or {}).get("expectancy"),
    )
    if overall_n < min_trades:
        logger.info("Skipping: not enough trades (%s < %s)", overall_n, min_trades)
        append_journal(journal_path, {
            "ts": time.time(),
            "skipped": True,
            "reason": f"min_trades_not_met ({overall_n}<{min_trades})",
            "report": report.to_dict(),
        })
        return {"applied": False, "skipped": True, "reason": "insufficient_data"}

    current_overrides = load_current(overrides_path)
    client = LLMClient(model=model or None)
    logger.info("Asking %s for proposals…", client.model)
    proposal = propose(report, current_overrides, client=client)
    logger.info("Proposal: %d overrides, diagnosis=%s",
                len(proposal.overrides),
                (proposal.diagnosis or {}).get("summary", "")[:200])

    # Backtest
    bt = backtest_evaluate(
        trades_path=state_dir / "trades.jsonl",
        proposed_overrides=proposal.overrides,
        current_overrides=current_overrides,
        lookback_hours=int(max(24.0, window_hours)),
    )
    logger.info("Backtest: %s", json.dumps(bt))

    # Save proposal artifact regardless of promotion outcome
    ts_tag = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    artifact = proposals_dir / f"proposal_{ts_tag}.json"
    artifact.write_text(json.dumps({
        "report": report.to_dict(),
        "proposal": proposal.to_dict(),
        "backtest": bt,
        "current_overrides": current_overrides,
    }, indent=2, default=str))
    logger.info("Wrote proposal artifact %s", artifact)

    decision = promote(
        overrides_path=overrides_path,
        journal_path=journal_path,
        proposal=proposal.to_dict(),
        backtest=bt,
        dry_run=dry_run,
    )
    logger.info("Decision: %s (%s)", decision.get("applied"), decision.get("reason"))
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="Moonshot self-improver loop")
    parser.add_argument("--state-dir", default=str(ROOT / "state" / "moonshot"))
    parser.add_argument("--overrides", default=str(ROOT / "state" / "moonshot" / "runtime_overrides.json"))
    parser.add_argument("--journal", default=str(ROOT / "state" / "moonshot" / "improver_journal.jsonl"))
    parser.add_argument("--proposals", default=str(ROOT / "state" / "moonshot" / "proposals"))
    parser.add_argument("--window-hours", type=float, default=float(os.getenv("IMPROVER_WINDOW_HOURS", "24")))
    parser.add_argument("--interval-minutes", type=float, default=float(os.getenv("IMPROVER_INTERVAL_MINUTES", "60")))
    parser.add_argument("--min-trades", type=int, default=int(os.getenv("IMPROVER_MIN_TRADES", "8")))
    parser.add_argument("--cycles", type=int, default=0, help="0 = run forever")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=os.getenv("IMPROVER_MODEL", "gpt-5.5"))
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    overrides_path = Path(args.overrides)
    journal_path = Path(args.journal)
    proposals_dir = Path(args.proposals)

    logger.info("Improver starting | model=%s window=%.1fh interval=%.1fmin",
                args.model, args.window_hours, args.interval_minutes)

    n = 0
    while True:
        n += 1
        try:
            cycle(
                state_dir, overrides_path, journal_path, proposals_dir,
                model=args.model,
                window_hours=args.window_hours,
                min_trades=args.min_trades,
                dry_run=args.dry_run,
            )
        except Exception as e:
            logger.exception("Cycle failed: %s", e)
            append_journal(journal_path, {
                "ts": time.time(),
                "error": str(e),
            })
        if args.once or args.cycles and n >= args.cycles:
            break
        sleep_s = max(60.0, args.interval_minutes * 60.0)
        logger.info("Sleeping %.0fs until next cycle…", sleep_s)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
