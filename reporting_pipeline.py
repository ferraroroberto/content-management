#!/usr/bin/env python
"""
Initialization script to run the complete data processing pipeline:
1. social_api_client - Fetch data from social media APIs
2. data_processor - Process and transform the raw data
3. profile_aggregator - Aggregate profile data across platforms
4. posts_consolidator - Consolidate posts data across platforms
5. notion_update - Update Notion databases with processed data
6. substack.daily_pipeline - Publish daily Substack Note (follower scrape now lives in reporting/scrape_client/substack.py)
"""

import os
import sys
import json
import argparse
import importlib.util
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

# Add the current directory to the Python path
sys.path.append(str(Path(__file__).parent))
# Console can be cp1252 on Windows; force UTF-8 so emoji in logs don't blow up.
from config.console import force_utf8_stdio  # noqa: E402
force_utf8_stdio()
from config.logger_config import setup_logger  # noqa: E402
from config.loader import load_full_config  # noqa: E402
from reporting.social_client.social_api_client import (
    main as run_social_api_client,
    configure_logger as configure_social_logger,
    check_file_exists_for_date,
)
from reporting.process.data_processor import main as run_data_processor, configure_logger as configure_data_processor_logger
from reporting.process.profile_aggregator import main as run_profile_aggregator, configure_logger as configure_profile_logger
from reporting.process.posts_consolidator import main as run_posts_consolidator, configure_logger as configure_posts_logger
from reporting.notion.notion_update import main as run_notion_update, configure_logger as configure_notion_logger
from planning.substack.daily_pipeline import main as run_substack_daily_pipeline

# Set up logger. Module-scope default is a real (unconfigured) Logger —
# never None — so every `logger.info(...)` call below is a plain Logger
# method call with no Optional to silence. `configure_logger()` replaces it
# with the fully-configured (handlers + formatter) instance before the
# pipeline actually runs.
logger: logging.Logger = logging.getLogger("init")

def configure_logger(debug_mode=False):
    """Set up logger with appropriate level based on debug mode."""
    global logger
    log_level = logging.DEBUG if debug_mode else logging.INFO
    logger = setup_logger("init", file_logging=False, level=log_level)
    return logger

class PipelineFailures:
    """Accumulates hard failures during a run for one consolidated alert.

    Three depths are tracked (issues #76, #84):

    * ``step_failures`` — a pipeline step raised (recorded by ``run_module``).
    * ``missing_endpoints`` — a configured endpoint produced no raw JSON file
      for the processing date (recorded by ``check_endpoint_coverage``); this
      catches the ``None``→no-file case that the per-endpoint loop swallows.
    * ``missing_post_metrics`` — a platform's *consolidated* post metrics are
      absent for the date (recorded by ``check_posts_coverage``); this catches
      the case where the raw file exists but holds no post the consolidator can
      match (e.g. the day's note was dropped before it reached the DB).
    * ``policy_drift`` — a public table lost RLS / an ``anon_*_all`` policy, or
      a view lost ``security_invoker`` (recorded by ``check_policy_drift_coverage``);
      this catches Supabase security drift on the project's own clock instead of
      waiting for the weekly security-advisor email (issue #50).
    """

    def __init__(self) -> None:
        self.step_failures: list[tuple[str, str]] = []
        self.missing_endpoints: list[str] = []
        self.missing_post_metrics: list[str] = []
        self.policy_drift: list[str] = []

    def any(self) -> bool:
        return bool(self.step_failures or self.missing_endpoints
                    or self.missing_post_metrics or self.policy_drift)


def _load_config() -> dict | None:
    """Load ``config/config.json`` for coverage + Slack channel resolution.

    Wraps ``config.loader.load_full_config`` (the project's single-source
    loader) but, unlike it, degrades to ``None`` on a missing/corrupt
    config.json rather than raising — coverage checks and the Slack channel
    resolution are best-effort here; the failure alert must still fire even
    if config couldn't be read.
    """
    try:
        return load_full_config()
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"❌ Could not load config for failure detection: {e}")
        return None


