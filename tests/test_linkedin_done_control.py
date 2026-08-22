"""The document dialog's Done anchor (issue #237).

LinkedIn re-rendered Done as a role-less ``<a>`` whose label sits in nested
``<span>``s, so ``get_by_role("button", …)`` stopped matching it entirely — it
found nothing for 177 consecutive polls while the dialog sat plainly ready, and
the caller reported the PDF as still processing.

The replacement anchors on visible text. That swap reintroduces a hazard the
role anchor had been avoiding for free: the only element in the whole document
whose text is exactly "Done" is video.js's *hidden* caption-settings button, a
leftover in the feed that ``DIALOG_SEL``'s ``[role="dialog"]`` branch matches.
``get_by_role`` skipped it because it skips hidden nodes; a text anchor does
not. The ``:visible`` filter on every clause is therefore load-bearing, and
these tests exist to stop a future edit from quietly dropping it.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from planning.linkedin.linkedin_labels import DONE_CTRL_SEL, DONE_TEXTS


class DoneControlSelectorTests(unittest.TestCase):

    def _clauses(self) -> list[str]:
        return [c.strip() for c in DONE_CTRL_SEL.split(",")]

    def test_every_clause_is_visible_filtered(self):
        """Load-bearing: without :visible the hidden vjs decoy wins."""
        for clause in self._clauses():
            self.assertTrue(
                clause.endswith(":visible"),
                f"{clause!r} would match hidden elements — see module docstring",
            )

    def test_matches_anchors_not_only_buttons(self):
        """The current rendering is an <a>; a rollback to <button> must still work."""
        for word in DONE_TEXTS:
            self.assertIn(f'a:has-text("{word}"):visible', self._clauses())
            self.assertIn(f'button:has-text("{word}"):visible', self._clauses())

    def test_covers_every_locale_word(self):
        self.assertEqual(len(self._clauses()), 2 * len(DONE_TEXTS))
        self.assertIn("Done", DONE_TEXTS)

    def test_no_role_based_anchor_remains(self):
        """A role anchor is what broke; it must not creep back into the selector."""
        self.assertNotIn("role=", DONE_CTRL_SEL)
        self.assertNotIn("[role", DONE_CTRL_SEL)


if __name__ == "__main__":
    unittest.main()
