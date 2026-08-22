"""Off-column idempotency marker for tag-along video platforms (issue #239).

Every platform in ``PLATFORMS_SCHEDULED`` except Threads gets its idempotency
for free: the orchestrator writes a sentinel into ``link <P>(v)`` after a LIVE
run, and ``_rows_for_platform`` skips any platform whose link is populated.
Threads is a *tag-along* — it deliberately has no ``clip TH(v)`` and no
``link TH(v)`` column on the editorial DB (issue #29) — so there is nowhere to
write that sentinel and TH re-runs on every invocation.

That costs nothing on a clean run: all four platforms succeed, WIP-Vd is
unticked, and there is never a second run. It bites on a *recovery* run, where
one leg failed and the other three are already scheduled. Re-running to fix the
failed leg re-posts to Threads, and the only way to avoid that — ``--skip-th``
— leaves WIP-Vd checked forever, because a flag-skip is deliberately not
accepted as success. Both outcomes are bad and there is no third option.

This module closes that gap with a small JSON file under ``results/videos/``
(gitignored) keyed by platform and day title. It is a *cache of what we already
did*, never a source of truth about Notion. The failure directions are
deliberately asymmetric:

* A **missing or unreadable** ledger reads as "nothing has been scheduled", so
  the platform runs — exactly today's behaviour. Losing the file costs a
  possible duplicate on a recovery run, which is where we already are.
* A **failed write** is logged loudly but never raises: the row genuinely is
  scheduled, and blocking the run over a bookkeeping miss would be worse than
  the missing entry.

Concurrency: ``record`` re-reads the file and merges before writing, and
publishes via ``os.replace`` so a reader never observes a half-written file. A
videos run spans several minutes across four browser sessions, which is long
enough for a second invocation to overlap.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("videos_schedule")

DEFAULT_LEDGER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "results" / "videos" / "tag_along_scheduled.json"
)
LEDGER_VERSION = 1


class TagAlongLedger:
    """Records that a tag-along platform already scheduled a given day's clip.

    Construct once per orchestrator run and thread it through the decision
    helpers, so the whole run sees one consistent view and the tests need no
    filesystem patching.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path: Path = Path(path) if path is not None else DEFAULT_LEDGER_PATH
        self._data: dict = self._read()

    # ---------- reads ----------

    def _read(self) -> dict:
        """Return the on-disk ledger, or an empty one if it can't be read.

        A missing file is the ordinary first-run case and is silent. Anything
        else means we lost information we used to have, so it is logged at
        error level — but it still degrades to "nothing scheduled" rather than
        aborting the run.
        """
        if not self.path.exists():
            return {"version": LEDGER_VERSION, "scheduled": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as err:
            logger.error(
                "❌ Tag-along ledger at %s is unreadable (%s) — treating every "
                "platform as unscheduled. A recovery run may re-post.",
                self.path, err,
            )
            return {"version": LEDGER_VERSION, "scheduled": {}}
        if not isinstance(raw, dict) or not isinstance(raw.get("scheduled"), dict):
            logger.error(
                "❌ Tag-along ledger at %s has an unexpected shape — treating "
                "every platform as unscheduled. A recovery run may re-post.",
                self.path,
            )
            return {"version": LEDGER_VERSION, "scheduled": {}}
        return raw

    def is_scheduled(self, platform: str, day_title: str) -> bool:
        """True iff a prior run recorded ``platform`` as LIVE for ``day_title``."""
        return day_title in self._data.get("scheduled", {}).get(platform, {})

    def recorded_at(self, platform: str, day_title: str) -> Optional[str]:
        """ISO timestamp of the recorded run, or None if there is no entry."""
        entry = self._data.get("scheduled", {}).get(platform, {}).get(day_title)
        return entry.get("recorded_at") if isinstance(entry, dict) else None

    # ---------- writes ----------

    def record(self, platform: str, day_title: str, *, detail: str = "") -> bool:
        """Record a LIVE tag-along schedule. Returns False if the write failed.

        Never raises: the post is already live by the time this is called, so a
        bookkeeping failure must not take the run down with it.
        """
        entry = {
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "detail": detail,
        }
        # Merge onto whatever is on disk now, not onto the snapshot taken at
        # construction — a concurrent run may have recorded another day since.
        merged = self._read()
        merged.setdefault("scheduled", {}).setdefault(platform, {})[day_title] = entry
        merged["version"] = LEDGER_VERSION
        try:
            self._write(merged)
        except OSError as err:
            logger.warning(
                "⚠️ %s: %s scheduled but could not record it in the tag-along "
                "ledger at %s: %s. A later re-run may schedule it again.",
                day_title, platform.upper(), self.path, err,
            )
            return False
        self._data = merged
        logger.debug("🧾 %s: recorded %s in the tag-along ledger.", day_title, platform.upper())
        return True

    def _write(self, data: dict) -> None:
        """Publish the ledger atomically so a reader never sees a partial file.

        The temp file is per-process: two runs sharing one temp name could
        interleave their writes and publish a corrupt file, which would defeat
        the point of staging it at all.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


__all__ = ["DEFAULT_LEDGER_PATH", "LEDGER_VERSION", "TagAlongLedger"]
