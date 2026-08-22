"""Scoring: deterministic priors from ``criteria.json`` + two LLM stages via the hub.

* ``sender_weight`` / ``domain_bonus`` — owner overrides first (tier), then the
  data priors (hit-rate per email, domain picks); unknown senders get a neutral
  weight and the ``new`` flag.
* **Stage A** (``score_metadata``) — batched, cheap: sender + anchor/title +
  domain for every candidate → topic, fit 0–5, news/promo flags. Ranks the
  long tail without fetching anything.
* **Stage B** (``score_content``) — per candidate, after the page fetch: topic,
  relevance 0–5, star-worthiness, news/promo/paywall signals, one-line summary.

Both stages go through ``newsletter.llm.call`` (hub ``/v1/messages``), the
model alias comes from ``config.newsletter_triage.llm_model``; responses are
JSON and parsed defensively — a bad answer is an ``unknown`` state, never a 0.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from newsletter import llm

logger = logging.getLogger("newsletter_triage.score")

REPO_ROOT = Path(__file__).resolve().parents[2]
LLM_CACHE_PATH = REPO_ROOT / "results" / "newsletter" / "triage" / "llm_cache.json"


class LLMCache:
    """JSON cache for both stages, keyed by a hash of (model, prompt inputs).

    Makes re-runs and backtest iterations free for unchanged items; entries carry
    the model alias so a model switch invalidates naturally.
    """

    def __init__(self, path: Path = LLM_CACHE_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
        self.hits = self.misses = 0
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("⚠️ llm cache unreadable (%s) — starting empty", exc)

    @staticmethod
    def key(model: str, *parts: str) -> str:
        return hashlib.sha1(("\x1f".join([model, *parts])).encode("utf-8", "ignore")).hexdigest()

    def get(self, k: str) -> Any:
        v = self._data.get(k)
        if v is None:
            self.misses += 1
        else:
            self.hits += 1
        return v

    def put(self, k: str, v: Any) -> None:
        with self._lock:
            self._data[k] = v

    def flush(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

TOPICS = ("leadership and management", "personal development", "innovation")
TIER_WEIGHT = {"never": 0.0, "rarely": 0.35, "review": 1.0, "usually": 1.25, "always": 1.5}
NEW_SENDER_WEIGHT = 0.85
FLOOR_WEIGHT = 0.25
MULTI_PICK_SENDERS = ("readwise.io", "hbr.org")   # digests where >1 pick per email is normal


# ---------------------------------------------------------------------------
# priors


class Priors:
    def __init__(self, criteria: Dict[str, Any]) -> None:
        self.criteria = criteria
        self.overrides: Dict[str, Dict[str, Any]] = {k.lower(): v for k, v in criteria.get("sender_overrides", {}).items()}
        self.ranked: Dict[str, Dict[str, Any]] = {r["address"].lower(): r for r in criteria.get("sender_priors", {}).get("ranked", [])}
        self.floor: Dict[str, Dict[str, Any]] = {r["address"].lower(): r for r in criteria.get("sender_priors", {}).get("floor", [])}
        self.domains: Dict[str, Dict[str, Any]] = {d["domain"]: d for d in criteria.get("domain_priors", [])}
        self.max_domain_picks = max([d["picks"] for d in self.domains.values()] + [1])

    def sender(self, address: str) -> Tuple[float, str, bool]:
        """→ (weight, basis, is_new)."""
        a = (address or "").lower()
        ov = self.overrides.get(a)
        if ov:
            tier = ov.get("tier", "review")
            return TIER_WEIGHT.get(tier, 1.0), f"override:{tier}", False
        r = self.ranked.get(a)
        if r:
            hit = float(r.get("hit_rate_per_email", 0.0))
            return round(0.6 + min(1.0, hit) * 0.9, 3), f"hit {hit:.2f}/email", False
        if a in self.floor:
            return FLOOR_WEIGHT, "floor (0 picks in 54 weeks)", False
        return NEW_SENDER_WEIGHT, "new sender", True

    def sender_topics(self, address: str) -> Dict[str, int]:
        r = self.ranked.get((address or "").lower())
        return dict(r.get("topics", {})) if r else {}

    def domain(self, dom: str) -> Tuple[float, int]:
        d = self.domains.get(dom)
        if not d:
            return 0.0, 0
        return round(0.3 * min(1.0, d["picks"] / max(10, self.max_domain_picks * 0.2)), 3), int(d["picks"])

    def topic_prior(self, address: str, dom: str) -> Optional[str]:
        """Most frequent topic of this sender, else of the domain — a weak prior for rule-only mode."""
        for mix in (self.sender_topics(address), (self.domains.get(dom) or {}).get("topics", {})):
            mix = {k: v for k, v in mix.items() if k in TOPICS}
            if mix:
                return max(mix, key=mix.get)
        return None

    def multi_pick(self, address: str) -> bool:
        return any(h in (address or "") for h in MULTI_PICK_SENDERS)


# ---------------------------------------------------------------------------
# prompts

LESSONS_PATH = Path(__file__).with_name("lessons.json")


def load_lessons(path: Optional[Path] = None) -> List[str]:
    """Owner-accepted criteria notes distilled from reviews (local gitignored file, text-only; exported from the
    store by ``lessons.py`` — absent on a fresh clone until the first accept/save)."""
    path = path or LESSONS_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [str(x.get("text") if isinstance(x, dict) else x).strip() for x in data.get("lessons", []) if x]


def _criteria_brief(criteria: Dict[str, Any]) -> str:
    rules = criteria.get("rules", {})
    parts = ["Newsletter selection criteria (owner-validated, measured on 54 weeks):"]
    for t in TOPICS:
        tr = rules.get("topics", {}).get(t, {})
        parts.append(f"- {t}: themes = {', '.join(tr.get('themes', []))}. Avoid: {', '.join(tr.get('anti_themes', []))}.")
    np_ = rules.get("news_policy", {})
    parts.append(f"- News policy: {np_.get('rule', '')} Keep: {'; '.join(np_.get('keep_if', []))}. Drop: {'; '.join(np_.get('drop_if', []))}.")
    parts.append(f"- Never: {'; '.join(rules.get('content_exclusions', {}).get('anchors', []))}; paywalled content.")
    parts.append(f"- Style: {rules.get('title_style', {}).get('note', '')}")
    learned = load_lessons()
    if learned:
        parts.append("Learned from the owner's reviews (apply as soft preferences):")
        parts.extend(f"- {x}" for x in learned)
    return "\n".join(parts)


_JSON_BLOCK = re.compile(r"\[.*\]|\{.*\}", re.DOTALL)


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _clamp(v: Any, lo: int = 0, hi: int = 5) -> Optional[int]:
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


def _topic(v: Any) -> Optional[str]:
    s = (str(v) if v is not None else "").strip().lower()
    for t in TOPICS:
        if s == t or s.startswith(t[:10]):
            return t
    aliases = {"leadership": TOPICS[0], "management": TOPICS[0], "personal": TOPICS[1], "innovation": TOPICS[2], "ai": TOPICS[2]}
    for k, t in aliases.items():
        if k in s:
            return t
    return None


# ---------------------------------------------------------------------------
# stage A — metadata, batched


@dataclass
class MetaScore:
    topic: Optional[str] = None
    fit: Optional[int] = None          # 0–5
    news: bool = False
    promo: bool = False
    reason: str = ""
    ok: bool = False


def _meta_key(model: str, it: Dict[str, str]) -> str:
    return LLMCache.key(model, "A", it.get("sender", ""), it.get("label", ""), it.get("domain", ""), it.get("path", ""))


def score_metadata(items: Sequence[Dict[str, str]], criteria: Dict[str, Any], *, base_url: str, model: str,
                   batch: int = 25, timeout: int = 180, workers: int = 4,
                   cache: Optional["LLMCache"] = None) -> List[MetaScore]:
    """``items``: dicts with sender, label, domain, path. Returns one MetaScore per item (same order).

    Batches run concurrently (``workers``) — the hub's per-call overhead (~8 s
    CLI start for the Claude aliases) dominates, not generation. Cached items
    are not re-sent.
    """
    brief = _criteria_brief(criteria)
    out: List[MetaScore] = [MetaScore() for _ in items]
    todo_idx: List[int] = []
    for i, it in enumerate(items):
        hit = cache.get(_meta_key(model, it)) if cache is not None else None
        if hit:
            out[i] = MetaScore(**hit)
        else:
            todo_idx.append(i)
    if cache is not None:
        logger.info("  stage A cache: %d hits, %d to score", len(items) - len(todo_idx), len(todo_idx))
    starts = list(range(0, len(todo_idx), batch))
    t0 = time.monotonic()
    done = 0

    def one(start: int) -> None:
        idxs = todo_idx[start:start + batch]
        chunk = [items[i] for i in idxs]
        lines = [f"{i + 1}. sender=\"{it['sender'][:40]}\" | anchor=\"{it['label'][:120]}\" | domain={it['domain']} | path={it['path'][:80]}"
                 for i, it in enumerate(chunk)]
        prompt = (f"{brief}\n\nYou triage links from newsletter emails for a weekly curated newsletter with three sections. "
                  f"For EACH numbered link below, judge from the metadata only (no fetching): the best-fitting topic, how well it fits "
                  f"the criteria (fit 0–5: 0 = promo/noise/irrelevant, 3 = plausible, 5 = textbook pick), whether it is a news/"
                  f"release announcement, whether it is a promo (course, book, product, event, sponsor). Be strict: most links are 0–2.\n"
                  f"Return ONLY a JSON array with one object per link, in order: "
                  f"{{\"i\": <number>, \"topic\": \"leadership and management|personal development|innovation\", \"fit\": 0-5, "
                  f"\"news\": true|false, \"promo\": true|false, \"reason\": \"<= 12 words\"}}\n\n" + "\n".join(lines))
        try:
            text = llm.call(base_url=base_url, model=model, prompt=prompt, max_tokens=70 * len(chunk) + 150, timeout=timeout)
            data = _parse_json(text)
        except Exception as exc:  # noqa: BLE001 — logged, stays unknown
            logger.warning("⚠️ stage-A batch %d failed: %s", start // batch, type(exc).__name__)
            data = None
        if not isinstance(data, list):
            return
        for obj in data:
            if not isinstance(obj, dict):
                continue
            idx = _clamp(obj.get("i"), 1, len(chunk))
            if idx is None:
                continue
            gi = idxs[idx - 1]
            ms = out[gi]
            ms.topic, ms.fit = _topic(obj.get("topic")), _clamp(obj.get("fit"))
            ms.news, ms.promo = bool(obj.get("news")), bool(obj.get("promo"))
            ms.reason, ms.ok = str(obj.get("reason") or "")[:160], ms.fit is not None
            if cache is not None and ms.ok:
                cache.put(_meta_key(model, items[gi]), ms.__dict__)

    if starts:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for _ in pool.map(one, starts):
                done += 1
                if done % 5 == 0 or done == len(starts):
                    logger.info("  … stage A %d/%d batches (%.0fs)", done, len(starts), time.monotonic() - t0)
    if cache is not None:
        cache.flush()
    return out


# ---------------------------------------------------------------------------
# stage B — content, per candidate


@dataclass
class ContentScore:
    topic: Optional[str] = None
    relevance: Optional[int] = None    # 0–5
    star: Optional[int] = None         # 0–5
    news: bool = False
    promo: bool = False
    paywall: bool = False
    summary: str = ""
    reason: str = ""
    ok: bool = False


def score_content(*, title: str, author: str, sender: str, domain: str, excerpt: str, criteria: Dict[str, Any],
                  base_url: str, model: str, timeout: int = 120, cache: Optional["LLMCache"] = None) -> ContentScore:
    ck = LLMCache.key(model, "B", title, sender, domain, excerpt[:2500]) if cache is not None else ""
    if cache is not None:
        hit = cache.get(ck)
        if hit:
            return ContentScore(**hit)
    brief = _criteria_brief(criteria)
    prompt = (f"{brief}\n\nJudge this article for the newsletter. Title: {title[:200]}\nAuthor: {author or '?'}\nSent by: {sender[:60]}\n"
              f"Domain: {domain}\nText (excerpt):\n\"\"\"\n{excerpt[:2500]}\n\"\"\"\n\n"
              f"Return ONLY JSON: {{\"topic\": \"leadership and management|personal development|innovation\", \"relevance\": 0-5, "
              f"\"star\": 0-5 (would this be the single best of its section?), \"news\": true|false (release/announcement), "
              f"\"promo\": true|false, \"paywall\": true|false (text is cut off behind a subscription wall), "
              f"\"summary\": \"one line, <= 25 words\", \"reason\": \"<= 15 words why it fits or not\"}}")
    cs = ContentScore()
    try:
        text = llm.call(base_url=base_url, model=model, prompt=prompt, max_tokens=300, timeout=timeout)
        data = _parse_json(text)
    except Exception as exc:  # noqa: BLE001
        cs.reason = f"llm-error:{type(exc).__name__}"
        return cs
    if not isinstance(data, dict):
        cs.reason = "llm-unparseable"
        return cs
    cs.topic, cs.relevance, cs.star = _topic(data.get("topic")), _clamp(data.get("relevance")), _clamp(data.get("star"))
    cs.news, cs.promo, cs.paywall = bool(data.get("news")), bool(data.get("promo")), bool(data.get("paywall"))
    cs.summary, cs.reason = str(data.get("summary") or "")[:240], str(data.get("reason") or "")[:160]
    cs.ok = cs.relevance is not None
    if cache is not None and cs.ok:
        cache.put(ck, cs.__dict__)
    return cs


# ---------------------------------------------------------------------------
# combine


def combine(*, sender_weight: float, domain_bonus: float, meta: Optional[MetaScore], content: Optional[ContentScore]) -> Optional[float]:
    """Final 0–10-ish score; None when no LLM signal exists (unknown, not zero)."""
    base: Optional[float] = None
    if content is not None and content.ok and content.relevance is not None:
        base = content.relevance / 5.0
        if content.star:
            base += 0.04 * content.star
    elif meta is not None and meta.ok and meta.fit is not None:
        base = meta.fit / 5.0 * 0.85          # metadata-only is less certain
    if base is None:
        return None
    score = base * sender_weight * (1.0 + domain_bonus) * 10.0
    news = (content.news if content and content.ok else False) or (meta.news if meta and meta.ok else False)
    if news:
        score *= 0.6
    return round(score, 2)
