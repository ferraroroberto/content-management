"""``open_day_schedule_menu`` opens the target day's own Schedule ▾ menu.

In Meta's planner week view the day-header row and the column bodies are
separate subtrees, so walking up from a header only meets a Schedule button at
the planner root, where today's always-visible slot button wins. A live probe
showed every target day (Fri 13, Sat 14) resolving to today's button. Rows
scheduled only because the composer rewrites the date afterwards, and a
Saturday row failed when today's slot button was not on screen. Each other
column shows its own bottom Schedule ▾ only while hovered, centred under its
header cell.

The fixture is anonymised and structure-only: a header row of seven day cells,
seven column bodies, today's mid-column slot button, and per-column bottom
buttons that only display under ``:hover``. A button's click opens a
``role="menu"`` whose items record which day's menu was used. The tests drive
the real ``open_day_schedule_menu`` against it in headless Chrome.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.instagram.test_instagram_day_schedule_menu -v
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from tools.planning.instagram import instagram_composer as composer

FIXTURE = """
<style>
  body { margin: 0; font: 14px sans-serif; }
  #planner { position: relative; }
  .row { display: flex; width: 910px; }
  .hdr, .col { width: 130px; box-sizing: border-box; }
  .hdr { height: 30px; text-align: center; }
  .col { height: 600px; position: relative; border-left: 1px solid #ccc; }
  .col .bottom { display: none; position: absolute; bottom: 8px; left: 10px; width: 110px; }
  .col:hover .bottom { display: block; }
  .slot { position: absolute; top: 300px; left: 10px; width: 100px; }
  [role="menu"] { position: absolute; background: #fff; border: 1px solid #000; }
</style>
<div id="planner">
  <div class="row" id="headers"></div>
  <div class="row" id="bodies"></div>
</div>
<script>
  const DAYS = ["Sun 8", "Mon 9", "Tue 10", "Wed 11", "Thu 12", "Fri 13", "Sat 14"];
  const TODAY = "Thu 12";
  window.picked = null;
  function scheduleButton(day, cls) {
    const b = document.createElement("div");
    b.setAttribute("role", "button");
    b.setAttribute("aria-haspopup", "menu");
    b.className = cls;
    b.textContent = "Schedule";
    b.addEventListener("click", () => {
      document.querySelectorAll('[role="menu"]').forEach(m => m.remove());
      const menu = document.createElement("div");
      menu.setAttribute("role", "menu");
      const r = b.getBoundingClientRect();
      menu.style.left = r.left + "px";
      menu.style.top = Math.max(0, r.top - 80) + "px";
      for (const item of ["Schedule post", "Schedule story"]) {
        const mi = document.createElement("div");
        mi.setAttribute("role", "menuitem");
        mi.textContent = item;
        mi.addEventListener("click", () => { window.picked = day + "|" + item; menu.remove(); });
        menu.appendChild(mi);
      }
      document.body.appendChild(menu);
    });
    return b;
  }
  const headers = document.getElementById("headers");
  const bodies = document.getElementById("bodies");
  for (const day of DAYS) {
    const h = document.createElement("div");
    h.className = "hdr";
    const span = document.createElement("span");
    span.textContent = day;
    h.appendChild(span);
    headers.appendChild(h);
    const col = document.createElement("div");
    col.className = "col";
    col.appendChild(scheduleButton(day, "bottom"));
    bodies.appendChild(col);
  }
  // Today's slot button lives in an overlay layer rendered after the columns
  // and positioned over today's column, so a DOM-order pick from the planner
  // root lands on it whatever the target day.
  const overlay = document.createElement("div");
  overlay.style.position = "absolute";
  overlay.style.top = "30px";
  overlay.style.left = (DAYS.indexOf(TODAY) * 130) + "px";
  overlay.style.width = "130px";
  overlay.appendChild(scheduleButton(TODAY, "slot"));
  document.getElementById("planner").appendChild(overlay);
</script>
"""


class OpenDayScheduleMenuTest(unittest.TestCase):
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
        self.page = browser.new_page(viewport={"width": 1280, "height": 900})
        # Keep a wrong pick failing fast: the real per-attempt timeouts are 8-32 s.
        self.enterContext(
            mock.patch.object(composer, "_MENU_ITEM_CLICK_TIMEOUTS_MS", (1000,))
        )
        self.page.set_content(FIXTURE)

    def _open(self, d: date, action: str) -> str:
        composer.open_day_schedule_menu(self.page, d, action)
        return self.page.evaluate("window.picked")

    def test_last_column_of_the_week_uses_its_own_menu(self) -> None:
        self.assertEqual(
            self._open(date(2026, 11, 14), "Schedule story"), "Sat 14|Schedule story"
        )

    def test_a_middle_column_uses_its_own_menu(self) -> None:
        self.assertEqual(
            self._open(date(2026, 11, 13), "Schedule post"), "Fri 13|Schedule post"
        )

    def test_today_uses_its_own_column(self) -> None:
        self.assertEqual(
            self._open(date(2026, 11, 12), "Schedule post"), "Thu 12|Schedule post"
        )


if __name__ == "__main__":
    unittest.main()