def check_endpoint_coverage(config: dict, processing_date: str, failures: "PipelineFailures") -> None:
    """Record any configured endpoint missing its raw JSON file for the date.

    Uses the same endpoint filter (`config` blocks carrying an ``api_url``) and
    the same ``<platform>_<data_type>_<date>.json`` path logic the social client
    writes with, by reusing ``check_file_exists_for_date``.
    """
    config_endpoints = {k: v for k, v in config.items() if isinstance(v, dict) and 'api_url' in v}
    for platform_key in config_endpoints:
        exists, _ = check_file_exists_for_date(platform_key, config, processing_date)
        if not exists:
            failures.missing_endpoints.append(platform_key)
            logger.error(f"❌ Missing expected data file for {platform_key} on {processing_date}")
    if not failures.missing_endpoints:
        logger.info(f"✅ All {len(config_endpoints)} expected endpoint files present for {processing_date}")


# Platforms whose consolidated post metrics we expect on every daily run.
COVERAGE_PLATFORMS = ("linkedin", "instagram", "twitter", "threads", "substack")


def _substack_had_editorial_content(day_yyyymmdd: str) -> Optional[bool]:
    """Whether a Substack editorial row with body text existed for ``day_yyyymmdd``.

    Unlike the other platforms, Substack Notes aren't scheduled automation —
    ``post_substack_note.py`` publishes only when the day's editorial row exists
    with non-empty body text, and treats a missing row as a normal no-op (rc=4),
    not a failure. So a day with no editorial row genuinely has nothing to post,
    and the resulting gap in ``posts`` is expected, not a coverage bug.

    Returns ``True``/``False``, or ``None`` if the check itself couldn't run
    (Notion unreachable, config missing) — callers should then fall back to
    treating the missing metrics as a real failure rather than silently
    downgrading it on an unverifiable guess.
    """
    try:
        from planning.substack.substack_session import load_notion_token, load_substack_config
        from reporting.notion.editorial import get_field, get_row_by_day, init_notion_client
        cfg = load_substack_config()
        notion = init_notion_client(load_notion_token())
        if notion is None:
            return None
        row = get_row_by_day(notion, cfg["editorial_db_id"], day_yyyymmdd, cfg["notion_columns"])
        if row is None:
            return False
        body_text = get_field(row, "text_body", cfg["notion_columns"]) or ""
        return bool(str(body_text).strip())
    except Exception as e:
        logger.warning(f"⚠️ Could not verify Substack editorial content for {day_yyyymmdd}: {e}")
        return None


