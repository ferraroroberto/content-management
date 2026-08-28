r"""Regression tests for the Instagram composer's transcode guard (issue #243).

``wait_action_button_enabled`` shipped with the driver but was never wired to a
call site — an unwired guard for a race the live path still ran into. Meta keeps
the composer's ``Schedule`` button ``aria-disabled="true"`` while the uploaded
media is still processing server-side; clicking straight through it does
nothing, and the failure only surfaced 30 s later as a misleading "composer did
not close" timeout from ``_wait_composer_closes``.

``test_schedule_paths_wait_before_clicking`` is the regression proof: it scans
the module's AST and fails against the pre-fix source, where the two
``_click_action_button(page, "Schedule")`` calls had no guard in front of them.

The virtual-clock fakes below mirror ``test_planning_waits_and_retry.py``: the
clock only advances inside the fakes' own waits, so a full 90 s timeout costs no
real time.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from planning.instagram.schedule_instagram_posts import wait_action_button_enabled

MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "planning" / "instagram" / "schedule_instagram_posts.py"
)

GUARD = "wait_action_button_enabled"
CLICK = "_click_action_button"


class _FakePage:
    """Minimal Playwright ``Page`` with a virtual clock in milliseconds."""

    def __init__(self, locator: "_FakeButton") -> None:
        self.now = 0.0
        self._locator = locator

    def evaluate(self, _expr: str) -> float:
        return self.now

    def wait_for_timeout(self, ms: float) -> None:
        self.now += ms

    def get_by_role(self, _role: str, name=None) -> "_FakeButton":
        return self._locator


class _FakeButton:
    """Button that stops being ``aria-disabled`` at ``enables_at`` on the clock.

    ``enables_at=None`` means "never enables" — the stuck-upload case.
    ``raises=True`` models a locator that cannot resolve at all.
    """

    def __init__(self, enables_at: float | None, *, raises: bool = False) -> None:
        self.enables_at = enables_at
        self.raises = raises
        self.page: _FakePage | None = None
        self.polls = 0

    @property
    def last(self) -> "_FakeButton":
        return self

    def get_attribute(self, _name: str) -> str | None:
        self.polls += 1
        if self.raises:
            raise RuntimeError("locator did not resolve")
        assert self.page is not None
        if self.enables_at is not None and self.page.now >= self.enables_at:
            return "false"
        return "true"


def _wire(button: _FakeButton) -> _FakePage:
    page = _FakePage(button)
    button.page = page
    return page


class WaitActionButtonEnabledTests(unittest.TestCase):
    def test_returns_immediately_when_already_enabled(self) -> None:
        button = _FakeButton(enables_at=0)
        page = _wire(button)
        wait_action_button_enabled(page, "Schedule")
        self.assertEqual(page.now, 0.0, "an enabled button must cost no wait")

    def test_waits_out_a_transcoding_button(self) -> None:
        # Disabled for the first 20 s — well past any fixed sleep the driver
        # would have used, and the exact case that used to click through.
        button = _FakeButton(enables_at=20_000)
        page = _wire(button)
        wait_action_button_enabled(page, "Schedule", poll_ms=500)
        self.assertGreaterEqual(page.now, 20_000)
        self.assertGreater(button.polls, 1, "must actually poll, not probe once")

    def test_raises_a_named_error_when_it_never_enables(self) -> None:
        button = _FakeButton(enables_at=None)
        page = _wire(button)
        with self.assertRaises(RuntimeError) as ctx:
            wait_action_button_enabled(page, "Schedule", timeout_ms=5_000)
        msg = str(ctx.exception)
        # Distinct from _wait_composer_closes' "composer did not close".
        self.assertIn("Schedule", msg)
        self.assertIn("aria-disabled", msg)

    def test_unresolvable_locator_degrades_to_clicking(self) -> None:
        # If Meta relabels the button the guard must not become a hard blocker:
        # it falls through so the click behaves exactly as it did pre-guard.
        # Against a real Playwright locator the raise costs one default action
        # timeout first; the fake raises at once, so this pins the fall-through
        # semantics (no raise, no polling loop), not the latency.
        button = _FakeButton(enables_at=None, raises=True)
        page = _wire(button)
        wait_action_button_enabled(page, "Schedule")
        self.assertEqual(button.polls, 1, "must not keep polling a dead locator")


class ScheduleCallSiteTests(unittest.TestCase):
    """Every live ``Schedule`` click must be guarded — the wiring regression."""

    @staticmethod
    def _call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name):
                return func.id
        return None

    def test_schedule_paths_wait_before_clicking(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        guarded = 0
        for parent in ast.walk(tree):
            body = getattr(parent, "body", None)
            if not isinstance(body, list):
                continue
            for i, node in enumerate(body):
                if self._call_name(node) != CLICK:
                    continue
                args = node.value.args
                if not (len(args) >= 2 and getattr(args[1], "value", None) == "Schedule"):
                    continue  # Cancel / Close clicks need no upload guard
                prev = body[i - 1] if i else None
                self.assertIsNotNone(
                    prev,
                    f"{CLICK}(page, 'Schedule') at line {node.lineno} has no preceding "
                    f"{GUARD} call",
                )
                self.assertEqual(
                    self._call_name(prev), GUARD,
                    f"{CLICK}(page, 'Schedule') at line {node.lineno} must be immediately "
                    f"preceded by {GUARD}(page, 'Schedule') — Meta keeps the button "
                    f"aria-disabled while media is still processing",
                )
                guarded += 1
        self.assertEqual(
            guarded, 2,
            "expected the story and post drivers' two Schedule clicks to be guarded",
        )


if __name__ == "__main__":
    unittest.main()
