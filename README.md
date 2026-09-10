# Legacy Computer-Use Automation

A record-once / replay-many system for automating back-office bank applications that have
no API: an LLM ("computer use") discovers how to accomplish a goal on a live UI, the run is
converted into a typed, reviewable **capability artifact**, and that artifact is replayed
**deterministically** (no LLM) in production - with explicit handling for business outcomes,
recoverable conditions, hard failures, and human escalation.

See [`REPORT.md`](REPORT.md) for the design write-up (architecture, artifact schema,
determinism/error handling, heterogeneity/multi-tenant, escalation, safety, cuts).

## What's here

- `src/app/mock_bank/` - a small legacy-flavored "CoreBank" servicing app (Flask, server-rendered
  HTML, table layout, no test IDs) used as the target surface.
- `src/discovery/` - the real LLM-driven observe -> decide -> act loop (Anthropic Claude), plus the
  artifact builder that converts a successful run into a reusable capability.
- `src/replay/` - the deterministic replay engine: no LLM, business-outcome / recoverable /
  hard-failure classification, checkpoint verification.
- `src/policy/` - allowlist + risk classification (safe/reversible vs. irreversible) + redaction.
- `src/hitl/` - the human-in-the-loop control-transfer state machine and escalation manager.
- `src/surface/` - the Surface abstraction (Playwright adapter is the one implementation here).
- `src/registry/` - SQLite-backed capability registry (DRAFT -> APPROVED lifecycle) and execution log.
- `artifacts/member_savings_balance_v1.json` - the artifact produced by the real discovery run.
- `evidence/` - logs + screenshots from the actual discovery run and five replay runs (success,
  three business outcomes, one hard failure) plus the HITL escalation run.
- `tests/` - fast, deterministic unit tests against an in-memory fake surface (no browser/LLM
  needed) covering the artifact schema, policy engine, checkpoint logic, and the full replay
  error taxonomy including HITL escalate-and-resume.

## Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then put your own ANTHROPIC_API_KEY in .env
```

The mock bank app needs no external services - it's a local Flask app with a JSON fixture as
its "database." The registry is a local SQLite file (`db/registry.db`), created automatically on
first run.

**Running without live services:** the unit test suite (`pytest`) needs neither a browser nor an
API key - it runs entirely against an in-memory fake surface. A live browser (headless Chromium,
installed above) and an `ANTHROPIC_API_KEY` are only required for the discovery run and for
replay (which needs a browser, but never an LLM).

## Demo path

Each script manages its own mock-bank server subprocess by default (pass `--no-server` if you
already have one running on `:8000`, e.g. via `python -m src.app.mock_bank.server`).

```bash
# 1. Real, LLM-driven discovery run against the live mock bank.
#    Produces artifacts/member_savings_balance_v1.json (status=DRAFT) and evidence/discovery/.
cd scripts
python run_discovery.py

# 2. Promote the draft to APPROVED (stands in for human review/sign-off).
python promote_artifact.py member.get_savings_balance

# 3. Deterministic replay - no LLM - against a few scenarios.
python run_replay.py member.get_savings_balance --member_id 12345 --scenario replay_success
python run_replay.py member.get_savings_balance --member_id 99999 --scenario replay_not_found
python run_replay.py member.get_savings_balance --member_id 40404 --scenario replay_locked
python run_replay.py member.get_savings_balance --member_id abc   --scenario replay_invalid_input
python run_replay.py member.get_savings_balance --member_id 77777 --scenario replay_hard_failure

# 4. Human-in-the-loop escalation & handoff: member 77777 requires a step-up verification
#    screen the capability doesn't know about. Replay escalates, a (scripted) operator takes
#    control of the SAME live session, approves, and replay resumes to completion.
python run_hitl_demo.py
```

Each run prints a structured `ExecutionResult` JSON to stdout and writes evidence (a JSONL event
log, screenshots, and `result.json`) under `evidence/<scenario>/`.

### Unit tests

```bash
pytest tests/ -v
```

34 tests, no browser or API key required, run in well under a second.

## Notes on what's mocked

- **The human operator console** is a scripted callback (`scripts/run_hitl_demo.py:approve_step_up_operator`),
  not a networked UI - it performs its one action through the exact same `Surface.act()` automation
  uses, against the same live session, logged as `actor=HUMAN`. See REPORT.md "Escalation & handoff"
  for what a real console would add.
- **Multi-tenant/desktop support** is designed for (`config/tenants.yaml`, `src/surface/target_resolver.py`,
  `src/surface/fingerprint.py`) but only one tenant and one surface (Playwright/web) are exercised.
  See REPORT.md "Heterogeneity & multi-tenant".
