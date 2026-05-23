#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path('/home/openclaw/FinRobot')
COMMON_CANDIDATES = [
    Path('/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files'),
    Path('/home/openclaw/.wine-mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files'),
]


def common_dir() -> Path | None:
    for d in COMMON_CANDIDATES:
        if (d / 'finrobot_status.json').exists() or (d / 'finrobot_deals.csv').exists():
            return d
    for p in Path('/home/openclaw/.wine-mt5').glob('**/finrobot_status.json'):
        return p.parent
    return None


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors='replace'))
    except Exception:
        return {}


def read_csv(path: Path) -> list[dict]:
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open(errors='replace', newline='') as fh:
        return list(csv.DictReader(fh))


def money(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def summarize_deals(rows: list[dict]) -> dict:
    exits = [r for r in rows if str(r.get('entry')) in {'1', '3'} or money(r.get('profit')) != 0]
    by_symbol: dict[str, list[float]] = defaultdict(list)
    for r in exits:
        by_symbol[r.get('symbol') or '?'].append(money(r.get('profit')) + money(r.get('commission')) + money(r.get('swap')))
    out = {'closed_deals': len(exits), 'by_symbol': {}}
    for sym, pnls in sorted(by_symbol.items()):
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        out['by_symbol'][sym] = {
            'n': len(pnls),
            'pnl': round(sum(pnls), 2),
            'win_rate': round(len(wins) / len(pnls), 4) if pnls else 0,
            'avg_win': round(mean(wins), 2) if wins else 0,
            'avg_loss': round(mean(losses), 2) if losses else 0,
        }
    return out


def main() -> None:
    d = common_dir()
    print(f'MT5 common dir: {d or "not found"}')
    if not d:
        return
    status_path = d / 'finrobot_status.json'
    positions_path = d / 'finrobot_positions.csv'
    deals_path = d / 'finrobot_deals.csv'
    acks_path = d / 'finrobot_acks.csv'

    status = read_json(status_path)
    if status:
        age = time.time() - status_path.stat().st_mtime
        print(f'Heartbeat age: {age:.1f}s')
        print(json.dumps(status, indent=2))
    positions = read_csv(positions_path)
    print(f'Open managed positions: {len(positions)}')
    if positions:
        by_sym = defaultdict(float)
        for p in positions:
            by_sym[p.get('symbol') or '?'] += money(p.get('profit'))
        print('Open PnL by symbol:', dict(sorted((k, round(v, 2)) for k, v in by_sym.items())))
        for p in positions[-20:]:
            print('  POS', p)
    deals = read_csv(deals_path)
    print('Closed deal summary:', json.dumps(summarize_deals(deals), indent=2))
    if acks_path.exists():
        print('Recent acknowledgements:')
        for line in acks_path.read_text(errors='replace').splitlines()[-20:]:
            print(' ', line)


if __name__ == '__main__':
    main()
