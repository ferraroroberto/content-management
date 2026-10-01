"""Tests for the per-clip review loop (issue #339) — no audio, no hub, no ffmpeg."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import podcast_pipeline  # noqa: E402
from podcast import review_state, revise  # noqa: E402
from podcast.episode import Episode  # noqa: E402

FOOTER = "the footer"


def _episode(folder: Path) -> Episode:
    return Episode(folder=folder, guest="Guest Person", guest_display="Guest Person", guest_first="Guest",
                   guest_pronoun_possessive="their", tracks={"guest": folder / "g.mp4", "host": folder / "h.mp4"},
                   adjective="kind")


def _words() -> list[dict]:
    # "so sleep is not a waste of time" at 100.0 s, 0.5 s apart, 0.3 s long
    return [{"w": w, "s": round(100.0 + 0.5 * i, 2), "e": round(100.3 + 0.5 * i, 2), "spk": "host"}
            for i, w in enumerate("So sleep is not a waste of time".split())]


def _clip(number: int = 1, title: str = "sleep is not a waste") -> dict:
    return {"number": number, "title": title, "file": title, "speaker": "host", "text": "x",
            "start": 99.9, "end": 104.2, "keep": [[99.9, 104.2]],
            "shots": [{"a": 99.9, "b": 102.0, "spk": "host", "zoom": 1.0},
                      {"a": 102.0, "b": 104.2, "spk": "guest", "zoom": 1.18}],
            "edit": {"source_s": 4.3, "cut_s": 4.3, "jump_cuts": 0},
            "instagram": "Sleep is not a waste. A clip from my conversation with the kind Guest Person",
            "linkedin": f"Old hook\n\nOld body one.\n\nOld body two.\n\n"
                        f"A clip from my conversation with the kind @Guest Person\n\n{FOOTER}",
            "videos": {"1x1": f"clips/1x1/{title}.mp4", "9x16": f"clips/9x16/{title}.mp4"}}


class ResolveTests(unittest.TestCase):
    def test_indices_become_episode_times(self) -> None:
        ops = revise.resolve({"caption_fixes": [{"i": 5, "from": "waste", "to": "gift"}],
                              "start_word": 1, "end_word": 5, "cuts": [{"from_word": 2, "to_word": 3}]},
                             _clip(), _words())
        self.assertEqual(ops["fixes"], [{"t": 102.5, "from": "waste", "to": "gift"}])
        self.assertAlmostEqual(ops["start"], 100.5 - revise.LEAD_S)
        self.assertAlmostEqual(ops["end"], 102.8 + revise.TAIL_S)
        # midpoints of the gaps around "is not": (100.8+101.0)/2 .. (101.8+102.0)/2
        self.assertEqual([tuple(round(t, 2) for t in c) for c in ops["cuts"]], [(100.9, 101.9)])
        self.assertEqual(ops["unhandled"], [])

    def test_a_stale_or_unknown_reference_is_reported_not_applied(self) -> None:
        ops = revise.resolve({"caption_fixes": [{"i": 5, "from": "happy", "to": "crappy"}],
                              "start_word": 99, "cuts": [{"from_word": 4, "to_word": 2}],
                              "shots": [{"shot": 7, "zoom": 1.0}], "unhandled": "make it funnier"},
                             _clip(), _words())
        self.assertEqual((ops["fixes"], ops["start"], ops["cuts"], ops["shots"]), ([], None, [], []))
        self.assertEqual(len(ops["unhandled"]), 5)
        self.assertEqual(ops["unhandled"][0], "make it funnier")

    def test_zoom_snaps_to_a_house_framing_and_extend_is_capped(self) -> None:
        ops = revise.resolve({"shots": [{"shot": 1, "speaker": "host", "zoom": 1.2}], "all_zoom": 0.9,
                              "extend_start_s": 500, "extend_end_s": "x"}, _clip(), _words())
        self.assertEqual(ops["shots"], [{"a": 102.0, "b": 104.2, "spk": "host", "zoom": 1.18}])
        self.assertEqual(ops["all_zoom"], 1.0)
        self.assertEqual(ops["extend"], (revise.MAX_EXTEND_S, 0.0))


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ep = _episode(Path(tempfile.gettempdir()))

    def _apply(self, reply: dict, clip: dict | None = None):
        clip = clip or _clip()
        return revise.apply_ops(self.ep, clip, _words(), revise.resolve(reply, clip, _words()), FOOTER)

    def test_caption_fix_finds_its_word_by_time(self) -> None:
        clip, words, applied, unhandled = self._apply({"caption_fixes": [{"i": 5, "from": "waste", "to": "gift"}]})
        self.assertEqual(words[5]["w"], "gift")
        self.assertEqual(applied, ["caption 'waste' → 'gift'"])
        self.assertEqual((clip["keep"], unhandled), ([[99.9, 104.2]], []))

    def test_start_trim_drops_the_words_shots_and_seconds_before_it(self) -> None:
        clip, words, applied, _ = self._apply({"start_word": 1})
        self.assertAlmostEqual(clip["keep"][0][0], 100.4, delta=1 / revise.FPS)
        self.assertAlmostEqual(clip["shots"][0]["a"], clip["keep"][0][0])
        self.assertLess(clip["edit"]["cut_s"], 4.3)
        self.assertEqual(len(words), 8)  # words stay; the cut timeline drops the one before the start
        self.assertTrue(applied[0].startswith("start moved"))

    def test_an_interior_cut_splits_the_span_and_drops_its_words(self) -> None:
        clip, words, applied, _ = self._apply({"cuts": [{"from_word": 2, "to_word": 3}]})
        self.assertEqual(len(clip["keep"]), 2)
        self.assertEqual(clip["edit"]["jump_cuts"], 1)
        self.assertNotIn("is", [w["w"] for w in words])
        self.assertEqual(applied, ["cut 2 word(s)"])

    def test_cuts_that_would_empty_the_clip_are_refused(self) -> None:
        clip, words, applied, unhandled = self._apply({"cuts": [{"from_word": 0, "to_word": 7}]})
        self.assertEqual((clip["keep"], len(words), applied), ([[99.9, 104.2]], 8, []))
        self.assertIn("no spoken word", unhandled[0])

    def test_shot_override_and_one_framing(self) -> None:
        clip, *_ = self._apply({"shots": [{"shot": 1, "speaker": "host"}]})
        self.assertEqual([s["spk"] for s in clip["shots"]], ["host", "host"])
        clip, *_ = self._apply({"all_zoom": 1.0})
        self.assertEqual({s["zoom"] for s in clip["shots"]}, {1.0})

    def test_new_title_updates_the_caption_and_linkedin_keeps_credit_and_footer(self) -> None:
        clip, _, applied, _ = self._apply({"title": "Sleep Is A Gift.", "linkedin_hook": "New hook"})
        self.assertEqual(clip["title"], "sleep is a gift")
        self.assertTrue(clip["instagram"].startswith("Sleep is a gift. A clip from my conversation"))
        self.assertEqual(clip["linkedin"].split("\n\n"), [
            "New hook", "Old body one.", "Old body two.",
            "A clip from my conversation with the kind @Guest Person", FOOTER])
        self.assertEqual(applied, ["title 'sleep is not a waste' → 'sleep is a gift'", "LinkedIn hook rewritten"])


class ReviewStateTests(unittest.TestCase):
    def test_review_persists_and_feedback_joins_the_open_round(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ep = _episode(Path(d))
            review_state.request_changes(ep, 3, "too long")
            review_state.request_changes(ep, 3, "and no punch-ins")
            review_state.set_status(ep, 4, "approved")
            state = json.loads((Path(d) / review_state.REVIEW_FILE).read_text(encoding="utf-8"))
        entry = state["clips"]["3"]
        self.assertEqual((entry["status"], entry["version"], len(entry["rounds"])), ("changes", 1, 1))
        self.assertEqual(entry["rounds"][0]["feedback"], "too long\nand no punch-ins")
        self.assertEqual(state["clips"]["4"]["status"], "approved")

    def test_only_clips_with_open_feedback_are_revised(self) -> None:
        clips = [{"number": n} for n in (1, 2, 3, 4)]
        state = {"clips": {"1": {"status": "changes", "version": 1, "rounds": [{"feedback": "x", "version": 1}]},
                           "2": {"status": "changes", "version": 2,
                                 "rounds": [{"feedback": "x", "version": 1, "applied_at": "t"}]},
                           "3": {"status": "approved", "version": 1, "rounds": []}}}
        self.assertEqual(review_state.to_revise(clips, state), [1])

    def test_gate_needs_every_clip_approved_or_dropped(self) -> None:
        clips = [{"number": n} for n in (1, 2)]
        state = {"clips": {"1": {"status": "approved"}, "2": {"status": "pending"}}}
        self.assertFalse(review_state.review_done(clips, state))
        state["clips"]["2"]["status"] = "dropped"
        self.assertTrue(review_state.review_done(clips, state))
        self.assertEqual([c["number"] for c in review_state.kept_clips(clips, state)], [1])
        self.assertFalse(review_state.review_done(clips, {"clips": {"1": {"status": "dropped"},
                                                                    "2": {"status": "dropped"}}}))


class RunTests(unittest.TestCase):
    """The stage end to end with the hub and ffmpeg faked."""

    def _setup(self, d: str) -> tuple[Episode, dict]:
        ep = _episode(Path(d))
        ep.package.mkdir(parents=True)
        clips = [_clip(1), _clip(2, "the bedtime story")]
        for c in clips:
            for rel in c["videos"].values():
                (ep.package / rel).parent.mkdir(parents=True, exist_ok=True)
                (ep.package / rel).write_text("v1", encoding="utf-8")
            cover = ep.package / "clips" / "covers" / f"{c['file']}.png"
            cover.parent.mkdir(parents=True, exist_ok=True)
            cover.write_text("cover v1", encoding="utf-8")
        (ep.package / "clips.json").write_text(json.dumps(clips), encoding="utf-8")
        (ep.package / "transcript").mkdir()
        (ep.package / "transcript" / "clip_words.json").write_text(json.dumps({"1": _words(), "2": _words()}),
                                                                   encoding="utf-8")
        review_state.request_changes(ep, 1, "title: sleep is a gift; 'waste' is wrong")
        review_state.set_status(ep, 2, "approved")
        return ep, {"work_dir": str(Path(d) / "work"), "linkedin_footer": FOOTER, "video_encoder": "libx264"}

    def test_only_the_clip_with_feedback_is_re_rendered_and_its_old_version_kept(self) -> None:
        reply = {"title": "sleep is a gift", "caption_fixes": [{"i": 5, "from": "waste", "to": "gift"}]}
        rendered = []

        def fake_render(ep, clip, layout, words, scratch, out, encoder):
            rendered.append((clip["number"], out.name))
            out.write_text("v2", encoding="utf-8")

        with tempfile.TemporaryDirectory() as d:
            ep, cfg = self._setup(d)
            with mock.patch.object(revise, "ask_json", return_value=reply), \
                    mock.patch.object(revise, "render_clip", side_effect=fake_render), \
                    mock.patch.object(revise, "prepare_fonts"), \
                    mock.patch.object(revise, "load_words", return_value=_words()):
                done = revise.run(ep, cfg, mock.Mock())
            clips = json.loads((ep.package / "clips.json").read_text(encoding="utf-8"))
            words = json.loads((ep.package / "transcript" / "clip_words.json").read_text(encoding="utf-8"))
            state = review_state.load_review(ep)
            v1 = revise.version_dir(ep, 1, 1)
            archived = sorted(p.name for p in v1.iterdir())
            old_files = [(ep.package / "clips" / crop / "sleep is not a waste.mp4").exists() for crop in ("1x1", "9x16")]
            old_cover = (ep.package / "clips" / "covers" / "sleep is not a waste.png").exists()
            untouched = (ep.package / "clips" / "1x1" / "the bedtime story.mp4").read_text(encoding="utf-8")

        self.assertEqual(done, [1])
        self.assertEqual(rendered, [(1, "sleep is a gift.mp4"), (1, "sleep is a gift.mp4")])
        self.assertEqual(clips[0]["file"], "sleep is a gift")
        self.assertEqual(clips[0]["videos"]["1x1"], "clips/1x1/sleep is a gift.mp4")
        self.assertEqual(words["1"][5]["w"], "gift")
        self.assertEqual(archived, ["1x1.mp4", "9x16.mp4", "clip.json", "cover.png"])
        self.assertEqual((old_files, old_cover, untouched), ([False, False], False, "v1"))
        entry = state["clips"]["1"]
        self.assertEqual((entry["status"], entry["version"]), ("pending", 2))
        self.assertEqual(entry["rounds"][0]["produced_version"], 2)
        self.assertIn("caption 'waste' → 'gift'", entry["rounds"][0]["applied"])
        self.assertEqual(state["clips"]["2"]["status"], "approved")

    def test_feedback_added_during_the_run_opens_the_next_round(self) -> None:
        def ask(*_a, **_k):
            review_state.request_changes(ep, 1, "also: no punch-ins")  # the owner types while it runs
            return {}

        with tempfile.TemporaryDirectory() as d:
            ep, cfg = self._setup(d)
            with mock.patch.object(revise, "ask_json", side_effect=ask), \
                    mock.patch.object(revise, "render_clip"), mock.patch.object(revise, "prepare_fonts"), \
                    mock.patch.object(revise, "load_words", return_value=_words()):
                revise.run(ep, cfg, mock.Mock())
            entry = review_state.load_review(ep)["clips"]["1"]
        self.assertEqual((entry["status"], entry["version"], len(entry["rounds"])), ("changes", 2, 2))
        self.assertEqual(entry["rounds"][0]["feedback"], "title: sleep is a gift; 'waste' is wrong")
        self.assertEqual((entry["rounds"][1]["feedback"], entry["rounds"][1]["version"]), ("also: no punch-ins", 2))

    def test_a_failed_clip_keeps_its_feedback_open(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            ep, cfg = self._setup(d)
            with mock.patch.object(revise, "ask_json", side_effect=RuntimeError("hub down")), \
                    mock.patch.object(revise, "prepare_fonts"), \
                    mock.patch.object(revise, "load_words", return_value=_words()), \
                    self.assertRaises(RuntimeError):
                revise.run(ep, cfg, mock.Mock())
            entry = review_state.load_review(ep)["clips"]["1"]
        self.assertEqual((entry["status"], entry["version"]), ("changes", 1))
        self.assertIsNotNone(review_state.open_round(entry))


class GateTests(unittest.TestCase):
    def test_covers_and_package_wait_for_the_review_unless_overridden(self) -> None:
        ran = []
        with tempfile.TemporaryDirectory() as d:
            ep = _episode(Path(d))
            ep.package.mkdir(parents=True)
            (ep.package / "clips.json").write_text(json.dumps([_clip(1)]), encoding="utf-8")
            stages = {name: ((lambda name: lambda *a: ran.append(name))(name), lambda e: False)
                      for name in ("covers", "package")}
            with mock.patch.dict(podcast_pipeline.STAGES, stages, clear=True), \
                    mock.patch.object(podcast_pipeline, "load_podcast_config", return_value={}), \
                    mock.patch.object(podcast_pipeline, "load_episode", return_value=ep), \
                    mock.patch("podcast.report.write_report", return_value=Path(d) / "m.md"):
                podcast_pipeline.main([d, "--stages", "covers,package"])
                self.assertEqual(ran, [])
                podcast_pipeline.main([d, "--stages", "covers,package", "--unreviewed"])
                self.assertEqual(ran, ["covers", "package"])
                review_state.set_status(ep, 1, "approved")
                podcast_pipeline.main([d, "--stages", "covers,package"])
        self.assertEqual(ran, ["covers", "package", "covers", "package"])


if __name__ == "__main__":
    unittest.main()
