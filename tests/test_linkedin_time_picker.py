"""The Schedule dialog's time menu can open on a stale slot list (issue #390).

On 2026-10-10 an evening ``--live`` run failed one post twice with "No
time-picker slot matched any locale candidate ('6:30 AM', …)" while posts for
other days at the same 6:30 AM scheduled fine in the same run. The live probe
showed the menu is not virtualized and its labels use plain ASCII spaces: once
settled it holds all 96 fifteen-minute slots. But its slot list trails the
date field. Opened right after a date change, it can still render the previous
date's list, and for today that is only the slots after now (7:45 PM … 11:45
PM in an evening run, no morning at all). ``_set_schedule_time`` looked the
slot up once, with no wait, so it sometimes read that stale list and gave up.

The fixture below is anonymised and structure-only: the probed markup (a
``role="menu"`` holding ``role="menuitemradio"`` options whose ``<span>`` is
the label, and an input carrying ``data-time-picker-value`` in minutes since
midnight), no account data, no post content, hashed class names dropped. The
tests drive the real ``_set_schedule_time`` against it in headless Chrome.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_linkedin_time_picker -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from planning.linkedin import schedule_linkedin_posts as sched
from planning.linkedin.linkedin_labels import TIME_INPUT_SEL, TIME_MENU_SEL

EVENING_START = 19 * 60 + 45  # the stale "today" list starts at 7:45 PM
DEFAULT_VALUE = EVENING_START

# ``mode`` picks how the fake menu misbehaves:
#   lag     first render is the stale evening list, full list 150 ms later
#   reopen  first opening stays stale for good, the second is full
#   stuck   every opening stays on the stale evening list
#   wrong   full list, but a click lands on the slot after the one clicked
FIXTURE = """
<div role="dialog">
  <input data-testid="date-picker-input" value="1/15/2030">
  <input data-testid="time-picker-input" value="7:45 PM" data-time-picker-value="%(default)d">
</div>
<script>
  const MODE = "%(mode)s";
  const EVENING = %(evening)d;
  let opens = 0;
  const label = m => {
    const h = Math.floor(m / 60), mm = String(m %% 60).padStart(2, "0");
    return `${h %% 12 || 12}:${mm} ${h < 12 ? "AM" : "PM"}`;
  };
  const input = document.querySelector('[data-testid="time-picker-input"]');
  function fill(menu, from) {
    menu.replaceChildren();
    for (let m = from; m < 24 * 60; m += 15) {
      const opt = document.createElement("div");
      opt.setAttribute("role", "menuitemradio");
      opt.setAttribute("data-testid", "time-picker-option");
      opt.id = `r-option-${m}`;
      const span = document.createElement("span");
      span.textContent = label(m);
      opt.appendChild(span);
      opt.addEventListener("click", () => {
        const picked = MODE === "wrong" ? m + 15 : m;
        input.value = label(picked);
        input.setAttribute("data-time-picker-value", String(picked));
        menu.remove();
      });
      menu.appendChild(opt);
    }
  }
  input.addEventListener("click", () => {
    if (document.querySelector('[data-testid="time-picker-menu"]')) return;
    opens += 1;
    const menu = document.createElement("div");
    menu.setAttribute("role", "menu");
    menu.setAttribute("data-testid", "time-picker-menu");
    menu.style.cssText = "max-height: 400px; overflow-y: auto";
    document.body.appendChild(menu);
    const stale = MODE === "stuck" || MODE === "lag" || (MODE === "reopen" && opens === 1);
    fill(menu, stale ? EVENING : 0);
    if (MODE === "lag") setTimeout(() => fill(menu, 0), 150);
  });
  document.addEventListener("mousedown", e => {
    const menu = document.querySelector('[data-testid="time-picker-menu"]');
    if (menu && !menu.contains(e.target) && e.target !== input) menu.remove();
  });