def check_posts_coverage(processing_date: str, failures: "PipelineFailures") -> None:
    """Record any platform whose consolidated post metrics are absent for the date.

    ``check_endpoint_coverage`` is presence-only — it cannot see this: a raw
    file can exist yet hold no post the consolidator can match (``posted_at =
    date - 1 day``), leaving every ``*_<platform>_*`` column NULL (issue #84).
    Here we read the consolidated ``posts`` row that actually feeds Notion and
    flag any platform with neither a video nor a non-video ``post_id``. DB
    errors degrade gracefully (logged) — the run is never crashed by the check.

    Substack is special-cased: if the editorial calendar genuinely had nothing
    queued for the day these metrics would cover (``processing_date - 1``),
    the gap is logged as an alert, not recorded as a failure (issue #182).
    """
    try:
        from reporting.process.supabase_uploader import get_db_connection
        connection = get_db_connection()
        if not connection:
            logger.error("❌ Posts-coverage check: no DB connection — skipped")
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM posts WHERE date = %s LIMIT 1", (processing_date,))
                row = cursor.fetchone()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
        finally:
            connection.close()

        if not row:
            failures.missing_post_metrics.append("(no consolidated posts row)")
            logger.error(f"❌ Posts-coverage: no consolidated posts row for {processing_date}")
            return

        data = dict(zip(columns, row))
        for platform in COVERAGE_PLATFORMS:
            has_metrics = data.get(f"post_id_{platform}_no_video") or data.get(f"post_id_{platform}_video")
            if has_metrics:
                continue
            if platform == "substack":
                covered_day = (datetime.strptime(processing_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y%m%d")
                had_content = _substack_had_editorial_content(covered_day)
                if had_content is False:
                    logger.warning(f"⚠️ Posts-coverage: substack had no editorial content queued for {covered_day} — no post metrics for {processing_date} is expected, not a failure")
                    continue
            failures.missing_post_metrics.append(platform)
            logger.error(f"❌ Posts-coverage: no post metrics for {platform} on {processing_date}")
        if not failures.missing_post_metrics:
            logger.info(f"✅ Posts-coverage: all {len(COVERAGE_PLATFORMS)} platforms have post metrics for {processing_date}")
    except Exception as e:
        logger.error(f"❌ Posts-coverage check failed: {e}")


def check_policy_drift_coverage(failures: "PipelineFailures") -> None:
    """Record any Supabase RLS / policy / view-security drift for the run.

    Reuses ``supabase_policy_script.check_policy_drift`` (read-only) so a public
    table that lost RLS or an ``anon_*_all`` policy — or a view that lost
    ``security_invoker`` — is caught on the daily run instead of via the weekly
    security-advisor email (issue #50). DB errors degrade gracefully (logged) —
    mirrors ``check_posts_coverage``; the run is never crashed by this posture
    check. Remediation stays a deliberate human action: re-run
    ``reporting/process/supabase_policy_script.sql``, then re-run the pipeline.
    """
    try:
        from reporting.process.supabase_uploader import get_db_connection
        from reporting.process.supabase_policy_script import check_policy_drift, summarize_drift
        connection = get_db_connection()
        if not connection:
            logger.error("❌ Policy-drift check: no DB connection — skipped")
            return
        try:
            result = check_policy_drift(connection)
        finally:
            connection.close()

        drift = summarize_drift(result)
        if not drift:
            logger.info(
                f"✅ Policy-drift: RLS + policies intact across {result['total_tables']} "
                f"tables, {result['total_views']} views"
            )
            return

        for kind, summary in drift:
            failures.policy_drift.append(f"{kind} {summary}")
            logger.error(f"❌ Policy-drift: {kind} {summary}")
    except Exception as e:
        logger.error(f"❌ Policy-drift check failed: {e}")


def _resolve_reporting_channel(config: dict | None) -> str:
    """Slack target: ``slack.reporting_channel`` → falls back to ``slack.autoheal_channel``."""
    slack_cfg = config.get("slack", {}) if config else {}
    channel = (slack_cfg.get("reporting_channel") or "").strip()
    if channel:
        return channel
    return (slack_cfg.get("autoheal_channel") or "").strip()


def _load_slack_notify():
    """Import the fleet-wide Slack helper from ``~/.claude/hooks/slack_notify.py``.

    Returns the module, or ``None`` if it can't be located/imported (logged).
    The helper is provided by the ``fleet-config`` project and reused fleet-wide
    (the same transport ``/schedule-autoheal`` uses) — do not reimplement it.
    """
    helper = Path.home() / ".claude" / "hooks" / "slack_notify.py"
    if not helper.exists():
        logger.error(f"❌ Slack helper not found at {helper} — alert not sent")
        return None
    try:
        spec = importlib.util.spec_from_file_location("slack_notify", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        return module
    except Exception as e:
        logger.error(f"❌ Could not import Slack helper: {e}")
        return None


def _build_alert_message(failures: "PipelineFailures", processing_date: str) -> str:
    """Deterministic, mobile-skimmable alert body: date, steps, endpoints, drift."""
    lines = [f"🚨 Reporting pipeline finished with failures — {processing_date}"]
    if failures.step_failures:
        lines.append("")
        lines.append("Failed steps:")
        for name, error in failures.step_failures:
            lines.append(f"• {name}: {error}")
    if failures.missing_endpoints:
        lines.append("")
        lines.append("Missing endpoints:")
        for endpoint in failures.missing_endpoints:
            lines.append(f"• {endpoint}")
    if failures.missing_post_metrics:
        lines.append("")
        lines.append("No post metrics (platform):")
        for platform in failures.missing_post_metrics:
            lines.append(f"• {platform}")
    if failures.policy_drift:
        lines.append("")
        lines.append("RLS/policy drift:")
        for entry in failures.policy_drift:
            lines.append(f"• {entry}")
    return "\n".join(lines)


def send_failure_alert(failures: "PipelineFailures", processing_date: str, config: dict | None) -> None:
    """Send exactly one consolidated Slack alert for a failed run.

    Channel resolution and missing token/channel degrade gracefully (logged);
    the non-zero exit in ``main()`` is the independent second signal regardless.
    """
    message = _build_alert_message(failures, processing_date)
    logger.error("🚨 Pipeline finished with failures:\n%s", message)

    channel = _resolve_reporting_channel(config)
    if not channel:
        logger.error("❌ No Slack channel configured (slack.reporting_channel / slack.autoheal_channel) — alert not sent")
        return

    slack = _load_slack_notify()
    if slack is None:
        return

    if not slack.notify(message, channel=channel):
        logger.error("❌ Slack alert delivery failed (see slack_notify logs)")


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Run the complete data processing pipeline.')
    parser.add_argument('-d', '--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('-s', '--skip-api', action='store_true', help='Skip the API data collection step')
    parser.add_argument('-p', '--skip-processing', action='store_true', help='Skip the data processing step')
    parser.add_argument('-a', '--skip-aggregation', action='store_true', help='Skip the profile aggregation step')
    parser.add_argument('-c', '--skip-consolidation', action='store_true', help='Skip the posts consolidation step')
    parser.add_argument('-n', '--skip-notion', action='store_true', help='Skip the Notion update step')
    parser.add_argument('-b', '--skip-substack', action='store_true', help='Skip the Substack daily pipeline (publish daily Note)')
    parser.add_argument('-y', '--yes', action='store_true',
                        help='Auto-confirm interactive prompts in sub-steps (e.g. Notion update). Use this for unattended/scheduled runs.')
    parser.add_argument('--date', type=str, help='Reference date in YYYYMMDD format. Will process the day before this date.')
    return parser.parse_args()

def run_module(module_func, module_name, debug_mode=False, extra_args=None, failures=None):
    """
    Run a module with clean command line arguments.

    Args:
        module_func: The module's main function to run
        module_name: Name of the module for logging
        debug_mode: Whether to enable debug mode
        extra_args: List of additional arguments to pass
        failures: Optional PipelineFailures accumulator to record step exceptions
    """
    # Save original command line arguments
    original_argv = sys.argv.copy()
    
    try:
        # Reset sys.argv to just the script name
        sys.argv = [original_argv[0]]
        
        # Add debug flag if needed
        if debug_mode:
            sys.argv.append('--debug')
            
        # Add any extra arguments
        if extra_args:
            sys.argv.extend(extra_args)
            
        # Run the module
        module_func()
        logger.info(f"✅ {module_name} completed successfully")
    except Exception as e:
        logger.error(f"❌ Error in {module_name}: {e}")
        if failures is not None:
            failures.step_failures.append((module_name, str(e)))
        if debug_mode:
            raise
    finally:
        # Restore original arguments
        sys.argv = original_argv.copy()

def run_pipeline(debug_mode=False, skip_api=False, skip_processing=False,
                skip_aggregation=False, skip_consolidation=False, skip_notion=False,
                skip_substack=False, reference_date=None, auto_confirm=False):
    """Run the complete data processing pipeline.

    Returns the PipelineFailures accumulator so the caller can set a non-zero
    exit code when the run dropped data.
    """
    # Configure the main logger
    configure_logger(debug_mode)

    # Failure detection: collect step exceptions + missing-endpoint coverage,
    # then emit one consolidated Slack alert at the end (issue #76).
    failures = PipelineFailures()
    config = _load_config()

    logger.info("🚀 Starting the complete data processing pipeline")
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")
    
    # Use the reference date directly or today's date
    if reference_date:
        try:
            # Normalize date to YYYY-MM-DD
            if '-' in reference_date:
                processing_date = reference_date
            else:
                processing_date = datetime.strptime(reference_date, "%Y%m%d").strftime("%Y-%m-%d")
            logger.info(f"📅 Using specified date: {processing_date}")
        except ValueError:
            logger.error(f"❌ Invalid date format: {reference_date}. Using current date.")
            processing_date = datetime.now().strftime("%Y-%m-%d")
    else:
        processing_date = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"📅 No date specified. Using current date: {processing_date}")
    
    # Prepare common arguments
    date_args = ['--date', processing_date]
    
    # Step 1: Fetch data from social media APIs
    if not skip_api:
        logger.info("📡 Step 1: Running Social API Client")
        configure_social_logger(debug_mode)
        run_module(run_social_api_client, "Social API Client", debug_mode, extra_args=date_args, failures=failures)
        # Coverage check: which configured endpoints produced no file for the date.
        if config:
            check_endpoint_coverage(config, processing_date, failures)
    else:
        logger.info("⏭️ Skipping Social API Client step")
    
    # Step 2: Process the raw data
    if not skip_processing:
        logger.info("🔄 Step 2: Running Data Processor")
        configure_data_processor_logger(debug_mode)
        run_module(run_data_processor, "Data Processor", debug_mode, extra_args=date_args, failures=failures)
    else:
        logger.info("⏭️ Skipping Data Processor step")
    
    # Step 3: Aggregate profile data
    if not skip_aggregation:
        logger.info("📊 Step 3: Running Profile Aggregator")
        configure_profile_logger()
        run_module(run_profile_aggregator, "Profile Aggregator", debug_mode, failures=failures)
    else:
        logger.info("⏭️ Skipping Profile Aggregator step")
    
    # Step 4: Consolidate posts data
    if not skip_consolidation:
        logger.info("📑 Step 4: Running Posts Consolidator")
        configure_posts_logger(debug_mode)
        run_module(run_posts_consolidator, "Posts Consolidator", debug_mode, failures=failures)
        # Content-coverage check: did every platform actually land post metrics
        # in the consolidated table (not just produce a raw file)? — issue #84.
        check_posts_coverage(processing_date, failures)
    else:
        logger.info("⏭️ Skipping Posts Consolidator step")
        
    # Step 5: Update Notion with processed data
    if not skip_notion:
        logger.info("📘 Step 5: Running Notion Update")
        configure_notion_logger(debug_mode)
        logger.info(f"🗓️  Using date for Notion update: {processing_date}")
        notion_extra_args = [processing_date]
        if auto_confirm:
            notion_extra_args.append('--yes')
        run_module(run_notion_update, "Notion Update", debug_mode, notion_extra_args, failures=failures)
    else:
        logger.info("⏭️ Skipping Notion Update step")

    # Step 6: Publish Substack Note (follower scrape now happens in step 1 via reporting/scrape_client/substack.py)
    if not skip_substack:
        logger.info("📰 Step 6: Running Substack Daily Pipeline")
        run_module(run_substack_daily_pipeline, "Substack Daily Pipeline", debug_mode, extra_args=date_args, failures=failures)
    else:
        logger.info("⏭️ Skipping Substack Daily Pipeline step")

    # Posture check: Supabase RLS / policy / view-security drift (issue #50).
    # Read-only and independent of the daily data; runs every pipeline so drift
    # surfaces on the project's own clock, not via the weekly advisor email.
    logger.info("🔒 Step 7: Checking Supabase RLS / policy drift")
    check_policy_drift_coverage(failures)

    # Notify only on failure: one consolidated alert + a non-zero exit signal.
    if failures.any():
        send_failure_alert(failures, processing_date, config)
        logger.error("❌ Complete data processing pipeline finished WITH FAILURES")
    else:
        logger.info("🎉 Complete data processing pipeline finished")

    return failures

def main():
    """Main function to run the complete pipeline."""
    args = parse_arguments()

    failures = run_pipeline(
        debug_mode=args.debug,
        skip_api=args.skip_api,
        skip_processing=args.skip_processing,
        skip_aggregation=args.skip_aggregation,
        skip_consolidation=args.skip_consolidation,
        skip_notion=args.skip_notion,
        skip_substack=args.skip_substack,
        reference_date=args.date,
        auto_confirm=args.yes,
    )

    # Second, independent failure signal so the launcher / scheduler can react.
    if failures and failures.any():
        sys.exit(1)

if __name__ == "__main__":
    main()
