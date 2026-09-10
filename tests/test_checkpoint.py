"""check_condition: the shared primitive behind both success_condition verification
and business_outcome detection."""
from __future__ import annotations

from src.models.capability import Condition, ConditionType, LocatorStrategy, Target, TargetLocator
from src.replay.checkpoint import check_condition
from src.surface.base import Observation

from .fake_surface import FakeScreen, FakeSurface


def _surface_with(text: str, url: str = "http://127.0.0.1:8000/", controls=None) -> tuple[FakeSurface, Observation]:
    surface = FakeSurface({"s": FakeScreen(url=url, text=text, controls=controls or [])}, {}, start="s")
    obs = Observation(url=url, visible_text=text, accessibility_summary="", controls=controls or [])
    return surface, obs


async def test_text_present_matches():
    surface, obs = _surface_with("No member found with ID 99999.")
    condition = Condition(type=ConditionType.TEXT_PRESENT, text="No member found")
    assert await check_condition(surface, obs, condition) is True


async def test_text_present_does_not_match():
    surface, obs = _surface_with("Member Services")
    condition = Condition(type=ConditionType.TEXT_PRESENT, text="No member found")
    assert await check_condition(surface, obs, condition) is False


async def test_url_matches():
    surface, obs = _surface_with("x", url="http://127.0.0.1:8000/member/12345")
    condition = Condition(type=ConditionType.URL_MATCHES, url_pattern=r"/member/\d+")
    assert await check_condition(surface, obs, condition) is True


async def test_element_visible_true_when_control_present():
    controls = [{"role": "button", "name": "Approve"}]
    surface, obs = _surface_with("Additional Verification Required", controls=controls)
    condition = Condition(
        type=ConditionType.ELEMENT_VISIBLE,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Approve")),
    )
    assert await check_condition(surface, obs, condition) is True


async def test_element_visible_false_when_control_absent():
    surface, obs = _surface_with("Member Services", controls=[])
    condition = Condition(
        type=ConditionType.ELEMENT_VISIBLE,
        target=Target(primary=TargetLocator(strategy=LocatorStrategy.ROLE, role="button", name="Approve")),
    )
    assert await check_condition(surface, obs, condition) is False
