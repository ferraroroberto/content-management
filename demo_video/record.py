"""The record stage: drive an app through scripted beats and film it (issue #361).

The harness knows nothing about any app. A **driver** — a throwaway Python
file in the demo folder, written per demo from
``docs/demo-video-driver-playbook.md`` — boots a disposable instance of the
app and performs its actions; the harness owns the browser, the beat runner,
the chat pacing and the marks:

- every take opens its pages, each in its own browser context, so each page
  becomes one video file ``rec/<lang>/<page>.webm``;
- every beat is marked ``[start, end]`` in seconds **per page video**,
  measured from that page's own creation, into the marks file;
- the driver is stopped even when a step fails.

A driver module defines ``make_driver(args: dict) -> Driver`` returning an
object with::

    boot(workdir: Path, lang: str) -> dict   # {"base_url": ..., <url placeholders>}
    stop() -> None
    act(name: str, arg) -> None              # raise on a refused action — never log-and-continue
    chat(sender: str, text: str) -> None     # optional: post one chat message
    tick() -> None                           # optional: ~once a second during waits
"""

from __future__ import annotations

import importlib.util
import json
import logging
import random
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from demo_video.storyboard import STATES_KEY, Beat, Demo, Recording, Step, Take

logger = logging.getLogger("demo_video.record")


class Driver(Protocol):
    def boot(self, workdir: Path, lang: str) -> dict: ...
    def stop(self) -> None: ...
    def act(self, name: str, arg: Any) -> None: ...


