# Design Report

## 1. Architecture

Two execution modes share almost everything except who's making decisions:

```
 Goal + target                                   Capability + inputs
      |                                                  |
      v                                                  v
 DISCOVERY AGENT  --propose action-->  POLICY ENGINE  <--recorded step-- REPLAY EXECUTOR
 (LLM decides)         ALLOW/BLOCK/        |                                  (no LLM)
      ^                CONFIRM             v
      |                              SURFACE (Playwright)
      +--- observe() -----------------------+--------> live app (mock CoreBank)
                                             |
                                    RunLogger --> evidence/*.jsonl + SQLite execution_event
```

**Discovery** (`src/discovery/agent.py`) runs a real observe -> decide -> act loop: the surface
scans the live page for interactive controls (role + accessible name, not CSS selectors) and any
plain data cells with a stable id but no role of their own; the LLM (Anthropic Claude, forced
tool-use so every turn returns one structured action) proposes exactly one next action; the
policy engine authorizes or blocks it; the surface executes it. Every step is logged before and
after policy evaluation. On success, `discovery/artifact_builder.py` converts the transcript into
a **capability artifact** - deliberately a separate step, not something the LLM emits: it
templates the concrete input value used during the run (`"12345"` -> `"{{member_id}}"`) and
requires an explicitly-authored output contract, so a human reviewer's judgment about "what this
capability promises" isn't just inherited from the model's own account of its run.

**Replay** (`src/replay/executor.py`) takes an approved artifact and inputs, and runs it with no
model in the loop: it validates/coerces inputs, resolves each step's target through the same
Surface interface, and applies the error taxonomy described in Section 3.

Both paths run through the *same* `Surface` interface and the *same* `PolicyEngine` - the
difference is only what produces the next action (a model call vs. a recorded step), which keeps
the safety and targeting logic from ever being reimplemented per mode.

**Storage** is SQLite (`db/schema.sql`), not Postgres: single file, zero external services, and
trivially inspectable by an evaluator (`sqlite3 db/registry.db`). The schema is still a normal
versioned design - `capability_version` is append-mostly with an `is_current` flag,
`execution_event` is a pure append-only audit log - so moving to a managed Postgres for
concurrent multi-worker replay in production is a driver change, not a schema change. Domain
objects are typed Pydantic models (`src/models/`); `src/registry/repository.py` is a thin
serialize/deserialize layer over raw SQL rather than a second, parallel ORM model hierarchy.

## 2. Artifact schema

The artifact (`src/models/capability.py`) is designed as a **contract an agent calls**, not a
recorded macro:

- `inputs` / `outputs` are typed and separately declared from the steps - an agent caller sees a
  clean function signature, not a step list it has to reverse-engineer.
- Each step's `target` is a `primary` locator plus ordered `fallbacks`, each with an explicit
  `strategy`: `role` (accessibility role + accessible name - preferred, survives markup/CSS
  changes, and is the same signal a screen reader would use), `label`, `text`, or `css` (last
  resort). The real discovery run used `role` for every interactive control and fell back to
  `css` exactly once - see below.
- `business_outcomes` are named, detectable results the caller needs, distinct from the
  `success_condition` checkpoint - both reuse one `Condition` primitive (`text_present` /
  `element_visible` / `url_matches`), so "did we reach the expected screen" and "did we reach a
  known non-success screen" are the same kind of check, not special-cased.
- `status` (`DRAFT -> VALIDATED -> APPROVED -> ACTIVE -> RETIRED`) is a lifecycle a discovered
  artifact must be promoted through (`scripts/promote_artifact.py`) before replay will use it - an
  LLM producing a working draft doesn't by itself make it safe for unattended production replay.

**A concrete gap the real run exposed:** the savings balance sits in a `<td>` with no ARIA role or
accessible label of its own (a common shape in legacy label/value tables). Rather than having the
model guess a selector, the observation exposes such elements separately under "elements with a
stable id," and the model can deliberately choose `target_css` for exactly that case
(`src/discovery/llm_client.py`, `src/discovery/observer.py`). The recorded artifact
(`artifacts/member_savings_balance_v1.json`) shows this: three `role`-based steps, one `css`
fallback step for the read - a real example of the "no clean DOM" problem the brief describes,
solved by making the fallback an explicit, visible choice rather than a silent one.

