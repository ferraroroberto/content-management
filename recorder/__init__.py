"""Self-hosted two-side podcast recorder.

Each participant opens a link, records their own camera and mic locally at
full resolution, and the page uploads the recording in chunks while it runs.
The finished file lands in the episode's ``video editing/`` folder, ready for
``podcast_pipeline.py``. See ``recorder/README.md``.
"""
