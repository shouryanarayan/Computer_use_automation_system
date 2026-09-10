#!/usr/bin/env python
"""
Demonstrates escalation & handoff (brief 3.6): replays
member.get_savings_balance for member 77777, whose record requires
supervisor step-up verification - a screen outside the capability's
recorded flow. The replay engine cannot recognize it, escalates to a
human operator, the operator acts on the SAME live session (not a
fresh one), and the replay resumes and completes. The operator here is
a scripted stand-in for a real console - see src/hitl/manager.py for
what's real vs. mocked.

    python scripts/run_hitl_demo.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

from common import get_repository, start_mock_bank, stop_mock_bank

from src.hitl.manager import HITLManager
from src.models.capability import ActionType, LocatorStrategy, Target, TargetLocator
from src.models.execution import ExecutionMode
from src.models.intervention import Intervention
from src.observability.evidence import scenario_dir
from src.observability.logger import RunLogger
from src.policy.engine import PolicyEngine
from src.policy.redaction import redact_params
from src.registry.capability_registry import CapabilityRegistry
from src.replay.executor import ReplayExecutor
from src.surface.base import Action
from src.surface.playwright_adapter import PlaywrightAdapter

MEMBER_ID = "77777"
SCENARIO = "hitl"


async def approve_step_up_operator(hitl: HITLManager, intervention: Intervention) -> None:
    """Stands in for a human operator: the one action a supervisor performs on this screen."""
    action = Action(
        type=ActionType.ACTIVATE,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Approve")),
        target_description="button 'Approve'",
    )
    await hitl.perform_operator_action(intervention, action, step_id=intervention.context.current_step_id or "unknown")


async def main(headless: bool) -> int:
    repo = get_repository()
    registry = CapabilityRegistry(repo)
    artifact = registry.get_replayable_artifact("member.get_savings_balance")

    execution_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    sensitive = {n for n, s in artifact.inputs.items() if s.sensitive}
    redacted = redact_params({"member_id": MEMBER_ID}, sensitive)

    repo.insert_execution(
        execution_id=execution_id, mode=ExecutionMode.REPLAY.value, capability_id=artifact.capability_id,
        capability_version_id=None, tenant_id="default", status="RUNNING", goal=artifact.goal_template,
        input_params_json=json.dumps(redacted), started_at=started_at.isoformat(),
    )

    evidence_dir = scenario_dir(SCENARIO)
    logger = RunLogger(execution_id, evidence_dir, repo=repo)

    surface = PlaywrightAdapter(headless=headless)
    await surface.start()
    policy = PolicyEngine()
    hitl = HITLManager(surface, repo, logger, evidence_scenario=SCENARIO)

    executor = ReplayExecutor(
        surface, policy, logger, evidence_scenario=SCENARIO, hitl=hitl, operator_handler=approve_step_up_operator
    )
    result = await executor.run(artifact, {"member_id": MEMBER_ID}, execution_id=execution_id)

    repo.update_execution_status(execution_id, result.status.value, completed_at=datetime.now(timezone.utc).isoformat())
    result_path = evidence_dir / "result.json"
    result_path.write_text(result.model_dump_json(indent=2))
    print(result.model_dump_json(indent=2))

    interventions = repo.list_interventions(execution_id)
    print(f"\n{len(interventions)} intervention(s) recorded for this run.", file=sys.stderr)
    print(f"Evidence written to {evidence_dir}", file=sys.stderr)

    await surface.close()
    repo.close()
    return 0 if result.status.value == "SUCCESS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-server", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    proc = None
    if not args.no_server:
        proc = start_mock_bank()
    try:
        exit_code = asyncio.run(main(headless=not args.headed))
    finally:
        if proc is not None:
            stop_mock_bank(proc)
    sys.exit(exit_code)
