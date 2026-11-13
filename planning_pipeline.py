#!/usr/bin/env python
"""Planning pipeline: clone IG → TW/TH/SB, then schedule every Notion WIP row
across LinkedIn, Instagram, Twitter, and Threads.

Step 0 (clone): ``planning.instagram.clone_to_other_platforms`` mirrors the
IG editorial plan onto Threads / Twitter / Substack rows (captions +
illustrations) and ticks the per-platform WIP checkbox so the downstream
schedulers can pick them up. A clone failure does NOT block the platform
schedulers — captured in the summary.

The four platform schedulers each pick up their own WIP-* rows (no date
filter — supports multi-week planning runs), schedule through the respective
platform's native scheduler, and untick the WIP checkbox on success. A
per-platform failure does NOT stop the next platform — every failure is
captured in the final markdown summary written to
``results/planning/YYYY-MM-DD-HHMMSS-summary.md``.

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written, already-illustrated content
into the respective platforms' native schedulers.

CLI:
    python planning_pipeline.py [--dry-run | --live] [--debug] [--force]
        [--skip-clone]
        [--skip-linkedin] [--skip-instagram] [--skip-twitter] [--skip-threads]
        [--skip-videos]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

sys.path.append(str(Path(__file__).parent))
# Console can be cp1252 on Windows; force UTF-8 so emoji in summaries don't blow up.
from config.console import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
from config.logger_config import setup_logger  # noqa: E402
from tools.planning import run_state  # noqa: E402
from tools.planning._failure import classify as classify_failure  # noqa: E402
from tools.planning._failure import extract_screenshot
from tools.planning.instagram.clone_to_other_platforms import main as run_clone  # noqa: E402
from tools.planning.instagram.schedule_instagram_posts import main as run_instagram  # noqa: E402
from tools.planning.linkedin.schedule_linkedin_posts import main as run_linkedin  # noqa: E402
from tools.planning.row_outcome import RowStatus, has_scheduled, is_failure, parse  # noqa: E402
from tools.planning.threads.schedule_threads_posts import main as run_threads  # noqa: E402
from tools.planning.twitter.schedule_twitter_posts import main as run_twitter  # noqa: E402
from tools.planning.videos.schedule_videos_posts import main as run_videos  # noqa: E402

logger: Optional[logging.Logger] = None


@dataclass
class PlatformResult:
    name: str
    skipped: bool = False
    exit_code: int = 0
    rows: list[dict] = field(default_factory=list)
    duration_s: float = 0.0
    error: Optional[str] = None

    @property
    def status_counts(self) -> dict[str, int]:
        """Rows per outcome; ``OTHER`` holds a status that names none of them."""
        counts = {status.value: 0 for status in RowStatus}
        counts["OTHER"] = 0
        for r in self.rows:
            parsed = parse(r["status"])
            counts[parsed.value if parsed else "OTHER"] += 1
        return counts


def configure_logger(debug: bool) -> logging.Logger:
    global logger
    level = logging.DEBUG if debug else logging.INFO
    logger = setup_logger("planning_pipeline", file_logging=True, level=level)
    return logger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=f"Planning pipeline: schedule every WIP row across {' → '.join(run_state.STAGES)}."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                     help="Walk each platform's flow up to the final Schedule action; do NOT submit.")
    mode.add_argument("--live", action="store_true",
                     help="Actually schedule posts on every platform stage.")
    p.add_argument("--debug", action="store_true",
                  help="Enable debug logging (passes through to each scheduler).")
    p.add_argument("--force", action="store_true",
                  help="Passes through: schedule even if link <P> is already populated. "
                       "Also passed to clone (overwrite populated TW/TH/SB targets).")
    p.add_argument("--skip-clone",     action="store_true",
                  help="Skip the IG → TW/TH/SB clone step.")
    p.add_argument("--skip-linkedin",  action="store_true")
    p.add_argument("--skip-instagram", action="store_true")
    p.add_argument("--skip-twitter",   action="store_true")
    p.add_argument("--skip-threads",   action="store_true")
    p.add_argument("--skip-videos",    action="store_true")
    return p.parse_args()


def _build_scheduler_args(args: argparse.Namespace) -> list[str]:
    out: list[str] = ["--all-wip"]
    if args.live:
        out.append("--live")
    elif args.dry_run:
        out.append("--dry-run")
    if args.debug:
        out.append("--debug")
    if args.force:
        out.append("--force")
    return out


def _build_clone_args(args: argparse.Namespace) -> list[str]:
    # Clone uses --week-start (defaults to next Monday), which matches what the
    # platform schedulers operate on. No --all-wip equivalent — week-default is right.
    out: list[str] = []
    if args.live:
        out.append("--live")
    elif args.dry_run:
        out.append("--dry-run")
    if args.debug:
        out.append("--debug")
    if args.force:
        out.append("--force")
    return out


def _clone_step() -> tuple[int, list[dict]]:
    """Adapt clone's exit-code-only ``main()`` to the schedulers' ``(rc, rows)`` shape."""
    return run_clone(), []


def _run_clone(args: argparse.Namespace) -> PlatformResult:
    """Run the IG → TW/TH/SB clone as step 0. Failures do NOT block schedulers."""
    result = _run_platform("Clone", _clone_step, _build_clone_args(args), args.skip_clone)
    # Clone has no rows, so its exit code is the only failure signal the
    # summary can show — surface it as the platform-level error.
    if result.exit_code and not result.error:
        result.error = f"exit code {result.exit_code}"
    return result


def _run_platform(
    name: str,
    main_fn: Callable[[], tuple[int, list[dict]]],
    args_list: list[str],
    skip: bool,
) -> PlatformResult:
    assert logger is not None
    if skip:
        logger.info("⏭️  Skipping %s (per --skip-* flag).", name)
        return PlatformResult(name=name, skipped=True)

    logger.info("━━━━━━━━━━ %s ━━━━━━━━━━", name)
    orig_argv = sys.argv.copy()
    sys.argv = [orig_argv[0]] + args_list
    start = time.monotonic()
    try:
        exit_code, rows = main_fn()
    except SystemExit as exc:
        # main() should NOT call sys.exit() — it returns. Be defensive anyway.
        code = exc.code if isinstance(exc.code, int) else 99
        duration = time.monotonic() - start
        logger.error("❌ %s exited via SystemExit(%s) — treating as platform error.", name, code)
        return PlatformResult(name=name, exit_code=code, rows=[], duration_s=duration,
                              error=f"SystemExit({code})")
    except Exception as exc:  # noqa: BLE001 — orchestrator MUST swallow
        duration = time.monotonic() - start
        logger.exception(
            "❌ %s raised an unhandled exception — continuing to next platform.", name
        )
        return PlatformResult(name=name, exit_code=99, rows=[], duration_s=duration,
                              error=str(exc))
    finally:
        sys.argv = orig_argv

    duration = time.monotonic() - start
    if exit_code == 0:
        logger.info("✅ %s finished in %s with %d row(s).",
                    name, _fmt_duration(duration), len(rows))
    else:
        logger.error("❌ %s finished with exit code %s in %s with %d row(s).",
                     name, exit_code, _fmt_duration(duration), len(rows))
    return PlatformResult(name=name, exit_code=exit_code, rows=rows, duration_s=duration)


def _fmt_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _platform_failed(r: PlatformResult) -> bool:
    """Whether a single platform result counts as a failure.

    A skipped platform never fails. Otherwise a failure is a platform-level
    error, a non-zero exit code (each scheduler's own verdict, e.g. Videos'
    ``PARTIAL`` row aggregation), or any row ``is_failure`` calls a failure.
    Computed once so the exit code, the JSON
    verdict, and the markdown verdict never drift from each other.
    """
    if r.skipped:
        return False
    return bool(
        r.error
        or r.exit_code != 0
        or any(is_failure(row["status"]) for row in r.rows)
    )


def _any_platform_failed(results: list[PlatformResult]) -> bool:
    return any(_platform_failed(r) for r in results)


def _build_summary_md(args: argparse.Namespace,
                      results: list[PlatformResult],
                      started_at: datetime,
                      finished_at: datetime) -> str:
    total_duration = (finished_at - started_at).total_seconds()
    lines: list[str] = [
        f"# Planning summary — {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- Mode: **{_result_mode(args)}**",
        f"- Total duration: {_fmt_duration(total_duration)}",
        "",
        "| Platform  | LIVE | DRY  | SKIP | PART | FAIL | LOGIN | OTHER | Duration |",
        "|-----------|-----:|-----:|-----:|-----:|-----:|------:|------:|---------:|",
    ]
    for r in results:
        if r.skipped:
            lines.append(f"| {r.name:<9} |   -  |   -  |   -  |   -  |   -  |   -   |   -   | skipped  |")
            continue
        c = r.status_counts
        lines.append(
            f"| {r.name:<9} | {c[RowStatus.LIVE]:>4} | {c[RowStatus.DRY]:>4} | {c[RowStatus.SKIP]:>4} | "
            f"{c[RowStatus.PARTIAL]:>4} | {c[RowStatus.FAIL]:>4} | {c[RowStatus.LOGIN_REQUIRED]:>5} | "
            f"{c['OTHER']:>5} | {_fmt_duration(r.duration_s):>8} |"
        )
    lines.append("")

    for r in results:
        if r.skipped:
            continue
        if not r.rows and not r.error:
            lines.append(f"## {r.name} detail")
            lines.append("")
            lines.append(f"_No in-scope WIP rows found — idempotent no-op._")
            lines.append("")
            continue
        lines.append(f"## {r.name} detail")
        lines.append("")
        if r.rows:
            lines.append("| Day      | Status         | Detail |")
            lines.append("|----------|----------------|--------|")
            for row in r.rows:
                detail = (row.get("detail") or "").replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {row['day']:<8} | {row['status']:<14} | {detail} |")
            lines.append("")
        if r.error:
            lines.append(f"> Platform-level error: `{r.error}`")
            lines.append("")

    any_fail = _any_platform_failed(results)
    any_scheduled = any(
        not r.skipped and any(has_scheduled(row["status"]) for row in r.rows)
        for r in results
    )
    any_dry = any(
        not r.skipped and any(parse(row["status"]) is RowStatus.DRY for row in r.rows)
        for r in results
    )
    if not any_scheduled and not any_dry and not any_fail:
        verdict = "✅ Idempotent run — nothing scheduled, nothing failed."
    elif any_fail and any_scheduled:
        verdict = "⚠️  Mixed result — some posts scheduled, some failed. See detail above."
    elif any_fail:
        verdict = "❌ Run finished with failures — see detail sections above."
    else:
        verdict = "✅ Run finished successfully — see detail for scheduled posts."
    lines.append(f"**Verdict:** {verdict}")
    lines.append("")
    return "\n".join(lines)


def _build_result_json(args: argparse.Namespace,
                       results: list[PlatformResult],
                       started_at: datetime,
                       finished_at: datetime,
                       exit_code: int,
                       summary_path: Path) -> dict:
    """Machine-readable run record consumed by the planning tab + the
    ``/schedule-autoheal`` skill. Each row is decorated with the screenshot
    path (lifted out of the inline ``detail`` text) and a ``failure_kind`` from
    the shared classifier so the heal loop knows which failures are UI-drift
    (the only kind it may auto-fix)."""
    platforms: list[dict] = []
    for r in results:
        rows: list[dict] = []
        for row in r.rows:
            status = row.get("status", "OTHER")
            detail = row.get("detail", "") or ""
            rows.append({
                "platform": r.name,
                "day": row.get("day", ""),
                "status": status,
                "detail": detail,
                "screenshot": extract_screenshot(detail),
                "failure_kind": classify_failure(status, detail),
            })
        platforms.append({
            "platform": r.name,
            "skipped": r.skipped,
            "exit_code": r.exit_code,
            "duration_s": round(r.duration_s, 2),
            "error": r.error,
            "rows": rows,
        })

    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "mode": _result_mode(args),
        "exit_code": exit_code,
        "verdict": "clean" if exit_code == 0 else "failures",
        "summary_path": str(summary_path),
        "platforms": platforms,
    }


def _result_mode(args: argparse.Namespace) -> str:
    """The run mode as the summary, the log and the result record all name it."""
    if args.live:
        return "LIVE"
    if args.dry_run:
        return "DRY-RUN"
    return "config default"


def _skipped_stages(args: argparse.Namespace) -> list[str]:
    """The stages the operator opted out of, so a killed run's record can tell them from stages it never reached."""
    return [name for name in run_state.STAGES if getattr(args, f"skip_{name.lower()}")]


