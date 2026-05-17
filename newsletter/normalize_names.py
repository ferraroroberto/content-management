#!/usr/bin/env python3
"""Notion article name normaliser.

Reads recently-created article rows from the Notion articles DB and rewrites
each title into sentence case while preserving:

- ALL-CAPS words of 2+ letters (treated as acronyms / emphasis)
- A whitelist of proper names (people, companies, products)
- The pronoun "I"
- Acronym dot patterns like ``U.S.A.``
- spaCy-detected PERSON entities (optional, on by default)

Originally from ``E:\\automation\\automation\\notion\\normalize_names.py``;
migrated into the newsletter package as part of issue #18. Config now comes
from this project's ``config/config.json`` (Notion token + articles DB id)
plus a sidecar ``normalize_names_words.json`` for the proper-name whitelist
and word lists.

CLI:
    python -m newsletter.normalize_names --days 14
    python -m newsletter.normalize_names --days 7 --dry-run
    python -m newsletter.normalize_names --test "TEST ALL CAPS"
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_CONFIG = REPO_ROOT / "config" / "config.json"
WORDS_CONFIG = Path(__file__).parent / "normalize_names_words.json"


class NotionNameNormalizer:
    """Normalise Notion article names to sentence case with whitelist preservation."""

    def __init__(self):
        self.config = self._load_config()
        self._setup_api_credentials()
        self._load_word_lists()
        self._initialize_spacy()
        logging.info("✅ Name normalizer initialized")
        logging.info(f"📊 Database ID: {self.database_id}")
        logging.info(
            f"🔤 Special cases: {len(self.special_cases)}, "
            f"Common words: {len(self.common_words)}, "
            f"Proper names: {len(self.proper_names)}"
        )

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        with PROJECT_CONFIG.open("r", encoding="utf-8") as f:
            proj = json.load(f)
        with WORDS_CONFIG.open("r", encoding="utf-8") as f:
            words = json.load(f)
        archive = proj.get("newsletter_archive", {})
        return {
            "notion_api_key": proj["notion"]["api_token"],
            "database_id": archive["articles_db_id"],
            "use_spacy": words.get("use_spacy", True),
            "proper_name_whitelist": words.get("proper_name_whitelist", []),
            "special_cases": words.get("special_cases", []),
            "common_words": words.get("common_words", []),
            "common_words_with_punct": words.get("common_words_with_punct", []),
        }

    def _setup_api_credentials(self):
        self.notion_api_key = self.config["notion_api_key"]
        self.database_id = self.config["database_id"]
        if not all([self.notion_api_key, self.database_id]):
            raise ValueError("Missing notion_api_key or articles_db_id in config")
        self.headers = {
            "Authorization": f"Bearer {self.notion_api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    def _load_word_lists(self):
        self.proper_names = set(self.config["proper_name_whitelist"])
        self.special_cases = set(self.config["special_cases"])
        self.common_words = set(self.config["common_words"])
        self.common_words_with_punct = set(self.config["common_words_with_punct"])

    def _initialize_spacy(self):
        self.spacy_nlp = None
        if not self.config.get("use_spacy", False):
            return
        try:
            import spacy
            logging.info("🔄 Loading spaCy model (en_core_web_sm)…")
            self.spacy_nlp = spacy.load("en_core_web_sm")
            logging.info("🧠 spaCy loaded for entity detection")
        except ImportError:
            logging.warning("⚠️ spaCy requested but not installed; falling back to heuristics")
        except Exception as e:
            logging.warning(f"⚠️ spaCy failed to load: {e}. Falling back to heuristics")

    # ------------------------------------------------------------------ DB I/O

    def _query_notion_database(self, days: int) -> List[Dict[str, Any]]:
        filter_date = datetime.utcnow() - timedelta(days=days)
        filter_date_str = filter_date.isoformat() + "Z"
        logging.info(
            f"🔍 Querying articles created since {filter_date_str} ({days} days back)"
        )
        body: Dict[str, Any] = {
            "filter": {"and": [{"property": "created", "created_time": {"after": filter_date_str}}]},
            "sorts": [{"property": "created", "direction": "descending"}],
        }
        pages: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            if cursor:
                body["start_cursor"] = cursor
            try:
                resp = requests.post(
                    f"https://api.notion.com/v1/databases/{self.database_id}/query",
                    headers=self.headers, json=body, timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as e:
                logging.error(f"❌ Notion API error: {e}")
                raise
            results = data.get("results", [])
            if not results:
                break
            pages.extend(results)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        logging.info(f"📊 Total pages retrieved: {len(pages)}")
        return pages

    def _extract_page_info(self, page: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        page_id = page.get("id", "")
        last_edited_time = page.get("last_edited_time", "")
        prop = page.get("properties", {}).get("article", {})
        if not prop or prop.get("type") != "title":
            return None
        arr = prop.get("title", [])
        if not arr:
            return None
        name_text = "".join([seg.get("plain_text", "") for seg in arr]).strip()
        if not name_text:
            return None
        return page_id, last_edited_time, name_text

    def _update_page_title(self, page_id: str, new_value: str) -> bool:
        body = {
            "properties": {
                "article": {"title": [{"type": "text", "text": {"content": new_value}}]}
            }
        }
        try:
            resp = requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=self.headers, json=body, timeout=30,
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Failed to update page {page_id[:8]}…: {e}")
            return False

    # ----------------------------------------------------------- normalisation

    def _normalize_name(self, original_name: str) -> str:
        if not original_name:
            return original_name
        normalized = self._apply_sentence_case(original_name)
        original_tokens = original_name.split()
        normalized_tokens = normalized.split()
        result: List[str] = []
        i = 0
        while i < len(normalized_tokens):
            matched_length = self._find_multi_word_match(normalized_tokens, i)
            if matched_length > 1:
                proper = self._get_proper_name_for_tokens(normalized_tokens[i:i + matched_length])
                result.extend(proper.split())
                i += matched_length
            else:
                orig_token = original_tokens[i]
                norm_token = normalized_tokens[i]
                sentence_punct, word_part = self._extract_sentence_punctuation(norm_token)
                should_cap = self._should_capitalize_token(i, normalized_tokens)
                word_part = self._capitalize_first_letter(word_part) if should_cap else self._lowercase_first_letter(word_part)
                restored = self._restore_token_capitalization(orig_token, word_part)
                result.append(restored + sentence_punct)
                i += 1
        return re.sub(r"\s+", " ", " ".join(result)).strip()

    def _find_multi_word_match(self, tokens: List[str], start: int) -> int:
        for proper in self.proper_names:
            if " " not in proper:
                continue
            words = proper.lower().split()
            n = len(words)
            if (start + n <= len(tokens)
                and [w.lower() for w in tokens[start:start + n]] == words
                and self._is_valid_multi_word_match(tokens[start:start + n], proper)):
                return n
        return 1

    def _get_proper_name_for_tokens(self, tokens: List[str]) -> str:
        text = " ".join(t.lower() for t in tokens)
        for proper in self.proper_names:
            if proper.lower() == text:
                return proper
        return " ".join(tokens)

    def _should_capitalize_token(self, idx: int, normalized: List[str]) -> bool:
        if idx == 0:
            return True
        prev = normalized[idx - 1]
        if re.search(r"[A-Za-z]\.[A-Za-z]", prev):
            if re.match(r"^[A-Za-z](?:\.[A-Za-z])+\.?$", prev):
                return bool(re.search(r"[!?]+$", prev))
        return bool(re.search(r"[.!?]+$", prev))

    @staticmethod
    def _capitalize_first_letter(token: str) -> str:
        return token[0].upper() + token[1:] if token and token[0].isalpha() else token

    @staticmethod
    def _lowercase_first_letter(token: str) -> str:
        return token[0].lower() + token[1:] if token and token[0].isalpha() else token

    @staticmethod
    def _apply_sentence_case(text: str) -> str:
        if not text or not text[0].isalpha():
            return text
        return text[0].upper() + text[1:].lower()

    def _restore_token_capitalization(self, original: str, normalised: str) -> str:
        _, orig_word = self._extract_sentence_punctuation(original)
        alpha = re.sub(r"[^\w]", "", orig_word)
        if len(alpha) >= 2 and alpha.isupper():
            return orig_word
        if orig_word.lower() == "i":
            return "I"
        if self._is_proper_name(orig_word):
            return self._get_proper_name_capitalization(orig_word)
        if self.spacy_nlp and self._is_person_entity(orig_word):
            return orig_word
        if re.search(r"[^\w\s]", orig_word):
            return self._handle_punctuated_token(orig_word, normalised)
        return normalised

    def _is_proper_name(self, token: str) -> bool:
        token_lower = token.lower()
        for proper in self.proper_names:
            if proper.lower() == token_lower:
                return True
            if " " in proper and token_lower in proper.lower().split() and token_lower not in self.common_words:
                return True
        return False

    def _get_proper_name_capitalization(self, token: str) -> str:
        tl = token.lower()
        for proper in self.proper_names:
            if proper.lower() == tl:
                return proper
            if " " in proper:
                for word in proper.split():
                    if word.lower() == tl:
                        return word
        return token

    def _handle_punctuated_token(self, token: str, normalised: str) -> str:
        match = re.match(r"^([A-Za-z]+)", token)
        if not match:
            return token
        alpha = match.group(1)
        if len(alpha) >= 2 and alpha.isupper():
            return token
        if self._is_proper_name(alpha):
            return token
        norm_match = re.match(r"^([A-Za-z]+)", normalised)
        norm_alpha = norm_match.group(1) if norm_match else normalised
        return self._reconstruct_with_punctuation(norm_alpha, token)

    @staticmethod
    def _reconstruct_with_punctuation(new_alpha: str, original: str) -> str:
        if "'" in original:
            parts = original.split("'", 1)
            if len(parts) == 2:
                return new_alpha + "'" + parts[1]
        match = re.match(r"^([A-Za-z]+)", original)
        if match:
            return new_alpha + original[len(match.group(1)):]
        parts = re.findall(r"[^\w]+|\w+", original)
        out: List[str] = []
        replaced = False
        for part in parts:
            if part.isalpha() and not replaced:
                out.append(new_alpha)
                replaced = True
            else:
                out.append(part)
        return "".join(out)

    @staticmethod
    def _extract_sentence_punctuation(token: str) -> Tuple[str, str]:
        if re.search(r"[A-Za-z]\.[A-Za-z]", token):
            m = re.match(r"^([A-Za-z](?:\.[A-Za-z])+\.?)([.!?]*)$", token)
            if m:
                base, trailing = m.group(1), m.group(2)
                if trailing and re.search(r"[!?]", trailing):
                    return trailing, base
                return "", token
        m = re.search(r"([.!?]+)$", token)
        if m:
            punct = m.group(1)
            return punct, token[:-len(punct)]
        return "", token

    def _is_valid_multi_word_match(self, tokens: List[str], proper: str) -> bool:
        text = " ".join(t.lower() for t in tokens)
        if text == proper.lower():
            return True
        distinctive = [w for w in tokens if w.lower() not in self.common_words]
        return len(distinctive) >= 2 or proper in self.special_cases

    def _is_person_entity(self, token: str) -> bool:
        if not self.spacy_nlp:
            return False
        doc = self.spacy_nlp(token)
        return any(ent.label_ == "PERSON" for ent in doc.ents)

    # --------------------------------------------------------------- processing

    def process_database(self, days: int, dry_run: bool = False) -> List[Dict[str, Any]]:
        pages = self._query_notion_database(days)
        results: List[Dict[str, Any]] = []
        stats = {"processed": 0, "updated": 0, "unchanged": 0, "would_update": 0}
        if dry_run:
            logging.info("🔍 DRY RUN MODE: no Notion writes")
        for page in pages:
            info = self._extract_page_info(page)
            if not info:
                continue
            page_id, last_edited, original = info
            normalised = self._normalize_name(original)
            results.append({
                "page_id": page_id, "last_edited_time": last_edited,
                "original_name": original, "normalized_name": normalised,
            })
            stats["processed"] += 1
            if original != normalised:
                if dry_run:
                    stats["would_update"] += 1
                    logging.info(f'📝 [DRY RUN] "{original}" → "{normalised}"')
                else:
                    if self._update_page_title(page_id, normalised):
                        stats["updated"] += 1
                        logging.info(f'📝 "{original}" → "{normalised}"')
                    else:
                        logging.error(f'❌ Failed to update: "{original}"')
            else:
                stats["unchanged"] += 1
                logging.info(f'✅ Already normalised: "{original}"')
        if dry_run:
            logging.info(f"✅ Processed {stats['processed']} pages — would update {stats['would_update']}, unchanged {stats['unchanged']}")
        else:
            logging.info(f"✅ Processed {stats['processed']} pages — updated {stats['updated']}, unchanged {stats['unchanged']}")
        return results


# --------------------------------------------------------------- callable entry


def run(days: int = 14, dry_run: bool = False, debug: bool = False) -> List[Dict[str, Any]]:
    """Orchestrator-friendly entry point — same behaviour as ``main()``."""
    _setup_logging(debug)
    return NotionNameNormalizer().process_database(days=days, dry_run=dry_run)


def _setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    # Re-configure UTF-8 stdio so emoji-bearing log lines don't crash cp1252.
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    else:
        logging.getLogger().setLevel(level)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14,
                        help="Look back N days (default: 14)")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--test", type=str, help="Normalise one string and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show changes without writing to Notion")
    args = parser.parse_args()
    _setup_logging(args.debug)
    try:
        if args.test:
            normaliser = NotionNameNormalizer()
            out = normaliser._normalize_name(args.test)
            logging.info("Original:   %s", args.test)
            logging.info("Normalised: %s", out)
            logging.info("Changed:    %s", "yes" if args.test != out else "no")
            return 0
        run(days=args.days, dry_run=args.dry_run, debug=args.debug)
        logging.info("✅ Done")
        return 0
    except Exception as e:
        logging.error(f"❌ Fatal: {e}")
        if args.debug:
            logging.exception("Traceback:")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
