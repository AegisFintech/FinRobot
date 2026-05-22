"""Versioned prompts for the self-improver.

Read from the .md files in this directory at runtime so the LLM can be
shown / can edit them as plain text without reaching into Python source.
"""
from pathlib import Path

PROMPT_DIR = Path(__file__).parent

def load(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")
