"""
Resolves (tenant_id, application) -> a concrete base_url and any
per-tenant locator overrides, per config/tenants.yaml.

This is the seam that keeps a capability artifact portable across
tenants running the same vendor product (REPORT.md "Heterogeneity &
multi-tenant"): the artifact's TargetApplication.base_url is only the
discovery-time default. At replay time, when a tenant_id is supplied,
the resolver's base_url takes precedence, and per-step locator
overrides (if a tenant's variant renamed/moved a control) are applied
on top of the artifact's own primary/fallback locators.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

DEFAULT_TENANTS_PATH = Path(__file__).resolve().parents[2] / "config" / "tenants.yaml"


class TenantResolutionError(Exception):
    pass


class TargetResolver:
    def __init__(self, tenants_path: Path = DEFAULT_TENANTS_PATH):
        with open(tenants_path) as f:
            config = yaml.safe_load(f) or {}
        self.tenants: dict = config.get("tenants", {})

    def resolve_base_url(self, tenant_id: str, application: str) -> str:
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            raise TenantResolutionError(f"unknown tenant '{tenant_id}'")
        app = tenant.get("apps", {}).get(application)
        if app is None:
            raise TenantResolutionError(f"tenant '{tenant_id}' has no configured app '{application}'")
        return app["base_url"]

    def get_locator_override(
        self, tenant_id: str, application: str, capability_id: str, step_id: str
    ) -> Optional[dict]:
        app = self.tenants.get(tenant_id, {}).get("apps", {}).get(application, {})
        overrides = app.get("locator_overrides", {})
        return overrides.get(capability_id, {}).get(step_id)
