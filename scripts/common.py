"""Shared helpers for the demo CLI scripts."""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

MOCK_BANK_URL = "http://127.0.0.1:8000"


def start_mock_bank() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "src.app.mock_bank.server"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(MOCK_BANK_URL, timeout=0.5)
            return proc
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise RuntimeError("mock bank server did not become ready in time")


def stop_mock_bank(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def get_repository():
    from src.registry.repository import Repository

    return Repository(PROJECT_ROOT / "db" / "registry.db")
