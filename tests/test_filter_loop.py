"""Tests for scripts/filter_loop.py."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

import scripts.filter_loop as filter_loop

def test_get_top_markets_dynamic_env_reload(monkeypatch, tmp_path):
    # Setup a mock ROOT path for the script so it looks for .env in tmp_path
    monkeypatch.setattr(filter_loop, "ROOT", tmp_path)
    env_file = tmp_path / ".env"
    
    # 1. No .env, no process env -> defaults to 2
    monkeypatch.setattr(filter_loop, "_ORIGINAL_ENV_TOP", None)
    assert filter_loop._get_top_markets() == 2
    
    # 2. .env set to 5 -> returns 5
    env_file.write_text("SH_TOP_MARKETS=5", encoding="utf-8")
    assert filter_loop._get_top_markets() == 5
    
    # 3. .env changed to 10 -> returns 10 (dynamic reload works)
    env_file.write_text("SH_TOP_MARKETS=10", encoding="utf-8")
    assert filter_loop._get_top_markets() == 10
    
    # 4. Original process env was set -> overrides .env
    monkeypatch.setattr(filter_loop, "_ORIGINAL_ENV_TOP", "8")
    assert filter_loop._get_top_markets() == 8

def test_get_top_markets_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(filter_loop, "ROOT", tmp_path)
    # If dotenv fails, falls back to original env
    monkeypatch.setattr(filter_loop, "_ORIGINAL_ENV_TOP", "15")
    
    # Simulate dotenv error
    with patch("dotenv.dotenv_values", side_effect=Exception("mocked error")):
        assert filter_loop._get_top_markets() == 15
