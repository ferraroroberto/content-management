"""Pure tests for the podcast episode pipeline (issue #333) — no audio, no hub."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import podcast_pipeline  # noqa: E402
from podcast import captions, clips, metrics, package, score, transcribe  # noqa: E402
from podcast.episode import Episode  # noqa: E402


def _w(text: str, start: float, spk: str = "guest", dur: float = 0.3) -> dict:
    return {"w": text, "s": start, "e": round(start + dur, 2), "spk": spk}


def _words(text: str, spk: str = "guest", start: float = 0.0, step: float = 0.4) -> list[dict]:
    return [_w(t, round(start + i * step, 2), spk) for i, t in enumerate(text.split())]


def _episode(folder: Path) -> Episode:
    return Episode(folder=folder, guest="Guest Person", guest_display="Guest Person", guest_first="Guest",
                   guest_pronoun_possessive="their", tracks={"guest": folder / "g.mp4", "host": folder / "h.mp4"})


class SegmentWordsTests(unittest.TestCase):
    def test_subword_tokens_fold_into_the_previous_word(self) -> None:
        # whisper.cpp: a token without a leading space continues the word.
        seg = {"words": [
            {"word": " I", "start": 0.0, "end": 0.1}, {"word": " don", "start": 0.1, "end": 0.3},
            {"word": "'t", "start": 0.3, "end": 0.4}, {"word": " U", "start": 0.5, "end": 0.6},
            {"word": "plo", "start": 0.6, "end": 0.8}, {"word": "ad", "start": 0.8, "end": 1.0},
            {"word": ".", "start": 1.4, "end": 1.6},
        ]}
        words = transcribe.segment_words(seg, "host", offset=10.0)
        self.assertEqual([w["w"] for w in words], ["I", "don't", "Upload."])
        self.assertEqual((words[2]["s"], words[2]["e"]), (10.5, 11.0))  # punctuation keeps the word's end

    def test_first_token_without_space_starts_a_word(self) -> None:
        words = transcribe.segment_words({"words": [{"word": "Hello", "start": 0, "end": 0.2}]}, "guest")
        self.assertEqual([w["w"] for w in words], ["Hello"])


class LoopTests(unittest.TestCase):
    def test_real_speech_repeated_three_times_is_not_a_loop(self) -> None:
        text = "it was one of the most popular classes " * 3
        self.assertEqual(transcribe.find_loops(_words(text)), [])

    def test_decoder_loop_is_found(self) -> None:
        words = _words("so I will concede on this " * 8, start=100.0)
        loops = transcribe.find_loops(words)
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0][0], 100.0)

    def test_collapse_repeats_keeps_one_copy(self) -> None:
        out = transcribe.collapse_repeats(_words("we said that we said that we said that today"))
        self.assertEqual(" ".join(w["w"] for w in out), "we said that today")

    def test_backchannel_inside_a_turn_is_dropped(self) -> None:
        words = _words("this is my long point", "guest") + [_w("yeah", 1.0, "host")] + \
            _words("and it goes on", "guest", start=1.8)
        turns = transcribe.build_turns(words)
        self.assertEqual(len(turns), 1)
        self.assertNotIn("yeah", turns[0]["text"])


class BleedGateTests(unittest.TestCase):
    def test_frames_dominated_by_the_other_track_are_muted(self) -> None:
        import numpy as np
        own = np.array([1.0] * 10 + [0.1] * 20 + [1.0] * 10)
        other = np.array([0.1] * 10 + [1.0] * 20 + [0.1] * 10)
        keep = transcribe.keep_mask(own, other, hold_frames=2)
        self.assertTrue(keep[:12].all())
        self.assertFalse(keep[13:27].any())
        self.assertTrue(keep[28:].all())


class SelectionTests(unittest.TestCase):
    SENTS = [{"speaker": "guest", "start": 10.0 * i, "end": 10.0 * i + 9.5, "text": f"s{i}"} for i in range(20)]

    def test_ranges_map_to_padded_sentence_times_and_filter(self) -> None:
        raw = [
            {"start_id": 0, "end_id": 3, "title": "First Clip!"},     # 40 s, kept
            {"start_id": 2, "end_id": 5, "title": "overlaps"},        # overlaps the first
            {"start_id": 6, "end_id": 6, "title": "too short"},       # 10 s
            {"start_id": 99, "end_id": 100, "title": "bad ids"},
            {"start_id": "x", "end_id": 2},
            {"start_id": 8, "end_id": 12, "title": "second"},
            {"start_id": 14, "end_id": 18, "title": "third"},
        ]
        picked = clips.resolve_candidates(raw, self.SENTS, n=2, min_s=25, max_s=90)
        self.assertEqual([c["title"] for c in picked], ["first clip", "second"])
        self.assertEqual(picked[0]["start"], round(0.0, 2))
        self.assertEqual(picked[0]["end"], round(39.5 + clips.END_PAD_S, 2))

    def test_clip_text_joins_one_speakers_sentences(self) -> None:
        words = _words("I slept badly. Then I changed.", "host") + _words("Why?", "guest", start=3.0)
        ep = _episode(Path("."))
        self.assertEqual(clips.clip_text(ep, words, 0.0, 10.0), "Roberto: I slept badly. Then I changed.\nGuest: Why?")

    def test_wrapped_or_malformed_replies_become_a_list(self) -> None:
        self.assertEqual(clips._as_list({"clips": [{"start_id": 1}, "junk"]}), [{"start_id": 1}])
        self.assertEqual(clips._as_list("nope"), [])

    def test_instagram_caption_template(self) -> None:
        ep = _episode(Path("."))
        ep.adjective = "brilliant"
        self.assertEqual(clips.instagram_caption(ep, "sleep is a skill"),
                         "Sleep is a skill. A clip from my conversation with the brilliant Guest Person")


class CaptionTests(unittest.TestCase):
    def test_chunks_break_on_punctuation_speaker_and_size(self) -> None:
        words = _words("one two three four five six. seven", "guest") + _words("eight", "host", start=5.0)
        chunks = captions.chunk_words(words, max_words=4, max_chars=40)
        self.assertEqual([[w["w"] for w in c] for c in chunks],
                         [["one", "two", "three", "four"], ["five", "six."], ["seven"], ["eight"]])

    def test_ass_highlights_one_keyword_in_yellow(self) -> None:
        ass = captions.build_ass(_words("we value deep sleep", start=1.0), 0.0, 5.0, captions.LAYOUTS["1x1"])
        dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertEqual(len(dialogue), 1)
        self.assertIn("{\\c&H0001ECFD&}value{\\c&H00FFFFFF&}", dialogue[0])
        self.assertIn("Sora ExtraBold", ass)


class ScoreTests(unittest.TestCase):
    def test_word_error_rate(self) -> None:
        self.assertEqual(score.word_error_rate(["a", "b", "c", "d"], ["a", "x", "c", "d"]), 0.25)
        self.assertEqual(score.wer_to_score(0.04), 5)
        self.assertEqual(score.wer_to_score(0.5), 1)
        self.assertEqual(score.tokens("Don’t STOP, now."), ["don't", "stop", "now"])


class MetricsTests(unittest.TestCase):
    RATES = {"claude_opus": {"input": 5.0, "output": 25.0}}

    def test_cost_is_none_when_a_model_has_no_rate(self) -> None:
        data = {"stages": {
            "select": {"wall_s": 10.0, "whisper_s": 0.0,
                       "llm": [{"model": "claude_opus", "input_tokens": 1_000_000, "output_tokens": 0}]},
            "clean": {"wall_s": 5.0, "whisper_s": 0.0, "llm": [{"model": "mystery", "input_tokens": 10}]},
        }}
        rows = metrics.summarize(data, self.RATES)
        self.assertEqual(rows[0]["cost_usd"], 5.0)
        self.assertIsNone(rows[1]["cost_usd"])
        self.assertIsNone(rows[-1]["cost_usd"])  # total is unknown, never a partial sum
        self.assertEqual(rows[-1]["wall_s"], 15.0)


    def test_backend_without_usage_is_estimated_and_flagged(self) -> None:
        from unittest.mock import patch
        from podcast import hub
        rec = metrics.StageRecord("clean")
        empty = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}
        with patch.object(hub, "call_with_usage", return_value=("x" * 400, empty)):
            hub.ask({"models": {"clean": "gemini_flash"}, "llm_hub_base_url": "http://hub"}, rec, "clean", "y" * 800)
        self.assertEqual((rec.llm[0]["input_tokens"], rec.llm[0]["output_tokens"]), (200, 100))
        rows = metrics.summarize({"stages": {"clean": {"wall_s": 1.0, "llm": rec.llm}}}, self.RATES)
        self.assertTrue(rows[0]["estimated"] and rows[-1]["estimated"])
        self.assertIsNone(rows[0]["cost_usd"])  # no list price for the model: unknown, not free


class CleanTests(unittest.TestCase):
    def test_parse_clean_reads_labels_and_continuations(self) -> None:
        text = "Guest: Hello there.\n\nand more of it\n\n**Roberto:** Thanks."
        self.assertEqual(package.parse_clean(text, ("Guest", "Roberto")), [
            {"speaker": "Guest", "text": "Hello there."},
            {"speaker": None, "text": "and more of it"},
            {"speaker": "Roberto", "text": "Thanks."},
        ])

    def test_rejected_edit_is_retried_and_an_accepted_one_cached(self) -> None:
        from unittest.mock import patch
        chunk = ["Guest: um so the the point is sleep matters a lot", "Roberto: yeah I I agree"]
        good = "Guest: So the point is, sleep matters a lot.\n\nRoberto: Yeah, I agree."
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(package, "ask", side_effect=["Sure! Here it is.", good]) as ask:
            first = package._clean_chunk({}, None, chunk, ("Guest", "Roberto"), Path(tmp))
            again = package._clean_chunk({}, None, chunk, ("Guest", "Roberto"), Path(tmp))
        self.assertEqual(ask.call_count, 2)  # one rejected, one accepted, then the cache
        self.assertEqual(first, again)
        self.assertEqual(first[0], {"speaker": "Guest", "text": "So the point is, sleep matters a lot."})

    def test_website_html_follows_the_live_page_markup(self) -> None:
        ep = _episode(Path("."))
        copy = {"website_title": "A & B", "website_intro": "Talking with Guest Person was great.",
                "insights": [{"group": "Sleep", "bullets": [{"lead": "Habit", "text": "go to bed early"}]}]}
        out = package.website_html(ep, copy, [{"speaker": "Guest", "text": "<script>x</script>"},
                                              {"speaker": None, "text": "More."}])
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>x", out)
        self.assertIn('<a href="#guest-linkedin" target="_blank">Guest Person</a> was great.', out)
        self.assertIn(">Habit: go to bed early</p>", out)
        self.assertEqual(out.count("<strong>Guest</strong>"), 1)  # one name paragraph per turn
        self.assertEqual(out.count("BLOCK 1 START"), 1)


class ResumeTests(unittest.TestCase):
    def test_render_is_not_done_until_every_clip_has_both_crops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = _episode(Path(tmp))
            clips.save_clips(ep, [{"number": 1, "title": "a", "file": "a"}, {"number": 2, "title": "b", "file": "b"}])
            is_done = podcast_pipeline.STAGES["render"][1]
            for crop in ("1x1", "9x16"):
                (ep.package / "clips" / crop).mkdir(parents=True)
                (ep.package / "clips" / crop / "a.mp4").write_bytes(b"x")
            self.assertFalse(is_done(ep))  # crashed after clip 1
            for crop in ("1x1", "9x16"):
                (ep.package / "clips" / crop / "b.mp4").write_bytes(b"x")
            self.assertTrue(is_done(ep))

    def test_clip_without_a_file_name_is_not_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = _episode(Path(tmp))
            (ep.package).mkdir(parents=True)
            (ep.package / clips.CLIPS_FILE).write_text(json.dumps([{"number": 1, "title": "a"}]), encoding="utf-8")
            self.assertFalse(podcast_pipeline.STAGES["render"][1](ep))


if __name__ == "__main__":
    unittest.main()
