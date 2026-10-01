"""Podcast episode pipeline: raw two-track recording → cleaned transcript,
styled 1:1 + 9:16 clips with copy, covers, episode document and website page.

Entry point is ``podcast_pipeline.py`` at the repo root; the Streamlit surface
is ``app/tab_podcast.py``. Episode facts (guest name, tracks, roles) live in a
local ``episode.json`` next to the recording, never in this public repo.
"""
