"""Locate the PDF for a LinkedIn carousel post.

A carousel post in Notion points at a folder under
``<thread_root>/<books|monographic>/``. Folder naming is approximate
(e.g. the post is ``LI - failure and success 04`` and the folder is
``monographic thread - failure and success 4``), so we fuzzy-match
folder basenames against the normalized post title.

Public API:

* ``locate_pdf(post_title, carousel_cfg) -> CarouselDoc``
    - Always resolves to the best-scoring folder; when that best match is
      below ``fuzzy_min_ratio`` it logs a warning naming the chosen folder
      and candidates rather than failing. Raises ``FileNotFoundError`` only
      on genuine hard failures: no candidate folders, or the matched folder
      contains no PDF.
* ``CarouselDoc`` holds the resolved ``pdf_path`` and the ``doc_title``
  (filename stem, truncated to ``doc_title_max_chars`` if needed).

Pure module — no I/O outside the local filesystem.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger("linkedin_carousel_pdf")

# Strip the "LI - " / "li - " prefix the user uses on carousel titles so
# the fuzzy comparison sees the topical part of the name.
_PREFIX_RE = re.compile(r"^\s*li\s*[-–—:]\s*", re.I)


@dataclass
class CarouselDoc:
    """Resolved PDF + the title LinkedIn should show on the document.

    ``doc_title`` is the source PDF's filename stem, truncated to
    ``doc_title_max_chars`` if longer; ``truncated`` lets the caller log
    a warning when truncation actually happened.
    """

    pdf_path: Path
    doc_title: str
    folder_match_ratio: float
    matched_folder: Path
    truncated: bool


def _normalize(text: str) -> str:
    """Lowercase, drop the 'LI - ' prefix, collapse non-alphanumerics."""
    s = _PREFIX_RE.sub("", text or "")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _candidate_folders(root: Path, subfolders: list[str]) -> list[Path]:
    """Yield every immediate subdirectory under each configured branch."""
    out: list[Path] = []
    for sub in subfolders:
        branch = root / sub
        if not branch.exists() or not branch.is_dir():
            logger.warning("⚠️ Carousel branch missing: %s", branch)
            continue
        for child in branch.iterdir():
            if child.is_dir():
                out.append(child)
    return out


def _pick_best_pdf(folder: Path, post_title: str) -> Path:
    """From the PDFs in `folder`, prefer the one whose stem best matches the post title."""
    pdfs = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        raise FileNotFoundError(f"No .pdf inside matched folder: {folder}")
    if len(pdfs) == 1:
        return pdfs[0]
    pdfs.sort(key=lambda p: _ratio(post_title, p.stem), reverse=True)
    logger.info(
        "📎 Folder %s has %d PDFs; picked %s by stem similarity.",
        folder.name, len(pdfs), pdfs[0].name,
    )
    return pdfs[0]


def locate_pdf(post_title: str, carousel_cfg: dict) -> CarouselDoc:
    """Find the carousel PDF for ``post_title`` per ``carousel_cfg``.

    ``carousel_cfg`` shape (from ``config.json`` ``linkedin.carousel``):
      - ``thread_root``: filesystem path (string) — the parent of the
        ``books``/``monographic`` branches.
      - ``subfolders``: list of branch names to scan, e.g.
        ``["books", "monographic"]``.
      - ``fuzzy_min_ratio``: float — confidence threshold; below it the best
        match is still used but logged as a warning (not rejected).
      - ``doc_title_max_chars``: int — LinkedIn truncates very long doc
        titles, so we pre-truncate with a warning.
    """
    if not post_title:
        raise ValueError("Carousel post has no title to match against.")

    root = Path(carousel_cfg["thread_root"])
    if not root.exists():
        raise FileNotFoundError(f"Carousel thread_root does not exist: {root}")

    subfolders = list(carousel_cfg.get("subfolders") or ["books", "monographic"])
    candidates = _candidate_folders(root, subfolders)
    if not candidates:
        raise FileNotFoundError(
            f"No candidate folders under {root} (subfolders={subfolders})"
        )

    scored = sorted(
        ((_ratio(post_title, c.name), c) for c in candidates),
        key=lambda x: x[0],
        reverse=True,
    )
    best_ratio, best_folder = scored[0]

    # Folder naming is approximate by design, so a sub-threshold best match is
    # usually still the right folder. Always take the best-scoring folder, but
    # when it falls below the confidence threshold log loudly which folder we
    # picked and why — so the run record shows what we did. Genuine hard
    # failures (no candidates / no PDF) still raise below.
    min_ratio = float(carousel_cfg.get("fuzzy_min_ratio", 0.6))
    if best_ratio < min_ratio:
        runners_up = ", ".join(f"{c.name} ({r:.2f})" for r, c in scored[:3])
        logger.warning(
            "⚠️ Carousel title %r has no folder match above the %.2f confidence "
            "threshold — taking best match %s (%.2f) anyway. Top candidates: %s",
            post_title, min_ratio, best_folder.name, best_ratio, runners_up,
        )

    pdf = _pick_best_pdf(best_folder, post_title)
    stem = pdf.stem
    max_chars = int(carousel_cfg.get("doc_title_max_chars", 50))
    truncated = len(stem) > max_chars
    doc_title = stem[:max_chars] if truncated else stem
    if truncated:
        logger.warning(
            "✂️ Document title %d>%d chars — truncated %r → %r",
            len(stem), max_chars, stem, doc_title,
        )

    logger.info(
        "📎 Carousel %r → folder %s (ratio %.2f), pdf %s, doc_title %r",
        post_title, best_folder.name, best_ratio, pdf.name, doc_title,
    )
    return CarouselDoc(
        pdf_path=pdf,
        doc_title=doc_title,
        folder_match_ratio=best_ratio,
        matched_folder=best_folder,
        truncated=truncated,
    )


__all__ = ["CarouselDoc", "locate_pdf"]
