"""Pure tests for the podcast episode pipeline (issue #333) — no audio, no hub."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import podcast_pipeline  # noqa: E402
from podcast import captions, clips, edit, metrics, package, review, score, transcribe  # noqa: E402
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
    def test_chunks_break_on_sentence_end_speaker_and_size(self) -> None:
        words = _words("one two three four five six. seven", "guest") + _words("eight", "host", start=5.0)
        chunks = captions.chunk_words(words, max_words=4, line_chars=40)
        self.assertEqual([[w["w"] for w in c] for c in chunks],
                         [["one", "two", "three", "four"], ["five", "six."], ["seven"], ["eight"]])

    def test_each_word_is_highlighted_in_turn_while_the_chunk_shows(self) -> None:
        ass = captions.build_ass(_words("we value sleep", start=1.0), 0.0, 5.0, captions.LAYOUTS["1x1"])
        rows = [line.split(",", 9) for line in ass.splitlines() if line.startswith("Dialogue:")]
        yellow = "{\\c&H0001ECFD&}"
        self.assertEqual([(r[1], r[2]) for r in rows],
                         [("0:00:01.00", "0:00:01.40"), ("0:00:01.40", "0:00:01.80"), ("0:00:01.80", "0:00:02.50")])
        self.assertEqual([re.sub(r"\{[^}]*\}", "", r[9]) for r in rows], ["we value sleep"] * 3)
        self.assertEqual([r[9].split(yellow)[1].split("{")[0] for r in rows], ["we", "value", "sleep"])
        self.assertIn("Sora ExtraBold", ass)

    def test_commas_do_not_end_a_chunk(self) -> None:
        # the owner's captions run across commas ("routes, out of / Helsinki, follow")
        chunks = captions.chunk_words(_words("routes, out of Helsinki, follow"), max_words=7, line_chars=16, lines=2)
        self.assertEqual(len(chunks), 1)

    def test_no_caption_line_is_wider_than_the_layout_allows(self) -> None:
        # a few long words used to stay on one line and run off the 9:16 frame
        text = "more productive, whatever, systematically, you're notice what happens extraordinarily"
        for layout in captions.LAYOUTS.values():
            ass = captions.build_ass(_words(text), 0.0, 10.0, layout)
            for line in (ln for ln in ass.splitlines() if ln.startswith("Dialogue:")):
                visible = re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9])
                self.assertLessEqual(len(visible), layout.line_chars, (layout.name, visible))

    def test_two_line_chunk_is_two_layered_events_a_pitch_apart(self) -> None:
        layout = captions.LAYOUTS["9x16"]
        ass = captions.build_ass(_words("routes, out of Helsinki, follow"), 0.0, 3.0, layout)
        rows = [line.split(",", 9) for line in ass.splitlines() if line.startswith("Dialogue:")][:2]  # first word
        self.assertEqual([re.sub(r"\{[^}]*\}", "", r[9]) for r in rows], ["routes, out of", "Helsinki, follow"])
        # separate layers, or libass's collision handling moves the lines
        self.assertEqual([r[0] for r in rows], ["Dialogue: 0", "Dialogue: 1"])
        self.assertEqual([int(r[7]) for r in rows], [layout.margin_v + layout.line_pitch, layout.margin_v])


class ScoreTests(unittest.TestCase):
    def test_word_error_rate(self) -> None:
        self.assertEqual(score.word_error_rate(["a", "b", "c", "d"], ["a", "x", "c", "d"]), 0.25)
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
        self.assertIn("<strong>Where to find Guest and their work</strong></h4>", out)

    def test_website_links_come_from_episode_json(self) -> None:
        ep = _episode(Path("."))
        ep.extra = {"links": [{"label": "Guest’s book", "url": "https://example.com/?a=1&b=2"}]}
        copy = {"website_title": "T", "website_intro": "Intro.", "insights": []}
        out = package.website_html(ep, copy, [])
        self.assertIn('<a href="https://example.com/?a=1&amp;b=2" target="_blank">Guest’s book</a>', out)
        self.assertNotIn("#guest-linkedin", out.split("Where to find")[1])


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

    def test_edit_is_not_done_until_every_clip_has_its_cuts_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = _episode(Path(tmp))
            done = {"keep": [[0, 1]], "caption_review": {}}
            clips.save_clips(ep, [{"number": 1, **done}, {"number": 2}])
            self.assertFalse(podcast_pipeline.STAGES["edit"][1](ep))
            clips.save_clips(ep, [{"number": 1, **done}, {"number": 2, **done}])
            self.assertTrue(podcast_pipeline.STAGES["edit"][1](ep))

    def test_clip_without_a_file_name_is_not_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ep = _episode(Path(tmp))
            (ep.package).mkdir(parents=True)
            (ep.package / clips.CLIPS_FILE).write_text(json.dumps([{"number": 1, "title": "a"}]), encoding="utf-8")
            self.assertFalse(podcast_pipeline.STAGES["render"][1](ep))


class CaptionSourceTests(unittest.TestCase):
    """Captions come from the clip's own decode, which is complete and in sync
    where the per-track pass lost speech under cross-talk (#333, pilot clip 6)."""

    EPISODE = [_w("say,", 105.3, "host"), _w("okay,", 106.6, "host")]  # stretched over a lost sentence
    CLIP = {"number": 1, "start": 100.0, "end": 110.0}

    def _decode(self, segments: list[dict]) -> list[dict]:
        from unittest import mock
        with mock.patch.object(edit, "whisper_segments", return_value=segments):
            return edit.decode_clip({"whisper_url": "http://whisper"}, self.CLIP, Path("x.wav"), self.EPISODE,
                                    metrics.StageRecord("edit"))

    def test_captions_use_the_clip_decode_at_episode_times(self) -> None:
        seg = {"words": [{"word": " And", "start": 0.2, "end": 0.4}, {"word": " then", "start": 0.4, "end": 0.6}]}
        self.assertEqual([(w["w"], w["s"]) for w in self._decode([seg])], [("And", 100.2), ("then", 100.4)])

    def test_a_looping_clip_decode_falls_back_to_the_episode_words(self) -> None:
        loop = [{"word": f" {t}", "start": i * 0.2, "end": i * 0.2 + 0.1}
                for i, t in enumerate("so I will concede on this ".split() * 8)]
        self.assertEqual(self._decode([{"words": loop}]), self.EPISODE)


class CutListTests(unittest.TestCase):
    """Jump cuts (#335): long pauses shrink, filler islands go, every cut lands in silence."""

    def test_long_pause_is_shortened_and_a_short_one_kept(self) -> None:
        keep = edit.cut_list(100.0, 110.0, [(103.0, 104.0), (106.0, 106.3)], [])
        half = edit.KEEP_PAUSE_S / 2
        self.assertEqual(len(keep), 2)  # one cut, the 0.3 s pause stays
        self.assertAlmostEqual(keep[0][1], 103.0 + half, delta=1 / edit.FPS)
        self.assertAlmostEqual(keep[1][0], 104.0 - half, delta=1 / edit.FPS)
        self.assertAlmostEqual(edit.kept_seconds(keep), 10.0 - (1.0 - edit.KEEP_PAUSE_S), delta=2 / edit.FPS)

    def test_filler_island_goes_with_the_silences_around_it(self) -> None:
        # pause 0.2 s, "um" 0.3 s, pause 0.2 s: each pause alone is kept, the island is not
        keep = edit.cut_list(100.0, 110.0, [(104.0, 104.2), (104.5, 104.7)], [(104.2, 104.5)])
        self.assertEqual(len(keep), 2)
        self.assertLessEqual(keep[0][1], 104.2)
        self.assertGreaterEqual(keep[1][0], 104.5)

    def test_edges_keep_a_little_air_and_cuts_sit_on_the_frame_grid(self) -> None:
        keep = edit.cut_list(100.0, 110.0, [(100.0, 101.0), (109.0, 110.0)], [])
        self.assertAlmostEqual(keep[0][0], 101.0 - edit.LEAD_S, delta=1 / edit.FPS)
        self.assertAlmostEqual(keep[-1][1], 109.0 + edit.TAIL_S, delta=1 / edit.FPS)
        for a, b in keep:
            for t in (a, b):
                frames = (t - 100.0) * edit.FPS
                self.assertAlmostEqual(frames, round(frames), places=2)

    def test_silences_are_found_on_the_audio(self) -> None:
        import numpy as np
        rate = 16000
        tone = (8000 * np.sin(np.arange(rate) * 2 * np.pi * 220 / rate)).astype(np.int16)
        audio = np.concatenate([tone, np.zeros(rate // 2, dtype=np.int16), tone])
        self.assertEqual(edit.silences(audio, rate, offset=10.0), [(11.0, 11.5)])

    def test_islands_need_fillers_or_a_listen(self) -> None:
        isl = [(1.0, 1.3), (2.0, 2.4), (3.0, 3.3), (4.0, 4.5)]
        words = [_w("um,", 1.05, dur=0.2), _w("sleep", 2.05, dur=0.3), _w("you", 4.0, dur=0.2),
                 _w("know,", 4.2, dur=0.2)]
        fillers, unknown = edit.classify_islands(isl, words)
        self.assertEqual(fillers, [(1.0, 1.3), (4.0, 4.5)])
        self.assertEqual(unknown, [(3.0, 3.3)])
        self.assertTrue(edit.heard_as_filler("Uh,"))
        self.assertTrue(edit.heard_as_filler(""))
        self.assertFalse(edit.heard_as_filler("And,"))  # the pilot: most islands were real words


class WordTimingTests(unittest.TestCase):
    KEEP = [(100.0, 103.0), (104.0, 110.0)]  # one second cut at 103-104

    def test_remap_shifts_words_after_a_cut_and_clamps_inside_it(self) -> None:
        self.assertEqual(edit.remap(101.0, self.KEEP), 1.0)
        self.assertEqual(edit.remap(103.5, self.KEEP), 3.0)  # inside the cut: start of the next span
        self.assertEqual(edit.remap(105.0, self.KEEP), 4.0)

    def test_a_word_start_in_a_silence_moves_to_the_speech(self) -> None:
        words = [_w("day.", 100.0, dur=0.4), {"w": "Why", "s": 100.4, "e": 101.5, "spk": "mix"}]
        snapped = edit.snap_to_speech(words, [(100.45, 101.2)])
        self.assertEqual([(w["s"], w["e"]) for w in snapped], [(100.0, 100.4), (101.2, 101.5)])

    def test_caption_words_follow_the_cut_and_drop_fillers(self) -> None:
        words = [_w("we", 102.0), _w("um", 103.2), _w("sleep", 104.5), _w("late", 111.0)]
        out = edit.cut_words(words, self.KEEP)
        self.assertEqual([(w["w"], w["s"], w["e"]) for w in out], [("we", 2.0, 2.3), ("sleep", 3.5, 3.8)])

    def test_render_graph_trims_the_same_spans_for_picture_and_sound(self) -> None:
        from podcast import render
        clip = {"start": 100.0, "end": 110.0, "speaker": "guest", "keep": [list(s) for s in self.KEEP],
                "shots": [{"a": 100.0, "b": 103.0, "spk": "guest", "zoom": 1.0},
                          {"a": 104.0, "b": 107.0, "spk": "host", "zoom": 1.0},
                          {"a": 107.0, "b": 110.0, "spk": "host", "zoom": 1.18}]}
        square = render.filter_graph(captions.LAYOUTS["1x1"], clip, "c.ass")
        self.assertIn("[0:v]fps=24,split=1", square)
        self.assertIn("[1:v]fps=24,split=2", square)
        self.assertIn("concat=n=3:v=1:a=0", square)
        self.assertEqual(square.count("afade=t=in"), 2)
        self.assertIn("atrim=start=4.0000:end=10.0000", square)
        tall = render.filter_graph(captions.LAYOUTS["9x16"], clip, "c.ass")
        self.assertIn("trim=start=4.0000:end=10.0000,setpts", tall)
        self.assertIn("concat=n=2:v=1:a=0", tall)


class CameraTests(unittest.TestCase):
    def test_camera_follows_the_louder_track_and_ignores_short_turns(self) -> None:
        import numpy as np
        frame = transcribe.FRAME_MS / 1000

        def track(*spans: tuple[float, float]) -> np.ndarray:
            out = np.full(int(12 / frame), 50.0)  # bleed / room
            for a, b in spans:
                out[int(a / frame):int(b / frame)] = 1000.0
            return out
        # host asks (0-3 s), guest answers (4-8 s) with a 0.6 s host "yeah" inside, host again (9-12 s)
        rms = {"guest": track((4.0, 6.0), (6.6, 8.0)), "host": track((0.0, 3.0), (6.0, 6.6), (9.0, 12.0))}
        switches = edit.speaker_switches(rms, 100.0, "guest")
        self.assertEqual([spk for _, spk in switches], ["host", "guest", "host"])
        self.assertAlmostEqual(switches[1][0], 103.9, places=2)

    def test_long_stretch_gets_punch_ins_and_jump_cuts_reframe(self) -> None:
        words = _words(" ".join(["word"] * 40), start=100.0, step=0.5)  # 20 s of one speaker
        keep = [(100.0, 110.0), (110.5, 120.0)]
        plan = edit.shots(keep, [(100.0, "guest")], words)
        self.assertTrue(all(s["b"] - s["a"] <= edit.MAX_SHOT_S + 1e-6 for s in plan))
        self.assertGreater(len({s["zoom"] for s in plan}), 1)
        self.assertNotEqual(plan[0]["zoom"], plan[1]["zoom"])
        self.assertAlmostEqual(sum(s["b"] - s["a"] for s in plan), edit.kept_seconds(keep), places=3)


class CaptionReviewTests(unittest.TestCase):
    WORDS = [_w(t, i * 0.4) for i, t in enumerate("We aim for hate. I don't hate you.".split())]

    def test_corrections_apply_by_index_and_keep_punctuation(self) -> None:
        fixed, applied = review.apply_corrections(self.WORDS, [{"i": 3, "from": "hate", "to": "eight"}])
        self.assertEqual(" ".join(w["w"] for w in fixed), "We aim for eight. I don't hate you.")
        self.assertEqual(applied, [{"i": 3, "from": "hate.", "to": "eight."}])
        self.assertEqual(fixed[3]["s"], self.WORDS[3]["s"])  # timing kept

    def test_a_correction_quoting_another_word_is_skipped(self) -> None:
        fixed, applied = review.apply_corrections(self.WORDS, [{"i": 5, "from": "hate", "to": "eight"},
                                                               {"i": 99, "from": "x", "to": "y"},
                                                               {"from": "no index"}])
        self.assertEqual([w["w"] for w in fixed], [w["w"] for w in self.WORDS])
        self.assertEqual(applied, [])

    def test_only_sure_fixes_are_applied_and_guesses_become_doubts(self) -> None:
        from unittest import mock
        reply = {"corrections": [{"i": 3, "from": "hate.", "to": "eight.", "sure": True},
                                 {"i": 6, "from": "hate", "to": "love", "sure": False}],
                 "raw_score": 3, "final_score": 5, "doubts": ""}
        ep = _episode(Path(tempfile.gettempdir()))
        with mock.patch.object(review, "ask_json", return_value=reply):
            fixed, record = review.review_clip(ep, {}, None, {"number": 1, "start": 0.0, "end": 5.0},
                                               self.WORDS, [])
        self.assertEqual(" ".join(w["w"] for w in fixed), "We aim for eight. I don't hate you.")
        self.assertEqual(record["corrections"], [{"i": 3, "from": "hate.", "to": "eight."}])
        self.assertEqual(record["doubts"], "hate → love?")
        self.assertEqual((record["raw_score"], record["final_score"]), (3, 5))

    def test_capital_is_kept_and_an_empty_fix_drops_the_word(self) -> None:
        fixed, _ = review.apply_corrections(self.WORDS, [{"i": 0, "from": "we", "to": "wee"},
                                                         {"i": 2, "from": "for", "to": ""}])
        self.assertEqual([w["w"] for w in fixed][:3], ["Wee", "aim", "hate."])


class OpenerTests(unittest.TestCase):
    """Clips open on their hook, not on "And…/So…/Yeah…" (#335)."""

    def test_leading_conjunctions_and_then_are_openers(self) -> None:
        self.assertEqual(edit.opener_count(_words("But then I see that it improved")), 2)
        self.assertEqual(edit.opener_count(_words("Absolutely. Yeah. And now that you said that")), 3)
        self.assertEqual(edit.opener_count(_words("Sleep is not a waste of time")), 0)
        self.assertEqual(edit.opener_count(_words("then I changed")), 0)  # "then" only after and/but

    def test_opener_is_cut_up_to_the_silence_before_the_hook(self) -> None:
        words = [_w("And", 100.2, dur=0.2), _w("sleep", 100.9), _w("matters", 101.3)]
        region = edit.opener_cut(words, 1, [(100.0, 100.15), (100.45, 100.85)], 100.0)
        self.assertEqual(region, (100.0, 100.45))
        keep = edit.cut_list(100.0, 110.0, [(100.0, 100.15), (100.45, 100.85)], [region])
        self.assertAlmostEqual(keep[0][0], 100.85 - edit.LEAD_S, delta=1 / edit.FPS)

    def test_without_a_silence_the_cut_sits_on_the_word_boundary(self) -> None:
        words = [_w("So", 100.0, dur=0.2), _w("sleep", 100.3)]
        keep = edit.cut_list(100.0, 110.0, [], [edit.opener_cut(words, 1, [], 100.0)])
        self.assertAlmostEqual(keep[0][0], 100.25, delta=1 / edit.FPS)


if __name__ == "__main__":
    unittest.main()
