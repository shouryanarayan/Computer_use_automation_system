"""Input validation, output coercion, and redaction - the small pure functions
the error taxonomy in replay/executor.py depends on."""
from __future__ import annotations

import pytest

from src.models.capability import InputParam, OutputSpec, ParamType
from src.policy.redaction import redact_params, scrub_text
from src.replay.executor import InputValidationError, coerce_output, resolve_value, validate_inputs


class _FakeArtifact:
    def __init__(self, inputs):
        self.inputs = inputs


def test_validate_inputs_coerces_types():
    artifact = _FakeArtifact({"member_id": InputParam(type=ParamType.STRING, required=True)})
    result = validate_inputs(artifact, {"member_id": 12345})
    assert result["member_id"] == "12345"


def test_validate_inputs_raises_on_missing_required():
    artifact = _FakeArtifact({"member_id": InputParam(type=ParamType.STRING, required=True)})
    with pytest.raises(InputValidationError):
        validate_inputs(artifact, {})


def test_validate_inputs_allows_missing_optional():
    artifact = _FakeArtifact({"note": InputParam(type=ParamType.STRING, required=False)})
    result = validate_inputs(artifact, {})
    assert "note" not in result


def test_resolve_value_substitutes_template():
    assert resolve_value("{{member_id}}", {"member_id": "12345"}) == "12345"


def test_resolve_value_raises_on_undeclared_param():
    with pytest.raises(InputValidationError):
        resolve_value("{{unknown}}", {"member_id": "12345"})


def test_coerce_output_decimal_strips_currency_formatting():
    spec = OutputSpec(type=ParamType.DECIMAL)
    assert coerce_output("$4,250.00", spec) == 4250.00


def test_coerce_output_string_passthrough():
    spec = OutputSpec(type=ParamType.STRING)
    assert coerce_output("  John Smith  ", spec) == "John Smith"


def test_redact_params_masks_sensitive_keys_only():
    result = redact_params({"member_id": "12345", "note": "hello"}, {"member_id"})
    assert result["member_id"] == "***REDACTED***"
    assert result["note"] == "hello"


def test_scrub_text_masks_ssn_shaped_values():
    assert "***REDACTED***" in scrub_text("SSN on file: 123-45-6789")


def test_scrub_text_masks_credential_looking_pairs():
    assert "***REDACTED***" in scrub_text("password: hunter2")
