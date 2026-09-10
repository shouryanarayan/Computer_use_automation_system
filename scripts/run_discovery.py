#!/usr/bin/env python
"""
Runs the real, LLM-driven discovery loop against the mock bank app and,
on success, saves a DRAFT capability artifact.

    python scripts/run_discovery.py

By default this starts its own mock bank server; pass --no-server if
you already have one running on http://127.0.0.1:8000 (see
scripts/serve_mock_bank.py).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common import MOCK_BANK_URL, get_repository, start_mock_bank, stop_mock_bank

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from src.discovery.agent import DiscoveryAgent, DiscoveryStuckError
from src.discovery.artifact_builder import ParamHint, build_artifact
from src.discovery.llm_client import LLMClient
from src.models.capability import (
    BusinessOutcome,
    Condition,
    ConditionType,
    InputParam,
    OutputSpec,
    ParamType,
    RiskClass,
    TargetApplication,
)
from src.models.execution import ExecutionMode
from src.observability.evidence import scenario_dir, screenshot_dir
from src.observability.logger import RunLogger
from src.policy.engine import PolicyEngine
from src.registry.capability_registry import CapabilityRegistry
from src.surface.playwright_adapter import PlaywrightAdapter

CAPABILITY_ID = "member.get_savings_balance"
VERSION = "1.0"
MEMBER_ID = "12345"
GOAL = f"Look up member {MEMBER_ID} and read their current savings balance."


async def main(headless: bool) -> int:
    repo = get_repository()
    execution_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    repo.upsert_capability(capability_id=CAPABILITY_ID, name=CAPABILITY_ID, vendor_family="demo_vendor", description=GOAL)
    repo.insert_execution(
        execution_id=execution_id, mode=ExecutionMode.DISCOVERY.value,
        capability_id=CAPABILITY_ID, capability_version_id=None, tenant_id="default",
        status="RUNNING", goal=GOAL, input_params_json=json.dumps({"member_id": "***REDACTED***"}),
        started_at=started_at.isoformat(),
    )

    evidence_dir = scenario_dir("discovery")
    shots = screenshot_dir("discovery")
    logger = RunLogger(execution_id, evidence_dir, repo=repo)

    surface = PlaywrightAdapter(headless=headless)
    await surface.start()

    policy = PolicyEngine()
    llm = LLMClient()
    agent = DiscoveryAgent(surface, llm, policy, logger, screenshot_dir=shots)

    try:
        result = await agent.run(GOAL, MOCK_BANK_URL + "/")
    except (DiscoveryStuckError,) as e:
        logger.error(None, f"discovery stuck: {e}")
        repo.update_execution_status(execution_id, "FAILED", completed_at=datetime.now(timezone.utc).isoformat())
        print(f"DISCOVERY FAILED (stuck): {e}", file=sys.stderr)
        await surface.close()
        return 1

    print(f"Discovery finished. success={result.success} outputs={result.outputs} steps={len(result.steps)}")

    artifact = build_artifact(
        capability_id=CAPABILITY_ID,
        version=VERSION,
        goal_template="Get the savings balance for member {{member_id}}",
        discovery_result=result,
        param_hints={
            "member_id": ParamHint(
                value=MEMBER_ID,
                spec=InputParam(type=ParamType.STRING, required=True, sensitive=True, description="5-digit member identifier"),
            )
        },
        outputs={"savings_balance": OutputSpec(type=ParamType.DECIMAL, description="Member's current savings account balance in USD")},
        target_app=TargetApplication(application="mock_core_banking", vendor_family="demo_vendor", base_url=MOCK_BANK_URL),
        success_condition=Condition(type=ConditionType.TEXT_PRESENT, text="Savings"),
        business_outcomes=[
            BusinessOutcome(
                code="MEMBER_NOT_FOUND", description="No member record matches the given ID.",
                detect=Condition(type=ConditionType.TEXT_PRESENT, text="No member found"),
            ),
            BusinessOutcome(
                code="ACCOUNT_LOCKED", description="The member's account is locked; access denied.",
                detect=Condition(type=ConditionType.TEXT_PRESENT, text="Access Denied"),
            ),
            BusinessOutcome(
                code="INVALID_MEMBER_ID_FORMAT", description="The supplied member ID was not a valid 5-digit number.",
                detect=Condition(type=ConditionType.TEXT_PRESENT, text="Invalid Member ID"),
            ),
        ],
        risk_class=RiskClass.READ_ONLY,
        discovery_execution_id=execution_id,
    )

    registry = CapabilityRegistry(repo)
    version_id = registry.save_draft(artifact)

    artifacts_dir = PROJECT_ROOT / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    out_path = artifacts_dir / "member_savings_balance_v1.json"
    out_path.write_text(artifact.model_dump_json(indent=2))

    repo.update_execution_status(execution_id, "SUCCESS", completed_at=datetime.now(timezone.utc).isoformat())
    logger.checkpoint(None, "discovery run complete, draft artifact saved", status="OK", detail={"capability_version_id": version_id, "artifact_path": str(out_path)})

    print(f"Saved DRAFT artifact version {version_id} -> {out_path}")
    print(f"Evidence written to {evidence_dir}")

    await surface.close()
    repo.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-server", action="store_true", help="assume mock bank is already running")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
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
