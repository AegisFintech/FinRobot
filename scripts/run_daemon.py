#!/usr/bin/env python3
"""Legacy Hyperliquid daemon launcher.

Disabled by default: FinRobot now trades BTCUSD and XAUUSD through the MT5 EA.
Use `FINROBOT_ENABLE_LEGACY_HYPERLIQUID=1` only for historical debugging.
"""
import os
import sys

if os.getenv('FINROBOT_ENABLE_LEGACY_HYPERLIQUID') != '1':
    print('Legacy Hyperliquid daemon disabled. MT5 XAUUSD/BTCUSD is the active trading path.')
    sys.exit(0)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from moonshot.daemon.main import main

if __name__ == "__main__":
    main()
