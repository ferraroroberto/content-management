"""Regression test: the `.claude/skills/<name>/SKILL.md` files that also ship
a hand-maintained `.agents/skills/<name>/SKILL.md` mirror (for Codex/Pi, which
don't read `.claude/`) must stay byte-identical.

Filed as issue #242 finding: `.agents/skills/schedule-autoheal/SKILL.md` had
silently diverged from its `.claude/` counterpart (description frontmatter, a
selector-anchoring warning, and the Slack-bot rationale all differed), so a
Codex/Pi session was reading stale prose with no signal anything was wrong.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
AGENTS_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"


def _mirrored_skill_names() -> list[str]:
    """Every skill name present under both `.claude/skills/` and `.agents/skills/`."""
    if not CLAUDE_SKILLS_DIR.is_dir() or not AGENTS_SKILLS_DIR.is_dir():
        return []
    claude_names = {p.name for p in CLAUDE_SKILLS_DIR.iterdir() if p.is_dir()}
    agents_names = {p.name for p in AGENTS_SKILLS_DIR.iterdir() if p.is_dir()}
    return sorted(claude_names & agents_names)


class AgentSkillMirrorsInSyncTests(unittest.TestCase):
    def test_at_least_one_mirrored_pair_exists(self):
        # Guards against the discovery helper silently finding nothing (e.g.
        # a directory layout change) and every test below vacuously passing.
        self.assertTrue(
            _mirrored_skill_names(),
            "Expected at least one skill present under both .claude/skills/ "
            "and .agents/skills/ — check the directory layout.",
        )

    def test_mirrored_skill_md_pairs_are_byte_identical(self):
        for name in _mirrored_skill_names():
            claude_path = CLAUDE_SKILLS_DIR / name / "SKILL.md"
            agents_path = AGENTS_SKILLS_DIR / name / "SKILL.md"
            with self.subTest(skill=name):
                self.assertTrue(claude_path.is_file(), f"Missing {claude_path}")
                self.assertTrue(agents_path.is_file(), f"Missing {agents_path}")
                claude_text = claude_path.read_text(encoding="utf-8")
                agents_text = agents_path.read_text(encoding="utf-8")
                self.assertEqual(
                    claude_text, agents_text,
                    f".claude/skills/{name}/SKILL.md and .agents/skills/{name}/SKILL.md "
                    "have diverged — keep the Codex/Pi mirror byte-identical to the "
                    "Claude source (issue #242).",
                )


if __name__ == "__main__":
    unittest.main()
