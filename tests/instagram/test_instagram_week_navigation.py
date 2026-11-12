"""``navigate_to_week`` waits for Meta's planner week grid before searching it.

The planner shell (header, Week/Month toggle) paints before the week grid,
which can sit behind a spinner for several seconds. Searching during that gap
finds neither the day column nor the week buttons, so the navigator must wait
for the grid rather than give up at once. Nothing here opens a browser: the
page is a fake whose grid mounts after a set amount of waited time.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from datetime import date

from planning.instagram.instagram_composer import navigate_to_week

_TARGET = date(2026, 11, 13)


class _FakeButton:
    def __init__(self, page: "_FakePlannerPage") -> None:
        self._page = page

    def count(self) -> int:
        return 1 if self._page.mounted else 0

    @property
    def first(self) -> "_FakeButton":
        return self

    def click(self, timeout: int = 0) -> None:
        self._page.clicks += 1


class _FakePlannerPage:
    """Grid (target column and week buttons) appears once ``mount_after_ms`` has been waited."""

    def __init__(self, mount_after_ms: float) -> None:
        self.mount_after_ms = mount_after_ms
        self.waited_ms = 0
        self.clicks = 0

    @property
    def mounted(self) -> bool:
        return self.waited_ms >= self.mount_after_ms

    def evaluate(self, _js: str, _label: str):
        return object() if self.mounted else None

    def get_by_role(self, _role: str, name=None) -> _FakeButton:
        return _FakeButton(self)

    def wait_for_timeout(self, ms: int) -> None:
        self.waited_ms += ms


class NavigateToWeekWaitsForGridTest(unittest.TestCase):
    def test_slow_grid_is_waited_for_not_given_up_on(self) -> None:
        page = _FakePlannerPage(mount_after_ms=6000)
        navigate_to_week(page, _TARGET)
        self.assertTrue(page.mounted, "navigator returned before the grid mounted")
        self.assertEqual(page.clicks, 0, "target week was already showing; no click needed")

    def test_grid_that_never_mounts_raises_a_distinct_error(self) -> None:
        page = _FakePlannerPage(mount_after_ms=float("inf"))
        with self.assertRaisesRegex(RuntimeError, "week grid"):
            navigate_to_week(page, _TARGET)


if __name__ == "__main__":
    unittest.main()
