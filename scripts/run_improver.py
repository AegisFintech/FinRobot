#!/usr/bin/env python3
"""Entry point for the self-improvement loop."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from moonshot.improve.orchestrator import main

if __name__ == "__main__":
    main()
