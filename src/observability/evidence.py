"""
Evidence directory conventions and the "richer signal on failure"
capture required by the brief (3.5): a screenshot plus a redacted
accessibility/text dump, bundled together whenever a run hits an
error or exceptional state.
"""
from __future__ import annotations

from pathlib import Path

from src.policy.redaction import scrub_text
from src.surface.base import Surface

EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "evidence"


def scenario_dir(name: str) -> Path:
    d = EVIDENCE_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def screenshot_dir(name: str) -> Path:
    d = scenario_dir(name) / "screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def capture_failure_evidence(surface: Surface, scenario_name: str, tag: str) -> dict:
    shot_dir = screenshot_dir(scenario_name)
    screenshot_path = shot_dir / f"{tag}.png"
    await surface.screenshot(str(screenshot_path))

    observation = await surface.observe()
    dump_path = shot_dir / f"{tag}_state.txt"
    with open(dump_path, "w") as f:
        f.write(f"URL: {observation.url}\n\n")
        f.write("Controls (role 'name'):\n")
        f.write(scrub_text(observation.accessibility_summary))
        f.write("\n\nVisible text:\n")
        f.write(scrub_text(observation.visible_text))

    return {"screenshot": str(screenshot_path), "state_dump": str(dump_path)}
