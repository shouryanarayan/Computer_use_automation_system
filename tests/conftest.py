"""Keeps test runs from writing into the real evidence/ directory used by the demo scripts."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_evidence_root(tmp_path, monkeypatch):
    import src.observability.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "EVIDENCE_ROOT", tmp_path / "evidence")
