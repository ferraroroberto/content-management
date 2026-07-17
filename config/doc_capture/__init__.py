"""Deterministic, fail-safe screenshot engine for the control-panel docs.

``docs/screenshots/manifest.json`` is the contract: one entry per control-panel
section, each declaring the source files that render it (idempotency input),
how to reach it, what to wait for, and — mandatory — which regions to mask.
A feature with no mask selectors is refused, never captured raw.

Run via the CLI::

    & .\\.venv\\Scripts\\python.exe -m config.doc_capture all

See ``config/doc_capture/engine.py`` for the mechanics and issue #110 for the
design decisions.
"""