def load_driver(path: Path, args: dict) -> Driver:
    """Import a driver file from the demo folder and call its ``make_driver(args)``."""
    spec = importlib.util.spec_from_file_location(f"demo_driver_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load driver {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "make_driver"):
        raise RuntimeError(f"driver {path} has no make_driver(args)")
    return module.make_driver(args)


class Runner:
    """Runs setup steps and beats against a driver and pages; records the marks.

    ``pages`` maps a page role to an object with Playwright's ``click``,
    ``locator``, ``wait_for_selector`` and ``mouse`` (tests pass fakes);
    ``t0`` maps a page role to its creation time on ``clock``.
    """

    def __init__(self, driver: Any, copy: dict, recording: Recording, *,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.driver, self.copy, self.rec = driver, copy, recording
        self.clock, self.sleep = clock, sleep
        self.n = 0  # chat messages posted so far, across takes (the recorder's sender rule)
        self.pages: dict = {}
        senders = self.text(recording.senders)
        self.senders = senders if isinstance(senders, list) else [senders]

    def text(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("@"):
            key = value[1:]
            if key not in self.copy:
                raise KeyError(f"recording uses copy key {value!r}, missing for this language")
            return self.copy[key]
        return value

    def keepalive(self) -> None:
        """A no-op round trip to every page of the take.

        Without Playwright traffic for a page, Chrome delivered no new
        screencast frames for it (measured: a presenter page recorded 1.1 s
        of an 8 s take beside a second page, while its DOM kept changing);
        an ``evaluate`` per page every quarter second keeps every video at
        full length.
        """
        for page in self.pages.values():
            evaluate = getattr(page, "evaluate", None)
            if evaluate:
                evaluate("0")

    def pause(self, seconds: float) -> None:
        """Wait, keeping every page's video alive and calling the driver's ``tick`` about once a second."""
        end = self.clock() + seconds
        tick = getattr(self.driver, "tick", None)
        next_tick = self.clock()
        while (left := end - self.clock()) > 0:
            if tick and self.clock() >= next_tick:
                tick()
                next_tick = self.clock() + 1.0
            self.keepalive()
            self.sleep(min(0.25, left))

    def step(self, st: Step, pages: dict, beat: str) -> None:
        page = pages.get(st.page) if st.page else None
        if st.page and page is None:
            raise KeyError(f"step uses page {st.page!r}, not in this take")
        if st.act is not None:
            self.driver.act(st.act, st.arg)
        elif st.wait is not None:
            self.pause(st.wait)
        elif st.chat is not None:
            messages = self.text(st.chat)
            rnd = random.Random(beat)
            for text in messages:
                self.n += 1
                sender = self.senders[(self.n * self.rec.sender_step) % len(self.senders)] if self.senders else ""
                self.driver.chat(sender, text)
                gap = st.every_s if isinstance(st.every_s, (int, float)) else rnd.uniform(*st.every_s)
                self.pause(gap)
        elif st.click is not None:
            if st.text:
                row = page.locator(st.click, has_text=st.text).first
                row.scroll_into_view_if_needed()
                self.pause(0.8)
                row.click()
            else:
                page.click(st.click)
        elif st.wait_for is not None:
            page.wait_for_selector(st.wait_for)
        elif st.wheel is not None:
            for _ in range(st.times):
                page.mouse.wheel(*st.wheel)
                self.pause(st.every_s if isinstance(st.every_s, (int, float)) else st.every_s[0])
        elif st.mouse is not None:
            page.mouse.move(*st.mouse)

    def run(self, take: Take, pages: dict, t0: dict[str, float]) -> dict[str, dict[str, list[float]]]:
        """Setup, then every beat; returns ``{page: {beat: [start, end]}}`` for this take."""
        self.pages = pages
        for st in take.setup:
            self.step(st, pages, "_setup")
        marks: dict[str, dict[str, list[float]]] = {role: {} for role in pages}
        for beat in take.beats:
            logger.info("ℹ️ %s: beat %s", take.id, beat.name)
            start = self.clock()
            for st in beat.steps:
                self.step(st, pages, beat.name)
            end = self.clock()
            for role in pages:
                marks[role][beat.name] = [round(start - t0[role], 2), round(end - t0[role], 2)]
        return marks


def _fill(template: Optional[str], values: dict) -> Optional[str]:
    return template.format(**values) if template else template


def record_take(browser: Any, take: Take, runner: Runner, values: dict, rec: Recording, out_dir: Path) -> dict:
    """Open the take's pages (one context each), run it, save ``<page>.webm`` per page."""
    size = {"width": rec.viewport.width, "height": rec.viewport.height}
    raw = Path(tempfile.mkdtemp(prefix="demo-rec-"))
    contexts, pages, t0 = {}, {}, {}
    try:
        for role, spec in take.pages.items():
            ctx = browser.new_context(viewport=size, device_scale_factor=1, record_video_dir=str(raw),
                                      record_video_size=size, locale="en-US", color_scheme="light")
            if spec.init_script:
                ctx.add_init_script(_fill(spec.init_script, values))
            page = ctx.new_page()
            t0[role] = time.monotonic()
            page.goto(values["base_url"].rstrip("/") + _fill(spec.url, values))
            if spec.wait_for:
                page.wait_for_selector(spec.wait_for)
            contexts[role], pages[role] = ctx, page
        marks = runner.run(take, pages, t0)
    finally:
        videos = {role: p.video for role, p in pages.items()}
        for ctx in contexts.values():
            ctx.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    for role, video in videos.items():
        if video is not None:
            video.save_as(out_dir / f"{role}.webm")
    shutil.rmtree(raw, ignore_errors=True)
    return marks


def run_recording(demo: Demo, folder: Path, lang: str) -> Path:
    """Record every take for ``lang``; writes ``rec/<lang>/*.webm`` and that language's marks file."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    from config.chrome_launch import doc_capture_launch_kwargs  # noqa: PLC0415

    rec = demo.recording
    if rec is None:
        raise RuntimeError("demo.json has no `recording` block")
    driver = load_driver(folder / rec.driver, rec.driver_args)
    out_dir = folder / "rec" / lang
    work = Path(tempfile.mkdtemp(prefix=f"demo-{lang}-"))
    marks: dict[str, Any] = {}
    try:
        values = driver.boot(work, lang)
        logger.info("ℹ️ driver booted a disposable instance at %s", values.get("base_url"))
        runner = Runner(driver, demo.copy_.get(lang, {}), rec)
        with sync_playwright() as p:
            browser = p.chromium.launch(**doc_capture_launch_kwargs(headless=rec.headless))
            try:
                for take in rec.takes:
                    logger.info("▶ take %s (%s)", take.id, ", ".join(take.pages))
                    for role, beats in record_take(browser, take, runner, values, rec, out_dir).items():
                        marks.setdefault(role, {}).update(beats)
            finally:
                browser.close()
    finally:
        driver.stop()
        shutil.rmtree(work, ignore_errors=True)
    path = folder / demo.marks.replace("{lang}", lang)
    old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if STATES_KEY in old:
        logger.info("ℹ️ dropping measured states from the old marks — prep measures them again")
    path.write_text(json.dumps(marks, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    logger.info("✅ recorded %s: %s → %s", lang, ", ".join(sorted(marks)), out_dir)
    return path


def beats_in_order(marks: dict, take: Take) -> list[str]:
    """Problems with a take's marks: a missing beat, ``end ≤ start``, or beats out of order."""
    problems = []
    for role in take.pages:
        last = -1.0
        for beat in take.beats:
            span = marks.get(role, {}).get(beat.name)
            if span is None:
                problems.append(f"{role}: beat {beat.name} missing")
                continue
            if span[1] <= span[0]:
                problems.append(f"{role}: beat {beat.name} ends before it starts")
            if span[0] < last:
                problems.append(f"{role}: beat {beat.name} starts before the previous one ended")
            last = span[1]
    return problems


__all__ = ["Beat", "Runner", "load_driver", "record_take", "run_recording", "beats_in_order"]
