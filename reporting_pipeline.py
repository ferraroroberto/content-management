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

# Console can be cp1252 on Windows; force UTF-8 so emoji in logs don't blow up.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Add the current directory to the Python path
sys.path.append(str(Path(__file__).parent))
from config.logger_config import setup_logger
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

# Set up logger
logger: logging.Logger | None = None

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
    """

    def __init__(self) -> None:
        self.step_failures: list[tuple[str, str]] = []
        self.missing_endpoints: list[str] = []
        self.missing_post_metrics: list[str] = []

    def any(self) -> bool:
        return bool(self.step_failures or self.missing_endpoints or self.missing_post_metrics)


def _load_config() -> dict | None:
    """Load ``config/config.json`` for coverage + Slack channel resolution."""
    config_path = Path(__file__).parent / "config" / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"❌ Could not load config for failure detection: {e}")  # type: ignore
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
            logger.error(f"❌ Missing expected data file for {platform_key} on {processing_date}")  # type: ignore
    if not failures.missing_endpoints:
        logger.info(f"✅ All {len(config_endpoints)} expected endpoint files present for {processing_date}")  # type: ignore


# Platforms whose consolidated post metrics we expect on every daily run.
COVERAGE_PLATFORMS = ("linkedin", "instagram", "twitter", "threads", "substack")


def check_posts_coverage(processing_date: str, failures: "PipelineFailures") -> None:
    """Record any platform whose consolidated post metrics are absent for the date.

    ``check_endpoint_coverage`` is presence-only — it cannot see this: a raw
    file can exist yet hold no post the consolidator can match (``posted_at =
    date - 1 day``), leaving every ``*_<platform>_*`` column NULL (issue #84).
    Here we read the consolidated ``posts`` row that actually feeds Notion and
    flag any platform with neither a video nor a non-video ``post_id``. DB
    errors degrade gracefully (logged) — the run is never crashed by the check.
    """
    try:
        from reporting.process.supabase_uploader import get_db_connection
        connection = get_db_connection()
        if not connection:
            logger.error("❌ Posts-coverage check: no DB connection — skipped")  # type: ignore
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
            logger.error(f"❌ Posts-coverage: no consolidated posts row for {processing_date}")  # type: ignore
            return

        data = dict(zip(columns, row))
        for platform in COVERAGE_PLATFORMS:
            has_metrics = data.get(f"post_id_{platform}_no_video") or data.get(f"post_id_{platform}_video")
            if not has_metrics:
                failures.missing_post_metrics.append(platform)
                logger.error(f"❌ Posts-coverage: no post metrics for {platform} on {processing_date}")  # type: ignore
        if not failures.missing_post_metrics:
            logger.info(f"✅ Posts-coverage: all {len(COVERAGE_PLATFORMS)} platforms have post metrics for {processing_date}")  # type: ignore
    except Exception as e:
        logger.error(f"❌ Posts-coverage check failed: {e}")  # type: ignore


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
    The helper is provided by the ``claude-config`` project and reused fleet-wide
    (the same transport ``/schedule-autoheal`` uses) — do not reimplement it.
    """
    helper = Path.home() / ".claude" / "hooks" / "slack_notify.py"
    if not helper.exists():
        logger.error(f"❌ Slack helper not found at {helper} — alert not sent")  # type: ignore
        return None
    try:
        spec = importlib.util.spec_from_file_location("slack_notify", helper)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        return module
    except Exception as e:
        logger.error(f"❌ Could not import Slack helper: {e}")  # type: ignore
        return None


def _build_alert_message(failures: "PipelineFailures", processing_date: str) -> str:
    """Deterministic, mobile-skimmable alert body listing date, steps, endpoints."""
    lines = [f"🚨 Reporting pipeline dropped data — {processing_date}"]
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
    return "\n".join(lines)


def send_failure_alert(failures: "PipelineFailures", processing_date: str, config: dict | None) -> None:
    """Send exactly one consolidated Slack alert for a failed run.

    Channel resolution and missing token/channel degrade gracefully (logged);
    the non-zero exit in ``main()`` is the independent second signal regardless.
    """
    message = _build_alert_message(failures, processing_date)
    logger.error("🚨 Pipeline finished with failures:\n%s", message)  # type: ignore

    channel = _resolve_reporting_channel(config)
    if not channel:
        logger.error("❌ No Slack channel configured (slack.reporting_channel / slack.autoheal_channel) — alert not sent")  # type: ignore
        return

    slack = _load_slack_notify()
    if slack is None:
        return

    if not slack.notify(message, channel=channel):
        logger.error("❌ Slack alert delivery failed (see slack_notify logs)")  # type: ignore


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
        logger.info(f"✅ {module_name} completed successfully")  # type: ignore
    except Exception as e:
        logger.error(f"❌ Error in {module_name}: {e}")  # type: ignore
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

    logger.info("🚀 Starting the complete data processing pipeline")  # type: ignore
    logger.info(f"🐞 Debug mode: {'Enabled' if debug_mode else 'Disabled'}")  # type: ignore
    
    # Use the reference date directly or today's date
    if reference_date:
        try:
            # Normalize date to YYYY-MM-DD
            if '-' in reference_date:
                processing_date = reference_date
            else:
                processing_date = datetime.strptime(reference_date, "%Y%m%d").strftime("%Y-%m-%d")
            logger.info(f"📅 Using specified date: {processing_date}")  # type: ignore
        except ValueError:
            logger.error(f"❌ Invalid date format: {reference_date}. Using current date.")  # type: ignore
            processing_date = datetime.now().strftime("%Y-%m-%d")
    else:
        processing_date = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"📅 No date specified. Using current date: {processing_date}")  # type: ignore
    
    # Prepare common arguments
    date_args = ['--date', processing_date]
    
    # Step 1: Fetch data from social media APIs
    if not skip_api:
        logger.info("📡 Step 1: Running Social API Client")  # type: ignore
        configure_social_logger(debug_mode)
        run_module(run_social_api_client, "Social API Client", debug_mode, extra_args=date_args, failures=failures)
        # Coverage check: which configured endpoints produced no file for the date.
        if config:
            check_endpoint_coverage(config, processing_date, failures)
    else:
        logger.info("⏭️ Skipping Social API Client step")  # type: ignore
    
    # Step 2: Process the raw data
    if not skip_processing:
        logger.info("🔄 Step 2: Running Data Processor")  # type: ignore
        configure_data_processor_logger(debug_mode)
        run_module(run_data_processor, "Data Processor", debug_mode, extra_args=date_args, failures=failures)
    else:
        logger.info("⏭️ Skipping Data Processor step")  # type: ignore
    
    # Step 3: Aggregate profile data
    if not skip_aggregation:
        logger.info("📊 Step 3: Running Profile Aggregator")  # type: ignore
        configure_profile_logger()
        run_module(run_profile_aggregator, "Profile Aggregator", debug_mode, failures=failures)
    else:
        logger.info("⏭️ Skipping Profile Aggregator step")  # type: ignore
    
    # Step 4: Consolidate posts data
    if not skip_consolidation:
        logger.info("📑 Step 4: Running Posts Consolidator")  # type: ignore
        configure_posts_logger(debug_mode)
        run_module(run_posts_consolidator, "Posts Consolidator", debug_mode, failures=failures)
        # Content-coverage check: did every platform actually land post metrics
        # in the consolidated table (not just produce a raw file)? — issue #84.
        check_posts_coverage(processing_date, failures)
    else:
        logger.info("⏭️ Skipping Posts Consolidator step")  # type: ignore
        
    # Step 5: Update Notion with processed data
    if not skip_notion:
        logger.info("📘 Step 5: Running Notion Update")  # type: ignore
        configure_notion_logger(debug_mode)
        try:
            logger.info(f"🗓️  Using date for Notion update: {processing_date}")  # type: ignore
            notion_extra_args = [processing_date]
            if auto_confirm:
                notion_extra_args.append('--yes')
            run_module(run_notion_update, "Notion Update", debug_mode, notion_extra_args, failures=failures)
        except Exception as e:
            logger.error(f"❌ Error in Notion Update: {e}")  # type: ignore
            if debug_mode:
                raise
    else:
        logger.info("⏭️ Skipping Notion Update step")  # type: ignore

    # Step 6: Publish Substack Note (follower scrape now happens in step 1 via reporting/scrape_client/substack.py)
    if not skip_substack:
        logger.info("📰 Step 6: Running Substack Daily Pipeline")  # type: ignore
        try:
            run_module(run_substack_daily_pipeline, "Substack Daily Pipeline", debug_mode, extra_args=date_args, failures=failures)
        except Exception as e:
            logger.error(f"❌ Error in Substack Daily Pipeline: {e}")  # type: ignore
            if debug_mode:
                raise
    else:
        logger.info("⏭️ Skipping Substack Daily Pipeline step")  # type: ignore

    # Notify only on failure: one consolidated alert + a non-zero exit signal.
    if failures.any():
        send_failure_alert(failures, processing_date, config)
        logger.error("❌ Complete data processing pipeline finished WITH FAILURES")  # type: ignore
    else:
        logger.info("🎉 Complete data processing pipeline finished")  # type: ignore

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
