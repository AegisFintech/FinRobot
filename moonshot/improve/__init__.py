"""Self-improvement loop for the Moonshot daemon.

Reads recent trade history, asks an LLM to diagnose and propose
parameter changes, validates the proposals with a shadow backtest
over recent Hyperliquid candles, and writes whitelisted overrides to
`state/moonshot/runtime_overrides.json` for the live daemon to pick
up on its next iteration.
"""
