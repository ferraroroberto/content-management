"""Build ``newsletter/triage/criteria.json`` — the machine-readable selection criteria.

Two halves, merged:

* **RULES** (hand-written below, versioned with the code) — the caps, topic
  signatures, exclusions, news policy, timing and the owner-stated rules. Each
  carries the number measured on the 54-week history so the rationale travels
  with the rule (see ``docs/newsletter-triage-criteria.md``).
* **priors** (derived at build time from ``results/newsletter/triage/history/
  stats.json``) — per-sender and per-domain hit-rates, topic mix and star rates.

Usage::

    python -m newsletter.triage.criteria            # rebuild criteria.json
    python -m newsletter.triage.criteria --print    # show a summary

The Step-2 ranker reads ``criteria.json`` only; it never reads ``stats.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402

STATS_PATH = REPO_ROOT / "results" / "newsletter" / "triage" / "history" / "stats.json"
CRITERIA_PATH = Path(__file__).with_name("criteria.json")

TOPICS = ("leadership and management", "personal development", "innovation")

# ---------------------------------------------------------------------------
# hand-written rules (numbers = measured on N174–N227, 2025-08 → 2026-08)

RULES: Dict[str, Any] = {
    "edition": {
        "per_topic": 8,                 # 8/8/8 in 51 of 54 complete editions (24 articles)
        "stars_per_topic": 1,           # exactly 3 stars in 51/54 editions
        "must_read": 1,                 # first sentence of the edition title
        "must_read_topic_prior": {      # 48 recoverable must-reads
            "personal development": 0.60, "leadership and management": 0.35, "innovation": 0.04},
        "fill_ahead_editions": 2,       # review fills the first future edition with <8 per topic; N+1/N+2 already partly filled
    },
    "caps": {
        # per edition — target = the mode, hard = tolerated exception, never = reject
        "hbr_per_edition": {"target": 3, "hard": 4, "never_above": 5,
                            "measured_hist": "3:26 · 2:10 · 4:12 · 1:6 · 0:1 · 5:1 (56 editions)"},
        "same_author_per_edition": {"target": 2, "hard": 3,
                                    "measured_hist": "2:32 · 3:14 · 1:5 · 4:3 · 5:2 (org-level authors such as 'harvardbiz'/'McKinsey' inflate the tail)"},
        "same_domain_per_edition": {"target": 3, "hard": 4,
                                    "measured_hist": "3:30 · 4:13 · 2:11 · 5:1 · 1:1"},
        "same_sender_per_email": {"typical": 1, "note": "one email almost never yields more than 1–2 picks; Readwise/HBR digests are the exception"},
    },
    "timing": {
        "email_to_edition_lag_days_mode": [10, 17, 20],
        "review_window_days": [7, 21],
        "note": "emails are reviewed ~1 week before an edition and land in the first future edition with a free slot; picks dated >21 days before the edition are rare",
    },
    "news_policy": {
        "rule": "a product/model *release* is not an innovation pick; the pick is the commentary that explains what it changes for work, organisations or society",
        "measured": "news-like titles are 3.5% of innovation picks vs 2.3% of all offered links — no over-weighting; the few release-shaped picks are essays (Mollick 'Sign of the future: GPT-5.5', Thompson 'Gemini! At the disco') or one primary source per event (Google's Gemini 3 post, Anthropic research posts)",
        "keep_if": ["new capability class (agents, world models, coding agents) explained for a general business reader",
                    "credible survey / report on AI adoption, jobs, or the economy (McKinsey, Stanford HAI, Gallup, HBS)",
                    "first-hand essay from a practitioner/thinker (Mollick, Kozyrkov, Thompson, Woodbury, Dwarkesh interviews)"],
        "drop_if": ["funding rounds, valuations, earnings, executive moves", "benchmark leaderboards, model cards, release notes",
                    "vendor marketing, 'try it now' product pages", "daily AI-news roundups as a whole (pick at most the one general-interest item)"],
    },
    "topics": {
        "leadership and management": {
            "themes": ["psychological safety", "culture and change", "feedback and difficult conversations", "trust",
                       "meetings and decision-making", "managers and teams", "emotions at work", "power, politics and influence",
                       "delegation and accountability", "humor and humanity at work", "hiring, performance and careers of others"],
            "signature_domains": ["hbr.org", "think.fearlessculture.design", "psychsafety.com", "mikefisher.substack.com",
                                  "rishad.substack.com", "knowledge.insead.edu", "newsletter.weskao.com", "imd.org", "library.hbs.edu",
                                  "strategy-business.com", "gallup.com", "corporate-rebels.com", "leadingsapiens.com"],
            "anti_themes": ["executive-education brochures", "program/course promos", "alumni news"],
        },
        "personal development": {
            "themes": ["attention, focus and busyness", "habits and motivation", "learning and reading", "meaning, optimism and happiness",
                       "mental models and decision-making for oneself", "career and identity", "energy, sleep, health (when first-hand and practical)",
                       "writing and thinking clearly", "psychology of self (paradoxes, effects, traps)"],
            "signature_domains": ["sahilbloom.com", "nesslabs.com", "scotthyoung.com", "thegoodbusy.substack.com", "rishad.substack.com",
                                  "davidepstein.substack.com", "ryanholiday.net", "oliverburkeman.com", "gorick.com", "justinwelsh.me",
                                  "fs.blog", "vizi.substack.com", "theatlantic.com", "youtube.com"],
            "anti_themes": ["supplements, gear and gift guides", "book pre-orders and courses", "generic listicles without an idea"],
        },
        "innovation": {
            "themes": ["AI and the future of work / organisations", "agents and what they change", "strategy and foresight", "founders, product and growth",
                       "technology essays (what it means, not what shipped)", "science and long-form interviews (Dwarkesh)", "economics of AI and platforms",
                       "charts and reports that capture change"],
            "signature_domains": ["lennysnewsletter.com", "digitalnative.tech", "mckinsey.com", "oneusefulthing.org", "mikefisher.substack.com",
                                  "decision.substack.com", "dwarkesh.com", "stratechery.com", "hbr.org", "x.com", "leanfoundry.com", "howardyu.substack.com",
                                  "metatrends.substack.com", "techcrunch.com", "nfx.com", "longform.asmartbear.com", "thoughtsparks.substack.com", "youtube.com"],
            "anti_themes": ["release notes and model cards", "funding/earnings news", "sports/entertainment posts from tech writers",
                            "tool directories and 'product pass' drops"],
        },
    },
    "content_exclusions": {
        "anchors": ["order/pre-order/buy the book", "course, cohort, workshop, masterclass, consultation, coaching", "sponsor, partner, promo code, discount",
                    "job posting, hiring", "app download", "podcast player links (apple/spotify/overcast) when the episode page is also linked",
                    "continue in the substack app", "subscribe to rss feed", "view/read online"],
        "senders_never_selected_min_emails": 20,
        "note": "senders with ≥20 emails and 0–1 picks get a near-zero prior; still listed in the report, never silently dropped",
    },
    "title_style": {
        "avg_words": 6.8, "avg_chars": 40.7,
        "note": "evergreen, conceptual titles — 'The X effect', 'Why Y', 'How to Z', a named paradox or metaphor; sentence case is applied later by normalize_names",
    },
    "owner_rules": [
        "≤2 articles from the same person in one edition (measured mode 2; 3 tolerated, above that only for org-level sources)",
        "≤3 HBR articles per edition (measured mode 3; 4 in 12 editions, 5 once)",
        "AI model releases are not innovation picks — new capabilities / general-interest shifts are",
        "when a topic is short, backfill from the `next` pool or classics not yet published",
        "one star per topic, the must-read is usually personal development or leadership (innovation 2/48)",
    ],
    "backfill": {"next_checkbox_pool": True, "classics": True},
}

# Gmail senders that are system notifications, alumni bulletins or pure promos
# — kept in the report but ranked at the floor (0 picks in 54 weeks each).
FLOOR_SENDER_HINTS = ["substack.com", "esade.edu", "iese.edu", "designingyour.life", "workshops.work",
                      "matthiasfrank.de", "circle.so", "competia", "magda.es"]


# ---------------------------------------------------------------------------
# derived priors


def _sender_priors(stats: Dict[str, Any]) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    floor: List[Dict[str, Any]] = []
    for s in stats.get("senders", []):
        row = {"address": s["address"], "name": s["sender"], "emails": s["emails"], "picks": s["selected"],
               "hit_rate_per_email": s["hit_rate_per_email"], "hit_rate_per_link": s["hit_rate_per_link"],
               "stars": s["stars"], "topics": s["topics"]}
        if s["emails"] >= 5:
            if s["selected"] == 0 and s["emails"] >= RULES["content_exclusions"]["senders_never_selected_min_emails"]:
                floor.append(row)
            elif s["selected"] > 0:
                out.append(row)
        if any(h in s["address"] for h in FLOOR_SENDER_HINTS) and row not in floor and s["selected"] == 0:
            floor.append(row)
    out.sort(key=lambda r: (-r["hit_rate_per_email"], -r["picks"]))
    return {"ranked": out, "floor": floor}


def _domain_priors(stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    stars = stats.get("stars_by_domain", {})
    topic_dom = stats.get("topic_domains", {})
    for dom, v in list(stats.get("domains", {}).items())[:80]:
        topics = {t: d.get(dom, 0) for t, d in topic_dom.items() if d.get(dom)}
        rows.append({"domain": dom, "picks": v["total"], "editions": v["editions"],
                     "max_per_edition": v["max_per_edition"], "stars": stars.get(dom, 0), "topics": topics})
    return rows


def build_criteria(stats: Dict[str, Any]) -> Dict[str, Any]:
    w = stats.get("window", {})
    return {
        "version": date.today().isoformat(),
        "window": {"emails": w.get("emails"), "editions": w.get("editions"), "positives": w.get("positives"),
                   "first_email": w.get("first_email"), "last_email": w.get("last_email"),
                   "match_rate": stats.get("match", {}).get("rate")},
        "rules": RULES,
        "sender_priors": _sender_priors(stats),
        "domain_priors": _domain_priors(stats),
        "topic_authors": stats.get("topic_authors", {}),
        "stars_by_author": stats.get("stars_by_author", {}),
        "must_read_by_author": stats.get("must_read", {}).get("by_author", {}),
    }


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stats", default=str(STATS_PATH))
    ap.add_argument("--out", default=str(CRITERIA_PATH))
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args(argv)
    stats_path = Path(args.stats)
    if not stats_path.exists():
        print(f"❌ stats not found at {stats_path} — run `python -m newsletter.triage.history` first")
        return 2
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    crit = build_criteria(stats)
    Path(args.out).write_text(json.dumps(crit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"✅ criteria.json written: {len(crit['sender_priors']['ranked'])} ranked senders, "
          f"{len(crit['sender_priors']['floor'])} floor senders, {len(crit['domain_priors'])} domains → {args.out}")
    if args.print:
        for r in crit["sender_priors"]["ranked"][:25]:
            print(f"  {r['hit_rate_per_email']:.2f}/email  {r['picks']:3} picks / {r['emails']:3} emails  {r['name'][:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
