"""
Playwright-backed implementation of the Surface interface (src/surface/base.py).

Targeting strategy: TargetLocator.ROLE resolves via Playwright's
get_by_role(), which uses the browser's own accessible-name
computation (the same thing a screen reader would use) rather than
markup structure - this is what survives a legacy app's lack of test
IDs. LABEL/TEXT/CSS are fallbacks, tried in order, for controls where
role/name resolution isn't reliable. See docs/architecture.md.

observe() builds a lightweight, readable "accessibility summary" via a
single JS walk of interactive elements, rather than Playwright's
(deprecated) accessibility.snapshot(). This is intentionally the same
kind of signal a legacy app actually exposes: role inferred from tag +
type, name from aria-label / associated <label for> / value / visible
text - never from an id or test-id attribute.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

from src.models.capability import LocatorStrategy, Target, TargetLocator
from src.surface.base import Action, ActionResult, Observation, Surface, TargetNotFoundError

_CONTROL_SCAN_JS = """
() => {
  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl) return lbl.innerText.trim();
    }
    if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button')) {
      return (el.value || '').trim();
    }
    if (el.placeholder) return el.placeholder.trim();
    return (el.innerText || el.textContent || '').trim();
  }
  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'button') return 'button';
    if (tag === 'a') return 'link';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      return 'textbox';
    }
    return tag;
  }
  const controlEls = Array.from(document.querySelectorAll('button, input, a, select, textarea, [role]'));
  const controls = controlEls.map(el => ({
    role: roleOf(el),
    name: accessibleName(el),
    tag: el.tagName.toLowerCase(),
  })).filter(c => c.name || c.tag === 'input');

  // Elements with a stable id but not already covered above (e.g. a plain
  // <td> data cell in a legacy label/value table with no accessible
  // role/name of its own) - exposed separately as a deliberate CSS(#id)
  // fallback target, never used implicitly.
  const covered = new Set(controlEls);
  const idEls = Array.from(document.querySelectorAll('[id]')).filter(el => !covered.has(el));
  const id_elements = idEls.map(el => ({
    id: el.id,
    text: (el.innerText || el.textContent || '').trim().slice(0, 80),
  })).filter(e => e.text);

  return { controls, id_elements };
}
"""


class PlaywrightAdapter(Surface):
    def __init__(self, headless: bool = True, default_timeout_ms: int = 8000):
        self.headless = headless
        self.default_timeout_ms = default_timeout_ms
        self._playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.default_timeout_ms)

    async def navigate(self, url: str) -> None:
        await self.page.goto(url, wait_until="load")

    async def current_url(self) -> str:
        return self.page.url

    async def observe(self) -> Observation:
        scan = await self.page.evaluate(_CONTROL_SCAN_JS)
        controls = scan["controls"]
        id_elements = scan["id_elements"]
        visible_text = await self.page.inner_text("body")
        summary_lines = [f"{c['role']} '{c['name']}'" for c in controls if c["name"]]
        return Observation(
            url=self.page.url,
            visible_text=visible_text.strip(),
            accessibility_summary="\n".join(summary_lines),
            controls=controls,
            id_elements=id_elements,
        )

    def _build_locator(self, spec: TargetLocator) -> Locator:
        if spec.strategy == LocatorStrategy.ROLE:
            return self.page.get_by_role(spec.role, name=spec.name)
        if spec.strategy == LocatorStrategy.LABEL:
            return self.page.get_by_label(spec.label)
        if spec.strategy == LocatorStrategy.TEXT:
            return self.page.get_by_text(spec.text)
        if spec.strategy == LocatorStrategy.CSS:
            return self.page.locator(spec.css)
        raise ValueError(f"unknown locator strategy: {spec.strategy}")

    async def _resolve(self, target: Target) -> Locator:
        errors = []
        for spec in target.strategies_in_order():
            try:
                loc = self._build_locator(spec)
                count = await loc.count()
                if count >= 1:
                    return loc.first
                errors.append(f"{spec.strategy}: 0 matches")
            except Exception as e:  # noqa: BLE001 - collect and try next strategy
                errors.append(f"{spec.strategy}: {e}")
        raise TargetNotFoundError(
            f"no locator strategy resolved target '{target.primary}': {'; '.join(errors)}"
        )

    async def act(self, action: Action) -> ActionResult:
        from src.models.capability import ActionType

        if action.type == ActionType.NAVIGATE:
            await self.navigate(action.url or action.value)
            return ActionResult(ok=True, message=f"navigated to {self.page.url}")

        if action.target is None:
            return ActionResult(ok=False, message=f"action {action.type} requires a target")

        locator = await self._resolve(action.target)

        if action.type == ActionType.TYPE:
            await locator.fill(action.value or "")
            return ActionResult(ok=True, message=f"typed into {action.target_description}")

        if action.type == ActionType.ACTIVATE:
            await locator.click()
            await self.page.wait_for_load_state("load")
            return ActionResult(ok=True, message=f"activated {action.target_description}")

        if action.type == ActionType.READ:
            text = (await locator.inner_text()).strip()
            return ActionResult(ok=True, message=text)

        if action.type == ActionType.WAIT_FOR:
            await locator.wait_for(state="visible", timeout=self.default_timeout_ms)
            return ActionResult(ok=True, message=f"{action.target_description} became visible")

        return ActionResult(ok=False, message=f"unsupported action type: {action.type}")

    async def is_visible(self, target: Target) -> bool:
        try:
            locator = await self._resolve(target)
            return await locator.is_visible()
        except TargetNotFoundError:
            return False

    async def screenshot(self, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=path)
        return path

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
