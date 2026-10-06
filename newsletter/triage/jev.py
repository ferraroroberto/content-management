"""Stage A through TypeSafe Jev — a typed decision model, reached only via the hub (#285).

Jev does not generate text: one request carries a ``state`` plus typed questions
(Choice / Score / Noul) and comes back with an answer, per-option probabilities
and a confidence for each. Stage A maps onto it one link per request:

* ``state``  — the link metadata only: sender name, anchor text, domain, path.
  No email body, no address.
* ``topic``  — Choice over the three ``TOPICS``, each option described by its
  themes from ``criteria.json``.
* ``fit``    — Score over six levels carrying the legacy 0–5 rubric wording, with
  the selection criteria (themes, anti-themes, exclusions, accepted lessons) in
  the question's own ``instructions`` object.
* ``news`` / ``promo`` — Nouls, each with only the slice of the brief it needs.

The brief is trimmed per question rather than sent whole in ``state`` because the
vendor's documented limits (jev-1.13 "jaggedness") say accuracy falls as state
fills with content unrelated to the decision, and that it reads questions
literally. One link per request instead of a batch of 25 also removes the
"which item is ``links[i]``" indirection the same page warns about; at ~0.4 s
per call and 1,200 requests/min the batch saving is not needed.

Fit is compared by band (the Score's expectation rounded to a level) — the docs
say Score output is not numerically calibrated between levels. Nouls carry no
vendor confidence; ``conf`` for them is ``|2p − 1|`` (0 at p = 0.5, 1 at 0 or 1).

Every call goes to ``<hub>/v1/systemone``; the hub holds the API key and records
latency + tokens in its observability ring. A failed call leaves the item
``ok=False`` with ``error`` set — the caller records it as ``not evaluated``,
never as a score of 0.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from newsletter.topics import TOPICS
from newsletter.triage.score import MetaScore, load_lessons

logger = logging.getLogger("newsletter_triage.jev")

ENGINES = ("legacy", "jev", "both")
DEFAULT_ENGINE = "legacy"
DEFAULT_MODEL = "jev-latest"
ROUTE = "/v1/systemone"
NOT_EVALUATED = "not evaluated"
DEFAULT_WORKERS = 6
DEFAULT_TIMEOUT_S = 30
DEFAULT_BUDGET_S = 300            # `both`: the most a shadow pass may add to a run
MAX_RETRY_AFTER_S = 10.0          # one 429 retry, never a long sleep
YES = 0.5                         # Noul threshold for the boolean news / promo flags

TOPIC_KEYS: Dict[str, str] = {"leadership": TOPICS[0], "personal_development": TOPICS[1], "innovation": TOPICS[2]}
FIT_LEVELS = ("promo, noise or irrelevant", "off-topic or very weak", "weak fit", "plausible", "strong fit",
              "textbook pick")


@dataclass
class JevMetaScore(MetaScore):
    """A ``MetaScore`` plus what Jev returns beyond it. ``reason`` stays empty: Jev produces no text."""
    fit_score: Optional[float] = None     # Score expectation 0–5 (``fit`` is its band)
    news_p: Optional[float] = None        # Noul probability of "yes"
    promo_p: Optional[float] = None
    conf: Dict[str, float] = field(default_factory=dict)            # topic / fit / news / promo
    probs: Dict[str, Dict[str, float]] = field(default_factory=dict)  # topic + fit distributions
    served_model: str = ""
    error: str = ""                       # set when the item is not evaluated


# ---------------------------------------------------------------------------
# questions


def build_questions(criteria: Dict[str, Any], lessons: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """The four questions — identical for every link of a run, so built once."""
    rules = criteria.get("rules", {})
    topics = rules.get("topics", {})
    lessons = list(load_lessons() if lessons is None else lessons)
    news_policy = rules.get("news_policy", {})
    exclusions = rules.get("content_exclusions", {}).get("anchors", [])

    topic_criteria = {key: f"Themes: {', '.join(topics.get(t, {}).get('themes', []))}." for key, t in TOPIC_KEYS.items()}
    selection = {t: {"themes": topics.get(t, {}).get("themes", []), "avoid": topics.get(t, {}).get("anti_themes", [])}
                 for t in TOPICS}
    fit_instructions: Dict[str, Any] = {
        "newsletter": "A weekly curated newsletter with three sections; each section prints 8 articles a week.",
        "selection_criteria": selection,
        "never_select": list(exclusions) + ["paywalled content"],
        "question": ("How well does the article behind `link` fit `selection_criteria` for this newsletter? Judge from "
                     "the link metadata only. Most links in a newsletter email are not a fit."),
    }
    if lessons:
        fit_instructions["owner_preferences"] = lessons
    return {
        "topic": {"type": "choice", "criteria": topic_criteria,
                  "instructions": "Which newsletter section does the article behind `link` belong to?"},
        "fit": {"type": "score", "criteria": list(FIT_LEVELS), "instructions": fit_instructions},
        "news": {"type": "noul",
                 "instructions": {"news_policy": {"drop": news_policy.get("drop_if", [])},
                                  "question": ("Is `link` a news item or a release announcement, such as a product or "
                                               "model launch, a funding round, earnings or an executive move?")},
                 "criteria": {"true": "a news item or release announcement",
                              "false": "an essay, analysis, guide, research piece or interview"}},
        "promo": {"type": "noul",
                  "instructions": {"promotions_include": list(exclusions),
                                   "question": ("Is `link` a promotion, such as a course, book, product, event, "
                                                "webinar, sponsor or sign-up offer?")},
                  "criteria": {"true": "a promotion or offer", "false": "an article to read"}},
    }


def build_state(item: Dict[str, str]) -> Dict[str, Any]:
    """Link metadata only — the same four fields legacy Stage A sends, nothing else leaves the machine."""
    return {"link": {"sender": (item.get("sender") or "")[:40], "anchor_text": (item.get("label") or "")[:120],
                     "domain": item.get("domain") or "", "path": (item.get("path") or "")[:80]}}


# ---------------------------------------------------------------------------
# answers


def _f(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_answers(doc: Any) -> JevMetaScore:
    """Vendor answer → ``JevMetaScore``. A missing or malformed answer is ``not evaluated``, never a guess."""
    ms = JevMetaScore()
    answers = doc.get("answers") if isinstance(doc, dict) else None
    if not isinstance(answers, dict):
        ms.error = "no answers in response"
        return ms
    ms.served_model = str(doc.get("model") or "")
    topic, fit = answers.get("topic") or {}, answers.get("fit") or {}
    news, promo = answers.get("news") or {}, answers.get("promo") or {}
    ms.topic = TOPIC_KEYS.get(str(topic.get("choice") or ""))
    ms.fit_score = _f(fit.get("score"))
    if ms.fit_score is not None:
        ms.fit = max(0, min(len(FIT_LEVELS) - 1, int(round(ms.fit_score))))
    ms.news_p, ms.promo_p = _f(news.get("noul")), _f(promo.get("noul"))
    if ms.topic is None or ms.fit is None or ms.news_p is None or ms.promo_p is None:
        ms.error = "incomplete answers"
        return ms
    ms.news, ms.promo = ms.news_p > YES, ms.promo_p > YES
    ms.conf = {"topic": _f(topic.get("confidence")) or 0.0, "fit": _f(fit.get("confidence")) or 0.0,
               "news": round(abs(2 * ms.news_p - 1), 4), "promo": round(abs(2 * ms.promo_p - 1), 4)}
    ms.probs = {"topic": {TOPIC_KEYS.get(k, k): v for k, v in (topic.get("probabilities") or {}).items()},
                "fit": dict(fit.get("probabilities") or {})}
    ms.ok = True
    return ms


# ---------------------------------------------------------------------------
# transport


def evaluate(item: Dict[str, str], questions: Dict[str, Any], *, base_url: str, model: str = DEFAULT_MODEL,
             timeout: float = DEFAULT_TIMEOUT_S, session: Optional[requests.Session] = None) -> JevMetaScore:
    """One link → one hub ``/v1/systemone`` call. Never raises: a failure comes back as ``ok=False`` + ``error``."""
    url = base_url.rstrip("/") + ROUTE
    body = {"model": model, "state": build_state(item), "questions": questions}
    post = (session or requests).post
    for attempt in (1, 2):
        try:
            resp = post(url, json=body, timeout=timeout)
        except requests.RequestException as exc:
            return JevMetaScore(error=f"hub unreachable ({type(exc).__name__})")
        if resp.status_code == 429 and attempt == 1:
            wait = min(MAX_RETRY_AFTER_S, _f(resp.headers.get("Retry-After")) or 2.0)
            time.sleep(max(0.0, wait))
            continue
        if resp.status_code >= 400:
            kind = ""
            try:
                kind = str((resp.json() or {}).get("hub_error") or "")
            except ValueError:
                pass
            return JevMetaScore(error=f"http {resp.status_code}" + (f" {kind}" if kind else ""))
        try:
            return parse_answers(resp.json())
        except ValueError:
            return JevMetaScore(error="unparseable response")
    return JevMetaScore(error="http 429")


def score_metadata_jev(items: Sequence[Dict[str, str]], criteria: Dict[str, Any], *, base_url: str,
                       model: str = DEFAULT_MODEL, workers: int = DEFAULT_WORKERS, timeout: float = DEFAULT_TIMEOUT_S,
                       stop: Optional[threading.Event] = None, results: Optional[List[JevMetaScore]] = None,
                       ) -> List[JevMetaScore]:
    """Same input as ``score.score_metadata``; one ``JevMetaScore`` per item, same order.

    ``stop`` ends the pass early: items not yet started stay ``not evaluated``.
    ``results`` lets a caller read finished items while the pass is still running.
    """
    questions = build_questions(criteria)
    out = results if results is not None else [JevMetaScore(error=NOT_EVALUATED) for _ in items]
    t0 = time.monotonic()
    done = [0]
    lock = threading.Lock()
    session = requests.Session()

    def one(i: int) -> None:
        if stop is not None and stop.is_set():
            return
        out[i] = evaluate(items[i], questions, base_url=base_url, model=model, timeout=timeout, session=session)
        with lock:
            done[0] += 1
            if done[0] % 200 == 0 or done[0] == len(items):
                logger.info("  … jev stage A %d/%d links (%.0fs)", done[0], len(items), time.monotonic() - t0)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, range(len(items))))
    finally:
        session.close()
    failed = sum(1 for m in out if not m.ok)
    if failed:
        logger.warning("⚠️ jev stage A: %d/%d links not evaluated", failed, len(items))
    return out


class Shadow:
    """``both`` mode: Jev scores the same Stage A items on a background thread while legacy decides.

    ``collect()`` waits at most until ``budget_s`` after start, then stops the pass and returns what
    finished — everything else is ``not evaluated``. Nothing here can raise into the legacy run.
    """

    def __init__(self, items: Sequence[Dict[str, str]], criteria: Dict[str, Any], *, base_url: str,
                 model: str = DEFAULT_MODEL, workers: int = DEFAULT_WORKERS, timeout: float = DEFAULT_TIMEOUT_S,
                 budget_s: float = DEFAULT_BUDGET_S) -> None:
        self.n = len(items)
        self.model = model
        self.budget_s = budget_s
        self.results: List[JevMetaScore] = [JevMetaScore(error=NOT_EVALUATED) for _ in items]
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="jev-shadow", daemon=True,
                                        args=(items, criteria, base_url, workers, timeout))
        self._thread.start()

    def _run(self, items: Sequence[Dict[str, str]], criteria: Dict[str, Any], base_url: str, workers: int,
             timeout: float) -> None:
        try:
            score_metadata_jev(items, criteria, base_url=base_url, model=self.model, workers=workers, timeout=timeout,
                               stop=self._stop, results=self.results)
        except Exception as exc:  # noqa: BLE001 — the shadow must never surface into the legacy run
            logger.warning("⚠️ jev shadow pass failed: %s", type(exc).__name__)

    def collect(self) -> List[JevMetaScore]:
        remaining = self.budget_s - (time.monotonic() - self._t0)
        self._thread.join(timeout=max(0.0, remaining))
        if self._thread.is_alive():
            self._stop.set()
            logger.warning("⚠️ jev shadow over its %.0fs budget — unfinished links recorded as not evaluated",
                           self.budget_s)
        snapshot = list(self.results)
        return [m if m.ok else JevMetaScore(error=m.error or NOT_EVALUATED) for m in snapshot]


def shadow_rows(cids: Sequence[str], scores: Sequence[JevMetaScore], *, model: str) -> List[Dict[str, Any]]:
    """Shadow rows — one per Stage A item, keyed by candidate ``cid``, ``status`` ``ok`` or ``not evaluated``."""
    rows: List[Dict[str, Any]] = []
    for cid, m in zip(cids, scores):
        rows.append({"cid": cid, "model": m.served_model or model, "status": "ok" if m.ok else NOT_EVALUATED,
                     "error": None if m.ok else (m.error or NOT_EVALUATED),
                     "topic": m.topic, "fit": m.fit, "fit_score": m.fit_score, "news": m.news if m.ok else None,
                     "promo": m.promo if m.ok else None, "news_p": m.news_p, "promo_p": m.promo_p,
                     "topic_conf": m.conf.get("topic"), "fit_conf": m.conf.get("fit"),
                     "probs": m.probs or None})
    return rows


# ---------------------------------------------------------------------------
# shadow file — beside the run's report, so a `both` run never writes Jev into the store's legacy fields


def shadow_path(report: Path) -> Path:
    """``triage-<start>_<end>.md`` → ``triage-<start>_<end>.jev-shadow.json`` (same folder)."""
    return report.with_suffix(".jev-shadow.json")


def write_shadow(path: Path, rows: Sequence[Dict[str, Any]], *, window: Tuple[str, str]) -> None:
    data = {"window": list(window), "written_at": datetime.now(timezone.utc).isoformat(),
            "evaluated": sum(1 for r in rows if r["status"] == "ok"), "rows": list(rows)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_shadow(path: Path) -> Optional[Dict[str, Any]]:
    """The shadow file of a run, or None when the run had no ``both`` pass (or the file is unreadable)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ jev shadow unreadable (%s): %s", path.name, exc)
        return None
