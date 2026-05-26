"""Playwright-based metric scrapers — alternative path to the RapidAPI client.

Each platform module exposes ``fetch_profile(target_date)`` and
``fetch_posts(target_date)`` which return the ``data`` payload dict that the
RapidAPI flow's ``save_results`` will wrap into the canonical envelope shape:

    {"date": YYYY-MM-DD, "platform": <p>, "data_type": <profile|posts>, "data": <payload>}

Dispatch is wired through ``reporting/social_client/social_api_client.py``,
which inspects the per-endpoint ``"source"`` key in ``config.json`` and either
makes the HTTP call (rapidapi) or calls into this package (playwright).
"""
