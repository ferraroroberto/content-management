"""Candidates → one edition: vetoes, caps, 8 per topic, star + must-read suggestions.

Pure functions over ``Candidate`` records so the rules are unit-testable
without Gmail, the hub or the network. The numbers come from
``criteria.json → rules`` (see ``docs/newsletter-triage-criteria.md``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from newsletter.triage.score import ContentScore, MetaScore

TOPICS = ("leadership and management", "personal development", "innovation")
MIN_SCORE = 3.0          # below this a link is a "candidate", never auto-selected
WEAK_FILL = 7.0          # selected but below this = "weak fill": listed, unticked, flagged
ORG_AUTHORS = {"harvardbiz", "harvard business review", "mckinsey", "(not classified)", ""}
# Multi-author publishers: when the byline is missing, two articles are NOT the same
# author — the domain cap governs them, not the author cap.
# Digest-style senders (several distinct articles per email): up to 3 picks per email.
DIGEST_SENDER_HINTS = ("readwise", "hbr.org", "insead", "hbs.edu", "mckinsey", "strategy-business", "imd.org",
                       "gallup", "thinkers50", "techcrunch", "joinsuperhuman", "quanta", "firstround", "stanford",
                       "bcg.com", "simonsfoundation")
MULTI_AUTHOR_DOMAINS = {"hbr.org", "mckinsey.com", "knowledge.insead.edu", "library.hbs.edu", "strategy-business.com",
                        "imd.org", "bcg.com", "sloanreview.mit.edu", "inc.com", "fastcompany.com", "forbes.com",
                        "theatlantic.com", "wired.com", "youtube.com", "x.com", "techcrunch.com", "readwise.io"}


@dataclass
class Candidate:
    cid: str
    message_id: str
    sender_name: str
    sender_address: str
    subject: str
    email_ts: str
    label: str
    url: str
    canonical: str
    domain: str
    title: str = ""
    author: Optional[str] = None
    kind: str = "article"
    fetched_ok: Optional[bool] = None
    paywalled: Optional[bool] = None
    in_notion: bool = False
    sender_weight: float = 1.0
    sender_basis: str = ""
    is_new_sender: bool = False
    domain_bonus: float = 0.0
    meta: Optional[MetaScore] = None
    content: Optional[ContentScore] = None
    score: Optional[float] = None
    topic: Optional[str] = None
    verdict: str = "pending"   # selected | runner-up | candidate | vetoed | unknown | duplicate | low
    reason: str = ""
    summary: str = ""

    @property
    def display_title(self) -> str:
        return (self.title or self.label or self.url)[:160]

    @property
    def author_key(self) -> str:
        a = (self.author or "").strip().lower()
        if a and a not in ORG_AUTHORS:
            return a
        if self.domain in MULTI_AUTHOR_DOMAINS:
            return f"unknown:{self.cid}"
        return f"sender:{self.sender_address}"


@dataclass
class Selection:
    picks: Dict[str, List[Candidate]] = field(default_factory=lambda: {t: [] for t in TOPICS})
    runners: Dict[str, List[Candidate]] = field(default_factory=lambda: {t: [] for t in TOPICS})
    stars: Dict[str, Optional[Candidate]] = field(default_factory=lambda: {t: None for t in TOPICS})
    must_read: Optional[Candidate] = None
    short: Dict[str, int] = field(default_factory=dict)   # topic → missing count

    def all_picks(self) -> List[Candidate]:
        return [c for t in TOPICS for c in self.picks[t]]


def resolve_topic(c: Candidate) -> Optional[str]:
    if c.content is not None and c.content.ok and c.content.topic:
        return c.content.topic
    if c.meta is not None and c.meta.ok and c.meta.topic:
        return c.meta.topic
    return None


def apply_vetoes(c: Candidate) -> None:
    """Set verdict for anything that can never be selected; leave others ``pending``."""
    if c.in_notion:
        c.verdict, c.reason = "duplicate", "already in Notion"
    elif c.sender_weight <= 0.0:
        c.verdict, c.reason = "vetoed", f"sender {c.sender_basis}"
    elif c.paywalled is True:
        c.verdict, c.reason = "vetoed", "paywalled"   # fetch-level hard wall only — the LLM's
        # "text looks cut off" flag over-fires on metered sites (HBR, INSEAD) and is advisory
    elif (c.content is not None and c.content.ok and c.content.promo) or \
            (c.content is None and c.meta is not None and c.meta.ok and c.meta.promo):
        c.verdict, c.reason = "vetoed", "promo"
    elif c.score is None:
        c.verdict = "unknown"
        c.reason = c.reason or ("not scored" if c.fetched_ok is not False else "fetch failed")
    elif c.score < MIN_SCORE:
        c.verdict = "low"
        c.reason = (c.content.reason if c.content and c.content.ok else (c.meta.reason if c.meta and c.meta.ok else "")) or "below threshold"


def select(cands: List[Candidate], rules: Dict[str, Any], *, per_topic: int = 8, runners_n: int = 6,
           multi_pick_addrs: Optional[set] = None) -> Selection:
    caps = rules.get("caps", {})
    hbr_cap = int(caps.get("hbr_per_edition", {}).get("target", 3))
    author_cap = int(caps.get("same_author_per_edition", {}).get("target", 2))
    domain_cap = int(caps.get("same_domain_per_edition", {}).get("target", 3))
    multi = multi_pick_addrs or set()

    for c in cands:
        if c.verdict == "pending":
            apply_vetoes(c)
        c.topic = resolve_topic(c) or c.topic     # LLM topic first, sender/domain prior as fallback

    sel = Selection()
    eligible = [c for c in cands if c.verdict == "pending" and c.topic in TOPICS and c.score is not None]
    eligible.sort(key=lambda c: (-(c.score or 0), c.email_ts))

    hbr_n = 0
    author_n: Counter = Counter()
    domain_n: Counter = Counter()
    email_n: Counter = Counter()
    seen_canon: set = set()
    for c in eligible:
        t = c.topic
        if c.canonical in seen_canon:
            c.verdict, c.reason = "duplicate", "same article already picked"
            continue
        if len(sel.picks[t]) >= per_topic:
            continue
        if c.domain == "hbr.org" and hbr_n >= hbr_cap:
            c.reason = f"HBR cap {hbr_cap}"
            continue
        if author_n[c.author_key] >= author_cap:
            c.reason = f"author cap {author_cap}"
            continue
        if domain_n[c.domain] >= domain_cap:
            c.reason = f"domain cap {domain_cap}"
            continue
        per_email_cap = 3 if c.sender_address in multi or any(h in c.sender_address for h in DIGEST_SENDER_HINTS) else 1
        if email_n[c.message_id] >= per_email_cap:
            c.reason = "one pick per email"
            continue
        sel.picks[t].append(c)
        c.verdict = "selected"
        seen_canon.add(c.canonical)
        if c.domain == "hbr.org":
            hbr_n += 1
        author_n[c.author_key] += 1
        domain_n[c.domain] += 1
        email_n[c.message_id] += 1

    for c in eligible:
        if c.verdict != "pending":
            continue
        t = c.topic
        if c.canonical in seen_canon:
            c.verdict, c.reason = "duplicate", "same article already listed"
            continue
        seen_canon.add(c.canonical)
        if len(sel.runners[t]) < runners_n:
            sel.runners[t].append(c)
            c.verdict = "runner-up"
            c.reason = c.reason or "next in line"
        else:
            c.verdict = "candidate"
            c.reason = c.reason or "scored, below the fold"

    # star per topic: best content.star, tie → score; must-read by topic prior × score
    prior = rules.get("edition", {}).get("must_read_topic_prior", {})
    best_mr, best_val = None, -1.0
    for t in TOPICS:
        picks = sel.picks[t]
        if picks:
            star = max(picks, key=lambda c: ((c.content.star or 0) if c.content and c.content.ok else 0, c.score or 0))
            sel.stars[t] = star
            val = (star.score or 0) * float(prior.get(t, 0.1))
            if val > best_val:
                best_mr, best_val = star, val
        missing = per_topic - len(picks)
        if missing > 0:
            sel.short[t] = missing
    sel.must_read = best_mr
    return sel
