"""
Executor-level tests against the in-memory FakeSurface (see
tests/fake_surface.py). Covers the error taxonomy end to end:
SUCCESS (with a real recoverable-interstitial auto-dismiss along the
way), BUSINESS_OUTCOME (not found / locked / invalid format), and a
HITL escalation + resume that produces SUCCESS.
"""
from __future__ import annotations

import uuid

import pytest

from src.hitl.manager import HITLManager
from src.models.capability import (
    ActionType,
    BusinessOutcome,
    CapabilityArtifact,
    Condition,
    ConditionType,
    InputParam,
    LocatorStrategy,
    OutputSpec,
    ParamType,
    RiskClass,
    Step,
    Target,
    TargetApplication,
    TargetLocator,
)
from src.models.result import ExecutionStatus
from src.observability.logger import RunLogger
from src.policy.engine import PolicyEngine
from src.registry.repository import Repository
from src.replay.executor import ReplayExecutor
from src.surface.base import Action

from .fake_surface import FakeScreen, FakeSurface

MEMBER_STATUS = {"12345": "active", "99999": None, "40404": "locked", "77777": "step_up"}


def _search_transition(typed: dict) -> str:
    member_id = typed.get("Member ID")
    status = MEMBER_STATUS.get(member_id, "invalid")
    return {
        "active": "member_page",
        None: "not_found",
        "locked": "locked",
        "step_up": "verify",
        "invalid": "search_invalid",
    }[status]


def make_surface() -> FakeSurface:
    screens = {
        "notice": FakeScreen(url="http://127.0.0.1:8000/", text="System Notice: scheduled maintenance", controls=[{"role": "button", "name": "OK"}]),
        "search": FakeScreen(url="http://127.0.0.1:8000/", text="Member Services", controls=[{"role": "textbox", "name": "Member ID"}, {"role": "button", "name": "Search"}]),
        "search_invalid": FakeScreen(url="http://127.0.0.1:8000/", text="Invalid Member ID. Enter a 5-digit numeric ID.", controls=[{"role": "textbox", "name": "Member ID"}, {"role": "button", "name": "Search"}]),
        "member_page": FakeScreen(url="http://127.0.0.1:8000/member/x", text="Member Services\nSavings", controls=[{"css": "#savings-balance", "name": "savings", "value": "$4,250.00"}]),
        "not_found": FakeScreen(url="http://127.0.0.1:8000/", text="No member found with ID 99999."),
        "locked": FakeScreen(url="http://127.0.0.1:8000/", text="Access Denied: This account is locked."),
        "verify": FakeScreen(url="http://127.0.0.1:8000/", text="Additional Verification Required", controls=[{"role": "button", "name": "Approve"}]),
    }
    transitions = {
        ("notice", "role", "button", "OK"): "search",
        ("search", "role", "button", "Search"): _search_transition,
        ("search_invalid", "role", "button", "Search"): _search_transition,
        ("verify", "role", "button", "Approve"): "member_page",
    }
    return FakeSurface(screens, transitions, start="notice")


def make_artifact() -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id="member.get_savings_balance",
        version="1.0",
        goal_template="Get the savings balance for member {{member_id}}",
        target=TargetApplication(application="mock_core_banking", vendor_family="demo_vendor", base_url="http://127.0.0.1:8000/"),
        inputs={"member_id": InputParam(type=ParamType.STRING, required=True, sensitive=True)},
        outputs={"savings_balance": OutputSpec(type=ParamType.DECIMAL)},
        steps=[
            Step(id="s0", action=ActionType.TYPE, target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="textbox", name="Member ID")), value="{{member_id}}"),
            Step(id="s1", action=ActionType.ACTIVATE, target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Search"))),
            Step(id="s2", action=ActionType.READ, target=Target(primary=TargetLocator(strategy=LocatorStrategy.CSS, css="#savings-balance")), output_key="savings_balance"),
        ],
        business_outcomes=[
            BusinessOutcome(code="MEMBER_NOT_FOUND", description="not found", detect=Condition(type=ConditionType.TEXT_PRESENT, text="No member found")),
            BusinessOutcome(code="ACCOUNT_LOCKED", description="locked", detect=Condition(type=ConditionType.TEXT_PRESENT, text="Access Denied")),
            BusinessOutcome(code="INVALID_MEMBER_ID_FORMAT", description="invalid", detect=Condition(type=ConditionType.TEXT_PRESENT, text="Invalid Member ID")),
        ],
        success_condition=Condition(type=ConditionType.TEXT_PRESENT, text="Savings"),
        risk_class=RiskClass.READ_ONLY,
    )


