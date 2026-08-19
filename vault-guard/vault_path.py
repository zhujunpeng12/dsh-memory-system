"""Resolve the memory vault root with legacy-compatible defaults.

Priority:
  1. MEMORY_VAULT env — explicit choice (vault mode or custom location).
  2. Legacy default `~/Documents/Obsidian Vault` — kept for existing users
     whose vault already lives there (smooth migration, zero setup change).
  3. `~/.dsh-memory` — base mode for new users: plain local directory,
     no Obsidian required.
"""
from __future__ import annotations

import os
from pathlib import Path


def vault_root() -> Path:
    env = os.environ.get("MEMORY_VAULT")
    if env:
        return Path(env)
    home = Path.home()
    legacy = home / "Documents" / "Obsidian Vault"
    if legacy.exists():
        return legacy
    return home / ".dsh-memory"
