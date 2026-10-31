"""The composer's clock affordance that opens the Schedule dialog.

LinkedIn renamed the clock's icon id (``clock-medium`` -> ``clock-outline-medium``)
while the control stayed in plain view, so the exact-id selector matched nothing
and every scheduled row failed with "Could not open the Schedule dialog". The
control is now found by its accessible name first (a counter-bearing label such
as "Scheduled (2)") and by an icon-id *prefix* second; these tests pin both
anchors and the order they are tried in.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from planning.linkedin import linkedin_composer
from planning.linkedin.linkedin_labels import SCHEDULE_CLOCK_NAME_RE, SCHEDULE_CLOCK_SEL


class ClockNameTests(unittest.TestCase):

    def test_matches_counter_and_legacy_labels(self):
        for name in ("Scheduled (2)", "Scheduled (0)", "Schedule post", "schedule", "Programar publicación"):
            self.assertIsNotNone(SCHEDULE_CLOCK_NAME_RE.search(name), name)

    def test_ignores_unrelated_controls(self):
        for name in ("Post", "Media", "Celebration", "Add collaborators", "Unscheduled"):
            self.assertIsNone(SCHEDULE_CLOCK_NAME_RE.search(name), name)


class ClockIconSelectorTests(unittest.TestCase):

    def test_icon_id_is_matched_by_prefix_not_exact_id(self):
        """An exact ``svg#clock-medium`` is what silently stopped matching."""
        self.assertNotIn("svg#clock-medium", SCHEDULE_CLOCK_SEL)
        self.assertIn('svg[id^="clock"]', SCHEDULE_CLOCK_SEL)

    def test_matches_link_button_and_role_button(self):
        clauses = [c.strip() for c in SCHEDULE_CLOCK_SEL.split(",")]
        self.assertEqual(len(clauses), 3)
        for tag in ("a:has(", "button:has(", '[role="button"]:has('):
            self.assertTrue(any(c.startswith(tag) for c in clauses), tag)


class ScheduleClockOrderTests(unittest.TestCase):

    def _page(self, link_count: int, button_count: int):
        """A page whose dialog locator resolves each anchor to a counted fake."""
        link, button, icon = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        link.count.return_value = link_count
        button.count.return_value = button_count
        dialog = mock.MagicMock()
        dialog.get_by_role.side_effect = lambda role, **_: {"link": link, "button": button}[role]
        dialog.locator.return_value.first = icon
        return dialog, link, button, icon

    def _resolve(self, dialog):
        with mock.patch.object(linkedin_composer, "dialog_locator", return_value=dialog):
            return linkedin_composer.schedule_clock(mock.Mock())

    def test_name_as_link_wins(self):
        dialog, link, _, _ = self._page(1, 1)
        self.assertIs(self._resolve(dialog), link.first)

    def test_name_as_button_when_no_link(self):
        dialog, _, button, _ = self._page(0, 1)
        self.assertIs(self._resolve(dialog), button.first)

    def test_icon_prefix_is_the_fallback(self):
        dialog, _, _, icon = self._page(0, 0)
        self.assertIs(self._resolve(dialog), icon)
        dialog.locator.assert_called_once_with(SCHEDULE_CLOCK_SEL)


if __name__ == "__main__":
    unittest.main()
