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
import json
import logging
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
from planning._failure import classify as classify_failure, extract_screenshot  # noqa: E402
from planning.instagram.clone_to_other_platforms import main as run_clone  # noqa: E402
from planning.linkedin.schedule_linkedin_posts import main as run_linkedin  # noqa: E402
from planning.instagram.schedule_instagram_posts import main as run_instagram  # noqa: E402
from planning.twitter.schedule_twitter_posts import main as run_twitter  # noqa: E402
from planning.threads.schedule_threads_posts import main as run_threads  # noqa: E402
from planning.videos.schedule_videos_posts import main as run_videos  # noqa: E402


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
        counts = {"LIVE": 0, "DRY": 0, "FAIL": 0, "LOGIN-REQUIRED": 0, "OTHER": 0}
        for r in self.rows:
            key = r["status"] if r["status"] in counts else "OTHER"
            counts[key] += 1
        return counts


def configure_logger(debug: bool) -> logging.Logger:
    global logger
    level = logging.DEBUG if debug else logging.INFO
    logger = setup_logger("planning_pipeline", file_logging=True, level=level)
    return logger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Planning pipeline: schedule every WIP row across LI → IG → TW → TH."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                     help="Walk each platform's flow up to the final Schedule action; do NOT submit.")
    mode.add_argument("--live", action="store_true",
                     help="Actually schedule posts on all four platforms.")
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


def _run_clone(args: argparse.Namespace) -> PlatformResult:
    """Run the IG → TW/TH/SB clone as step 0. Failures do NOT block schedulers."""
    assert logger is not None
    if args.skip_clone:
        logger.info("⏭️  Skipping Clone (per --skip-clone).")
        return PlatformResult(name="Clone", skipped=True)

    logger.info("━━━━━━━━━━ Clone (IG → TW/TH/SB) ━━━━━━━━━━")
    clone_args = _build_clone_args(args)
    orig_argv = sys.argv.copy()
    sys.argv = [orig_argv[0]] + clone_args
    start = time.monotonic()
    try:
        exit_code = run_clone()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 99
        duration = time.monotonic() - start
        logger.error("❌ Clone exited via SystemExit(%s) — continuing to schedulers.", code)
        return PlatformResult(name="Clone", exit_code=code, rows=[], duration_s=duration,
                              error=f"SystemExit({code})")
    except Exception as exc:  # noqa: BLE001 — orchestrator MUST swallow
        duration = time.monotonic() - start
        logger.exception("❌ Clone raised — continuing to schedulers.")
        return PlatformResult(name="Clone", exit_code=99, rows=[], duration_s=duration,
                              error=str(exc))
    finally:
        sys.argv = orig_argv

    duration = time.monotonic() - start
    if exit_code == 0:
        logger.info("✅ Clone finished in %s.", _fmt_duration(duration))
        return PlatformResult(name="Clone", exit_code=0, rows=[], duration_s=duration)
    logger.error("❌ Clone finished with exit code %s in %s — continuing to schedulers.",
                 exit_code, _fmt_duration(duration))
    return PlatformResult(name="Clone", exit_code=exit_code, rows=[], duration_s=duration,
                          error=f"exit code {exit_code}")


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
    logger.info("✅ %s finished in %s with %d row(s).",
                name, _fmt_duration(duration), len(rows))
    return PlatformResult(name=name, exit_code=exit_code, rows=rows, duration_s=duration)


def _fmt_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _build_summary_md(args: argparse.Namespace,
                      results: list[PlatformResult],
                      started_at: datetime,
                      finished_at: datetime) -> str:
    if args.live:
        mode_label = "LIVE"
    elif args.dry_run:
        mode_label = "DRY-RUN"
    else:
        mode_label = "config default (per-platform)"

    total_duration = (finished_at - started_at).total_seconds()
    lines: list[str] = [
        f"# Planning summary — {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"- Mode: **{mode_label}**",
        f"- Total duration: {_fmt_duration(total_duration)}",
        "",
        "| Platform  | LIVE | DRY  | FAIL | LOGIN | OTHER | Duration |",
        "|-----------|-----:|-----:|-----:|------:|------:|---------:|",
    ]
    for r in results:
        if r.skipped:
            lines.append(f"| {r.name:<9} |   -  |   -  |   -  |   -   |   -   | skipped  |")
            continue
        c = r.status_counts
        lines.append(
            f"| {r.name:<9} | {c['LIVE']:>4} | {c['DRY']:>4} | {c['FAIL']:>4} | "
            f"{c['LOGIN-REQUIRED']:>5} | {c['OTHER']:>5} | {_fmt_duration(r.duration_s):>8} |"
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

    any_fail = any(
        not r.skipped and (r.error or any(row["status"] in ("FAIL", "LOGIN-REQUIRED") for row in r.rows))
        for r in results
    )
    any_scheduled = any(
        not r.skipped and any(row["status"] == "LIVE" for row in r.rows)
        for r in results
    )
    any_dry = any(
        not r.skipped and any(row["status"] == "DRY" for row in r.rows)
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


def _write_summary(md: str, started_at: datetime) -> Path:
    out_dir = Path(__file__).parent / "results" / "planning"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{started_at.strftime('%Y-%m-%d-%H%M%S')}-summary.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path


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
    if args.live:
        mode_label = "LIVE"
    elif args.dry_run:
        mode_label = "DRY-RUN"
    else:
        mode_label = "config default"

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
        "mode": mode_label,
        "exit_code": exit_code,
        "verdict": "clean" if exit_code == 0 else "failures",
        "summary_path": str(summary_path),
        "platforms": platforms,
    }


def _write_result_json(result: dict, started_at: datetime) -> Path:
    """Write the timestamped result record plus a stable ``latest-result.json``
    pointer so the app can find the newest run deterministically."""
    out_dir = Path(__file__).parent / "results" / "planning"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    out_path = out_dir / f"{started_at.strftime('%Y-%m-%d-%H%M%S')}-result.json"
    out_path.write_text(payload, encoding="utf-8")
    (out_dir / "latest-result.json").write_text(payload, encoding="utf-8")
    return out_path


def main() -> int:
    args = parse_args()
    configure_logger(args.debug)
    assert logger is not None
    started_at = datetime.now()

    if args.live:
        mode_label = "LIVE"
    elif args.dry_run:
        mode_label = "DRY-RUN"
    else:
        mode_label = "per-platform config default"
    logger.info("🚀 Planning pipeline starting (%s)", mode_label)

    results: list[PlatformResult] = []
    results.append(_run_clone(args))

    scheduler_args = _build_scheduler_args(args)
    for name, fn, skip in [
        ("LinkedIn",  run_linkedin,  args.skip_linkedin),
        ("Instagram", run_instagram, args.skip_instagram),
        ("Twitter",   run_twitter,   args.skip_twitter),
        ("Threads",   run_threads,   args.skip_threads),
        ("Videos",    run_videos,    args.skip_videos),
    ]:
        results.append(_run_platform(name, fn, scheduler_args, skip))

    finished_at = datetime.now()
    md = _build_summary_md(args, results, started_at, finished_at)
    out_path = _write_summary(md, started_at)
    logger.info("📝 Summary written to %s", out_path)
    print()
    print(md)

    any_fail = any(
        not r.skipped and (r.error or any(row["status"] in ("FAIL", "LOGIN-REQUIRED") for row in r.rows))
        for r in results
    )
    exit_code = 0 if not any_fail else 11

    result = _build_result_json(args, results, started_at, finished_at, exit_code, out_path)
    result_path = _write_result_json(result, started_at)
    logger.info("🧾 Result JSON written to %s", result_path)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
