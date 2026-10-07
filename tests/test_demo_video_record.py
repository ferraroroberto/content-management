"""Tests for the demo-video recorder's beat runner (issue #361) — fake driver, fake pages, no browser."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from demo_video.record import Runner, load_driver  # noqa: E402
from demo_video.storyboard import Recording, Step  # noqa: E402

EXAMPLE = REPO_ROOT / "demo_video" / "examples" / "facilitation-suite"


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def act(self, name, arg=None) -> None:
        self.calls.append(("act", name, arg))

    def chat(self, sender, text) -> None:
        self.calls.append(("chat", sender, text))

    def tick(self) -> None:
        self.calls.append(("tick",))


class FakeMouse:
    def __init__(self, log) -> None:
        self.log = log

    def wheel(self, dx, dy) -> None:
        self.log.append(("wheel", dx, dy))

    def move(self, x, y) -> None:
        self.log.append(("move", x, y))


class FakePage:
    def __init__(self) -> None:
        self.log: list[tuple] = []
        self.mouse = FakeMouse(self.log)

    def click(self, sel) -> None:
        self.log.append(("click", sel))

    def wait_for_selector(self, sel) -> None:
        self.log.append(("wait_for", sel))

    def evaluate(self, js) -> None:
        self.log.append(("evaluate", js))


def _recording(takes: list[dict]) -> Recording:
    return Recording.model_validate({"driver": "driver.py", "senders": "@people", "takes": takes})


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.driver = FakeDriver()
        self.copy = {"people": ["A", "B", "C"], "answers": ["one", "two", "three"]}

    def _runner(self, rec: Recording) -> Runner:
        return Runner(self.driver, self.copy, rec, clock=self.clock.now, sleep=self.clock.sleep)

    def test_marks_have_every_beat_in_order_with_monotonic_times_per_page(self) -> None:
        rec = _recording([{"id": "live", "pages": {"presenter": {"url": "/p"}, "stage": {"url": "/s"}},
                           "setup": [{"act": "clock_start"}, {"wait": 2}],
                           "beats": [{"name": "map", "steps": [{"act": "goto", "arg": 2}, {"wait": 1.5},
                                                               {"chat": "@answers", "every_s": 0.5}]},
                                     {"name": "scale", "steps": [{"act": "goto", "arg": 4}, {"wait": 3}]}]}])
        take = rec.takes[0]
        t0 = {"presenter": 100.0, "stage": 100.5}  # the stage page opened half a second later
        marks = self._runner(rec).run(take, {"presenter": FakePage(), "stage": FakePage()}, t0)
        self.assertEqual(list(marks["presenter"]), ["map", "scale"])
        self.assertEqual(marks["presenter"]["map"], [2.0, 5.0])  # after the 2 s setup; 1.5 s + 3 × 0.5 s
        self.assertEqual(marks["stage"]["map"], [1.5, 4.5])  # same instant, measured from its own page
        self.assertEqual(marks["presenter"]["scale"], [5.0, 8.0])

    def test_chat_senders_follow_the_recorder_rule_across_beats(self) -> None:
        rec = _recording([{"id": "t", "pages": {"p": {"url": "/"}},
                           "beats": [{"name": "a", "steps": [{"chat": "@answers", "every_s": 0.1}]},
                                     {"name": "b", "steps": [{"chat": ["four"], "every_s": 0.1}]}]}])
        self._runner(rec).run(rec.takes[0], {"p": FakePage()}, {"p": 100.0})
        chats = [c[1:] for c in self.driver.calls if c[0] == "chat"]
        people = self.copy["people"]
        self.assertEqual(chats, [(people[(n * 7) % 3], t) for n, t in enumerate(["one", "two", "three", "four"], 1)])

    def test_waits_tick_the_driver_and_browser_steps_reach_their_page(self) -> None:
        rec = _recording([{"id": "t", "pages": {"app": {"url": "/"}},
                           "beats": [{"name": "tour", "steps": [{"page": "app", "click": "#tabPlan"},
                                                                {"page": "app", "wheel": [0, 160], "times": 3, "every_s": 0.2},
                                                                {"page": "app", "mouse": [700, 600]}, {"wait": 2.5}]}]}])
        page = FakePage()
        self._runner(rec).run(rec.takes[0], {"app": page}, {"app": 100.0})
        self.assertEqual(page.log[0], ("click", "#tabPlan"))
        self.assertEqual(sum(1 for e in page.log if e[0] == "wheel"), 3)
        self.assertGreaterEqual(sum(1 for c in self.driver.calls if c[0] == "tick"), 3)
        # waits keep the page's video alive: an evaluate at least every quarter second
        self.assertGreaterEqual(sum(1 for e in page.log if e[0] == "evaluate"), 10)

    def test_a_missing_copy_key_or_page_fails_loudly(self) -> None:
        rec = _recording([{"id": "t", "pages": {"p": {"url": "/"}},
                           "beats": [{"name": "a", "steps": [{"chat": "@nope"}]}]}])
        with self.assertRaises(KeyError):
            self._runner(rec).run(rec.takes[0], {"p": FakePage()}, {"p": 100.0})
        rec = _recording([{"id": "t", "pages": {"p": {"url": "/"}},
                           "beats": [{"name": "a", "steps": [{"page": "other", "click": "#x"}]}]}])
        with self.assertRaises(KeyError):
            self._runner(rec).run(rec.takes[0], {"p": FakePage()}, {"p": 100.0})

    def test_a_step_must_have_exactly_one_kind(self) -> None:
        with self.assertRaises(ValidationError):
            Step.model_validate({"act": "next", "wait": 1})
        with self.assertRaises(ValidationError):
            Step.model_validate({"click": "#x"})  # a browser step needs its page
        with self.assertRaises(ValidationError):
            Step.model_validate({})


class ExampleTests(unittest.TestCase):
    def test_the_example_driver_loads_and_its_recording_validates(self) -> None:
        data = json.loads((EXAMPLE / "demo.json").read_text(encoding="utf-8"))
        rec = Recording.model_validate(data["recording"])
        self.assertEqual([t.id for t in rec.takes], ["app-tour", "live", "results"])
        repo = Path(tempfile.mkdtemp())
        (repo / "app" / "webapp").mkdir(parents=True)
        driver = load_driver(EXAMPLE / rec.driver, {"repo": str(repo)})
        for method in ("boot", "stop", "act", "chat", "tick"):
            self.assertTrue(callable(getattr(driver, method)), method)
        with self.assertRaises(RuntimeError):  # the committed placeholder fails loudly, not deep in boot()
            load_driver(EXAMPLE / rec.driver, rec.driver_args)


if __name__ == "__main__":
    unittest.main()
