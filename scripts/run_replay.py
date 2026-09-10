#!/usr/bin/env python
"""
Deterministic replay: given a capability_id and input parameters, runs
the approved artifact with no LLM in the loop and prints the typed
result contract.

    python scripts/run_replay.py member.get_savings_balance --member_id 12345 --scenario replay_success
    python scripts/run_replay.py member.get_savings_balance --member_id 99999 --scenario replay_not_found
    python scripts/run_replay.py member.get_savings_balance --member_id 40404 --scenario replay_locked
    python scripts/run_replay.py member.get_savings_balance --member_id abc   --scenario replay_invalid_input
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

from common import MOCK_BANK_URL, get_repository, start_mock_bank, stop_mock_bank

from src.models.execution import ExecutionMode
from src.observability.evidence import scenario_dir
from src.observability.logger import RunLogger
from src.policy.engine import PolicyEngine
from src.policy.redaction import redact_params
from src.registry.capability_registry import CapabilityRegistry
from src.replay.executor import ReplayExecutor
from src.surface.playwright_adapter import PlaywrightAdapter


async def main(capability_id: str, member_id: str, scenario: str, headless: bool) -> int:
    repo = get_repository()
    registry = CapabilityRegistry(repo)
    artifact = registry.get_replayable_artifact(capability_id)

    execution_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    raw_inputs = {"member_id": member_id}
    redacted = redact_params(raw_inputs, {n for n, s in artifact.inputs.items() if s.sensitive})

    repo.insert_execution(
        execution_id=execution_id, mode=ExecutionMode.REPLAY.value,
        capability_id=capability_id, capability_version_id=None, tenant_id="default",
        status="RUNNING", goal=artifact.goal_template, input_params_json=json.dumps(redacted),
        started_at=started_at.isoformat(),
    )

    evidence_dir = scenario_dir(scenario)
    logger = RunLogger(execution_id, evidence_dir, repo=repo)

    surface = PlaywrightAdapter(headless=headless)
    await surface.start()

    policy = PolicyEngine()
    executor = ReplayExecutor(surface, policy, logger, evidence_scenario=scenario)

    result = await executor.run(artifact, raw_inputs, execution_id=execution_id)

    repo.update_execution_status(
        execution_id, result.status.value, completed_at=datetime.now(timezone.utc).isoformat(),
        last_checkpoint=result.last_verified_checkpoint,
    )

    result_path = evidence_dir / "result.json"
    result_path.write_text(result.model_dump_json(indent=2))

    print(result.model_dump_json(indent=2))
    print(f"\nEvidence written to {evidence_dir}", file=sys.stderr)

    await surface.close()
    repo.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capability_id")
    parser.add_argument("--member_id", required=True)
    parser.add_argument("--scenario", default="replay_manual")
    parser.add_argument("--no-server", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    proc = None
    if not args.no_server:
        proc = start_mock_bank()
    try:
        exit_code = asyncio.run(main(args.capability_id, args.member_id, args.scenario, headless=not args.headed))
    finally:
        if proc is not None:
            stop_mock_bank(proc)
    sys.exit(exit_code)