@pytest.fixture
def repo(tmp_path):
    r = Repository(tmp_path / "test_registry.db")
    yield r
    r.close()


def make_logger(execution_id, tmp_path, repo):
    return RunLogger(execution_id, tmp_path / "evidence", repo=repo)


def _insert_execution(repo: Repository, execution_id: str) -> None:
    from datetime import datetime, timezone

    repo.upsert_capability(capability_id="member.get_savings_balance", name="member.get_savings_balance", vendor_family="demo_vendor")
    repo.insert_execution(
        execution_id=execution_id, mode="REPLAY", capability_id="member.get_savings_balance",
        capability_version_id=None, tenant_id="default", status="RUNNING", goal="test",
        input_params_json="{}", started_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.mark.asyncio
async def test_replay_success_dismisses_recoverable_notice(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    executor = ReplayExecutor(surface, PolicyEngine(), logger, evidence_scenario="test_success")

    result = await executor.run(artifact, {"member_id": "12345"}, execution_id=execution_id)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.outputs["savings_balance"] == 4250.00
    assert surface.state == "member_page"


@pytest.mark.asyncio
async def test_replay_member_not_found_is_business_outcome_not_error(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    executor = ReplayExecutor(surface, PolicyEngine(), logger, evidence_scenario="test_not_found")

    result = await executor.run(artifact, {"member_id": "99999"}, execution_id=execution_id)

    assert result.status == ExecutionStatus.BUSINESS_OUTCOME
    assert result.outcome_code == "MEMBER_NOT_FOUND"
    assert result.error is None


@pytest.mark.asyncio
async def test_replay_locked_account_is_business_outcome(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    executor = ReplayExecutor(surface, PolicyEngine(), logger, evidence_scenario="test_locked")

    result = await executor.run(artifact, {"member_id": "40404"}, execution_id=execution_id)

    assert result.status == ExecutionStatus.BUSINESS_OUTCOME
    assert result.outcome_code == "ACCOUNT_LOCKED"


@pytest.mark.asyncio
async def test_replay_invalid_member_id_format_is_business_outcome(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    executor = ReplayExecutor(surface, PolicyEngine(), logger, evidence_scenario="test_invalid")

    result = await executor.run(artifact, {"member_id": "00001"}, execution_id=execution_id)

    assert result.status == ExecutionStatus.BUSINESS_OUTCOME
    assert result.outcome_code == "INVALID_MEMBER_ID_FORMAT"


@pytest.mark.asyncio
async def test_replay_missing_required_input_is_hard_failure(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    executor = ReplayExecutor(surface, PolicyEngine(), logger, evidence_scenario="test_missing_input")

    result = await executor.run(artifact, {}, execution_id=execution_id)

    assert result.status == ExecutionStatus.HARD_FAILURE
    assert result.error.code.value == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_replay_unknown_state_without_hitl_is_hard_failure(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    executor = ReplayExecutor(surface, PolicyEngine(), logger, evidence_scenario="test_unknown_state")

    result = await executor.run(artifact, {"member_id": "77777"}, execution_id=execution_id)

    assert result.status == ExecutionStatus.HARD_FAILURE
    assert result.error.code.value == "TARGET_NOT_FOUND"
    assert result.error.failed_step == "s2"


@pytest.mark.asyncio
async def test_replay_escalates_to_hitl_and_resumes_to_success(tmp_path, repo):
    surface = make_surface()
    artifact = make_artifact()
    execution_id = str(uuid.uuid4())
    _insert_execution(repo, execution_id)
    logger = make_logger(execution_id, tmp_path, repo)
    hitl = HITLManager(surface, repo, logger, evidence_scenario="test_hitl")

    async def operator_approves(hitl_mgr: HITLManager, intervention) -> None:
        action = Action(
            type=ActionType.ACTIVATE,
            target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Approve")),
            target_description="button 'Approve'",
        )
        await hitl_mgr.perform_operator_action(intervention, action, step_id="s2")

    executor = ReplayExecutor(
        surface, PolicyEngine(), logger, evidence_scenario="test_hitl", hitl=hitl, operator_handler=operator_approves
    )

    result = await executor.run(artifact, {"member_id": "77777"}, execution_id=execution_id)

    assert result.status == ExecutionStatus.SUCCESS
    assert result.outputs["savings_balance"] == 4250.00

    interventions = repo.list_interventions(execution_id)
    assert len(interventions) == 1
    assert interventions[0]["resolution"] == "resumed"
    assert interventions[0]["control_state"] == "RESUME_REQUESTED"