def _record_progress(args: argparse.Namespace, started_at: datetime, stage: str,
                     results: list[PlatformResult]) -> None:
    """Publish the in-flight record before ``stage`` starts.

    A failed write is logged and the run goes on: the record is a trace for the
    case where the run dies, not something worth dying for.
    """
    assert logger is not None
    record = run_state.running_record(
        mode=_result_mode(args), started_at=started_at, stage=stage,
        completed=[r.name for r in results], skipped=_skipped_stages(args),
    )
    try:
        run_state.write_result(record, started_at)
    except OSError as err:
        logger.warning("⚠️ Could not write the in-flight result record: %s", err)


def main() -> int:
    args = parse_args()
    configure_logger(args.debug)
    assert logger is not None
    started_at = datetime.now()

    logger.info("🚀 Planning pipeline starting (%s, pid %d, parent pid %d)",
                _result_mode(args), os.getpid(), os.getppid())

    results: list[PlatformResult] = []
    scheduler_args = _build_scheduler_args(args)
    # Looked up here, not at import, so a test can swap one scheduler out.
    schedulers = {
        "LinkedIn": run_linkedin, "Instagram": run_instagram, "Twitter": run_twitter,
        "Threads": run_threads, "Videos": run_videos,
    }
    # Run order comes from run_state.STAGES, the list the killed-run resume command is built from.
    for name in run_state.STAGES:
        _record_progress(args, started_at, name, results)
        if name == "Clone":
            results.append(_run_clone(args))
        else:
            skip = getattr(args, f"skip_{name.lower()}")
            results.append(_run_platform(name, schedulers[name], scheduler_args, skip))

    finished_at = datetime.now()
    md = _build_summary_md(args, results, started_at, finished_at)
    out_path = run_state.write_summary(md, started_at)
    logger.info("📝 Summary written to %s", out_path)
    print()
    print(md)

    any_fail = _any_platform_failed(results)
    exit_code = 0 if not any_fail else 11

    result = _build_result_json(args, results, started_at, finished_at, exit_code, out_path)
    result_path = run_state.write_result(result, started_at)
    logger.info("🧾 Result JSON written to %s", result_path)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