## 3. Determinism & error handling

Replay never asks a model anything. Per step: check business outcomes first (short-circuits
immediately - a legitimate answer, not a failure), attempt the action, and on a target-not-found
classify what's happening before deciding what to do:

```
                    Runtime state at a step
                            |
        +-------------------+-------------------+
        |                   |                   |
  BUSINESS OUTCOME     RECOVERABLE          UNRECOGNIZED
  (declared Condition  (known interstitial,  / HARD FAILURE
   true right now:     bounded auto-dismiss  (no declared outcome,
   not found, locked,  + retry same step)     no known recovery)
   invalid format)           |                   |
        |                    |           +-------+-------+
   return outputs=      retry (<=2x)      |               |
   {}, outcome_code   then re-classify   HITL configured?  no
                                          |               |
                                     escalate,        HARD_FAILURE
                                     verify checkpoint  + evidence
                                     after handoff,     (screenshot +
                                     resume or stay     accessibility
                                     with human          dump)
```

`src/replay/error_classifier.py` holds both the business-outcome check (from the artifact) and a
small recoverable-pattern library (currently: dismiss the app's "System Notice" interstitial) -
infrastructure-level, shared across capabilities on the same app, not part of the artifact
contract itself. After every step, `checkpoint.py`'s `check_condition` re-verifies; after the
whole run, the declared `success_condition` is checked again before outputs are returned - a click
"succeeding" is never treated as proof the goal was reached.

Concretely, replaying the one capability built here against five inputs produces five distinct,
correctly-classified results (`evidence/replay_*`): `SUCCESS` (12345, after auto-dismissing the
notice), three flavors of `BUSINESS_OUTCOME` (99999 not found, 40404 locked, `abc` invalid
format), and a live `HARD_FAILURE` (77777, target-not-found, with a screenshot and redacted text
dump) when no HITL is configured for that run - the same input escalates and *succeeds* when HITL
is (Section 5).

UI drift is out of scope for pass/fail in this prototype but not ignored:
`src/surface/fingerprint.py` computes a structural hash of the visible (role, name) pairs on every
step, logged as evidence on every discovery and replay run. A production version would store the
fingerprint captured at recording time on the artifact and alert (not necessarily fail) when a
tenant's live fingerprint diverges - see Section 7.

## 4. Heterogeneity & multi-tenant

The seam is the `Surface` abstract class (`src/surface/base.py`): discovery and replay only call
`navigate/observe/act/is_visible/screenshot`, never Playwright directly. A legacy web surface with
framesets/nested tables would implement the same interface differently inside `observe()`/`act()`
(e.g. resolving locators through a frame tree) without the discovery loop, artifact schema, or
replay executor changing at all. A desktop surface would implement it against an OS accessibility
API instead of a DOM; the `role`/`name` vocabulary in the artifact already matches what desktop
accessibility trees expose, which is why `role` was chosen as the primary locator strategy over
anything web-specific.

For multi-tenant reuse, an artifact never embeds a concrete tenant URL - `TargetApplication.base_url`
is only the discovery-time default. `src/surface/target_resolver.py` resolves
`(tenant_id, application) -> base_url` plus optional per-tenant, per-step locator overrides from
`config/tenants.yaml` at replay time, so the same artifact can run against "Bank A" and "Bank B"
running the same vendor product with a renamed control, without re-recording. Only one tenant
(`default`) is actually exercised here; the config file's commented `bank_b` entry shows the
override shape without a second implementation to validate it against - see Section 7.

## 5. Escalation & handoff

Control-transfer is an explicit state machine (`src/models/intervention.py`,
`src/hitl/control_state.py`): `AUTOMATION_CONTROL -> PAUSE_REQUESTED -> INTERVENTION_WAIT ->
HUMAN_CONTROL -> RESUME_REQUESTED -> (AUTOMATION_CONTROL | HUMAN_CONTROL)`. The last transition is
conditional: after the operator acts, the executor re-checks whether the blocking condition is
actually resolved before returning control to automation - an operator's action isn't
automatically trusted to have fixed things.

"Stuck" is detected the same way as any other unrecognized screen: no declared business outcome
matches and no known recoverable pattern matches. `HITLManager.escalate()` then captures a
screenshot + accessibility text dump, persists an `Intervention` row, and calls an injected
`operator_handler` - which acts through the **exact same `Surface.act()`** automation uses,
against the same live browser session, just logged with `actor=HUMAN` instead of `actor=AUTOMATION`.
That's the real part: one live session, a real state machine, a real audit trail
(`evidence/hitl/run.jsonl` shows `INTERVENTION -> HUMAN act -> INTERVENTION -> CHECKPOINT ->
AUTOMATION act` in sequence, and `db intervention` row records `claimed_by`, `operator_actions`,
and `resolution=resumed`).

What's mocked, deliberately: the operator is a scripted callback
(`scripts/run_hitl_demo.py:approve_step_up_operator`), not a networked console. A real console
would poll/subscribe on the `intervention` table, render the screenshot, offer a small set of
allowed actions, and call the same `perform_operator_action` this code already has - the
plumbing this stub stands in for is the one piece of Section 3.6 explicitly allowed to be
mocked, and the seam for it already exists.

## 6. Safety

- **Allowlist**: `config/policy.yaml` enumerates permitted domains and action types; anything not
  listed is denied by default (allowlist, not denylist). `PolicyEngine.evaluate()` is the single
  choke point both discovery and replay pass every action through.
- **Risk classification**: action type has a default risk (`read`/`wait_for` = READ_ONLY,
  `type`/`navigate`/`activate` = REVERSIBLE); a control whose accessible name matches a keyword
  list (submit, confirm, delete, approve, transfer, withdraw, ...) is escalated to IRREVERSIBLE
  regardless of type. IRREVERSIBLE actions never execute inline - they require confirmation, which
  (since discovery/replay run unattended) is implemented as an escalation to the HITL path rather
  than a silent block, so a genuinely necessary irreversible step still has a path to completion,
  just never an unattended one.
- **Redaction**: two layers, enforced at the one place events reach disk/DB
  (`observability/logger.py`). Declared-`sensitive` input parameters (e.g. `member_id`) are masked
  by key before any log/DB write. A secondary pattern scrub (`policy/redaction.py`) catches
  SSN/card-number/credential shapes in free text pulled from the live surface, applied even to
  what reaches the LLM - though ordinary UI content (names, balances) necessarily reaches the
  model, since reading it is the task. A production deployment would also need to reason about
  the model provider's own data-handling terms for that unavoidable exposure; that's a real gap
  this prototype doesn't solve (Section 7).
- **Human actions bypass the automated allowlist** - a supervisor is presumed authorized to
  handle exactly the case automation couldn't - but every such action is still logged and tied to
  its intervention record for audit.

## 7. Cuts

Left out, deliberately, with what's already in place for it:

- **Networked operator console** - HITL's control-transfer mechanism, evidence capture, and audit
  trail are real; the console itself is a scripted stand-in (Section 5).
- **A second tenant/second app** - `target_resolver.py` and `tenants.yaml` define the override
  shape; only one tenant is exercised, so the override path is unvalidated against a real second
  case.
- **Desktop/legacy-web surface** - the `Surface` interface is designed to extend to them (Section
  4); no second adapter is implemented.
- **Fingerprint-driven drift alerting** - fingerprints are computed and logged on every run but
  don't gate anything yet; wiring them into an actual alert/threshold is next.
- **Confidence scoring / approval gating beyond DRAFT->APPROVED**, **assisted single-step LLM
  recovery on replay failure**, and **multi-run stability scoring** - all listed as optional
  stretch goals in the brief; none attempted, in favor of depth on the required core (artifact
  schema, error taxonomy, HITL).
- **Model-provider data handling for regulated PII reaching the LLM during discovery** - noted
  above as a real, unresolved production concern, not a design decision made lightly.