</script>
"""


class SetScheduleTimeTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            self.skipTest("playwright not installed")
        pw = self.enterContext(sync_playwright())
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001 - no Chrome installed is a skip, not a failure
            self.skipTest(f"Chrome is not available: {exc}")
        self.addCleanup(browser.close)
        self.page = browser.new_page()
        # Keep the failure paths quick: the real waits are seconds.
        self.enterContext(mock.patch.object(sched, "TIME_SLOT_WAIT_MS", 800, create=True))
        self.enterContext(mock.patch.object(sched, "TIME_SELECTED_WAIT_MS", 600, create=True))

    def _load(self, mode: str) -> None:
        self.page.set_content(FIXTURE % {
            "mode": mode, "evening": EVENING_START, "default": DEFAULT_VALUE,
        })

    def _picker_value(self) -> str:
        return self.page.locator(TIME_INPUT_SEL).get_attribute("data-time-picker-value")

    def test_morning_slot_found_when_menu_opens_on_stale_evening_list(self) -> None:
        """The production failure: the one-shot lookup read the stale list."""
        self._load("lag")
        sched._set_schedule_time(self.page, 6, 30)
        self.assertEqual(self._picker_value(), "390")
        self.assertEqual(self.page.locator(TIME_INPUT_SEL).input_value(), "6:30 AM")

    def test_menu_reopened_when_first_list_never_refreshes(self) -> None:
        self._load("reopen")
        sched._set_schedule_time(self.page, 6, 30)
        self.assertEqual(self._picker_value(), "390")

    def test_evening_slot_still_found_directly(self) -> None:
        self._load("stuck")
        sched._set_schedule_time(self.page, 21, 0)
        self.assertEqual(self._picker_value(), str(21 * 60))

    def test_missing_slot_reports_candidates_and_visible_labels(self) -> None:
        self._load("stuck")
        with self.assertRaises(RuntimeError) as ctx:
            sched._set_schedule_time(self.page, 6, 30)
        message = str(ctx.exception)
        self.assertIn("'6:30 AM'", message)
        self.assertIn("17 slot(s): 7:45 PM, 8:00 PM", message)
        self.assertIn("11:45 PM", message)
        self.assertEqual(self._picker_value(), str(DEFAULT_VALUE), "nothing was selected")

    def test_wrong_slot_is_an_error_not_a_silent_success(self) -> None:
        self._load("wrong")
        with self.assertRaises(RuntimeError) as ctx:
            sched._set_schedule_time(self.page, 6, 30)
        self.assertIn("did not take 06:30", str(ctx.exception))
        self.assertIn("'405'", str(ctx.exception))

    def test_menu_selector_matches_fixture(self) -> None:
        """Guards the fixture itself against drifting from the live selectors."""
        self._load("stuck")
        self.page.locator(TIME_INPUT_SEL).click()
        self.assertEqual(self.page.locator(TIME_MENU_SEL).count(), 1)


class SummaryHasTimeTest(unittest.TestCase):
    """The "Posting at …" line re-renders a beat after the click; it must name
    the wanted time, not just the wanted day, before the row moves on."""

    def test_twelve_hour_forms(self) -> None:
        self.assertTrue(sched._summary_has_time("Posting at Thu, Jan 15, 6:30 AM", 6, 30))
        self.assertTrue(sched._summary_has_time("Publicar el jue, 15 ene, 6:30 a. m.", 6, 30))
        self.assertTrue(sched._summary_has_time("Posting at Thu, Jan 15, 9:00 PM", 21, 0))

    def test_twenty_four_hour_forms(self) -> None:
        self.assertTrue(sched._summary_has_time("Publicar el jue, 15 ene, 06:30", 6, 30))
        self.assertTrue(sched._summary_has_time("Publicar el jue, 15 ene, 18:30", 18, 30))

    def test_stale_default_time_is_rejected(self) -> None:
        self.assertFalse(sched._summary_has_time("Posting at Thu, Jan 15, 8:00 PM", 6, 30))

    def test_neighbouring_times_are_rejected(self) -> None:
        self.assertFalse(sched._summary_has_time("Posting at Thu, Jan 15, 6:30 PM", 6, 30))
        self.assertFalse(sched._summary_has_time("Posting at Thu, Jan 15, 6:30 AM", 18, 30))
        self.assertFalse(sched._summary_has_time("Publicar el jue, 15 ene, 16:30", 6, 30))
        self.assertFalse(sched._summary_has_time("Posting at Thu, Jan 15, 6:45 AM", 6, 30))


class DescribeTimeLabelsTest(unittest.TestCase):
    def test_short_list_is_listed_in_full(self) -> None:
        self.assertEqual(sched._describe_time_labels(["8:00 PM", "8:15 PM"]),
                         "2 slot(s): 8:00 PM, 8:15 PM")

    def test_long_list_keeps_both_ends(self) -> None:
        labels = [f"s{i}" for i in range(96)]
        text = sched._describe_time_labels(labels)
        self.assertTrue(text.startswith("96 slot(s): s0, s1"))
        self.assertTrue(text.endswith("s94, s95"))
        self.assertIn(" … ", text)

    def test_empty_menu(self) -> None:
        self.assertEqual(sched._describe_time_labels([]), "no slots")


if __name__ == "__main__":
    unittest.main()
