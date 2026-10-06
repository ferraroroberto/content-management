"""Example demo driver: a disposable facilitation-suite with its synthetic demo session.

The worked example for docs/demo-video-driver-playbook.md. A driver is a
throwaway file that lives in the demo folder next to demo.json; this one is
kept in the repo only as the reference. It never touches the live app: it
starts its own server on a free port with temp config, ledger and data, from
the app's own checkout and venv (``repo`` in ``driver_args``), and builds the
app's synthetic demo session (``tests/fixtures/demo.py``) — no real data.

``driver_args``:

- ``repo``: the facilitation-suite checkout (its ``.venv`` runs the server).
- ``include_all``: drop ``include: false`` from every plan item, so the live
  numbering the ``goto`` actions use matches the plan (a skipped item shifts
  every later number — the reference run went wrong exactly there).
- ``timers``: ``{"<item number>": {"seconds": 14, "end": "keep", "start": "manual"}}``
  — shorten a timer so a clock visibly runs out; ``start: manual`` so a
  scripted start/pause/resume isn't fighting a timer that starts on entering.
- ``shuffle_seed``: shuffle the groups (the synthetic session ships unshuffled).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

logger = logging.getLogger("demo_video.driver.facilitation_suite")

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
LOOP_FACTORY = "app.webapp.event_loop:selector_loop_factory"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _session_id(folder: Path) -> str:
    """facilitation-suite's own id rule (src/sessions/store.py session_id)."""
    return hashlib.sha1(os.path.normcase(os.path.abspath(str(folder))).encode("utf-8")).hexdigest()[:10]


class FacilitationSuiteDriver:
    def __init__(self, args: dict) -> None:
        self.repo = Path(args["repo"])
        if not (self.repo / "app" / "webapp").is_dir():
            raise RuntimeError(f"driver_args.repo must point at a facilitation-suite checkout (got {args['repo']!r})")
        self.python = self.repo / ".venv" / "Scripts" / "python.exe"
        if not self.python.is_file():
            self.python = self.repo / ".venv" / "bin" / "python"
        self.args = args
        self.proc: Optional[subprocess.Popen] = None
        self.base = ""
        self.session = ""
        self.http = requests.Session()
        self.rows = 0

    # --------------------------------------------------------------- lifecycle

    def boot(self, workdir: Path, lang: str) -> dict:
        root = workdir / "fs"
        folder = root / "sessions" / "demo"
        ledger = root / "sessions.local.yaml"
        folder.parent.mkdir(parents=True, exist_ok=True)
        env = self._env(root)
        subprocess.run([str(self.python), "-m", "tests.fixtures.demo", "--root", str(folder), "--ledger", str(ledger)],
                       cwd=self.repo, env=env, check=True, capture_output=True, creationflags=NO_WINDOW)
        self.session = _session_id(folder)
        self._patch_plan(folder / "session.yaml")
        port = _free_port()
        cfg = json.loads((self.repo / "config" / "config.sample.json").read_text(encoding="utf-8"))
        cfg.update(port=port, session_root=str(root / "sessions"))
        cfg["obs"]["enabled"] = False
        cfg["reader"]["enabled"] = False
        cfg["quiz"]["public_port"] = 0
        (root / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        log = open(root / "server.log", "wb")  # noqa: SIM115 — closed in stop()
        self.proc = subprocess.Popen(
            [str(self.python), "-m", "uvicorn", "app.webapp.server:app", "--host", "127.0.0.1", "--port", str(port),
             "--loop", LOOP_FACTORY, "--log-level", "warning"],
            cwd=self.repo, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        self.proc.fs_log = log  # type: ignore[attr-defined]
        self.base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"facilitation-suite exited early; see {root / 'server.log'}")
            try:
                if self.http.get(self.base + "/healthz", timeout=1).ok:
                    break
            except requests.RequestException:
                time.sleep(0.25)
        else:
            raise RuntimeError(f"facilitation-suite did not answer /healthz within 30 s; see {root / 'server.log'}")
        if self.args.get("shuffle_seed") is not None:
            r = self.http.post(f"{self.base}/api/sessions/{self.session}/groups/shuffle",
                               json={"seed": self.args["shuffle_seed"]}, timeout=10)
            r.raise_for_status()
        return {"base_url": self.base, "session": self.session}

    def stop(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc.fs_log.close()  # type: ignore[attr-defined]
        self.proc = None

    def _env(self, root: Path) -> dict:
        env = os.environ.copy()
        env.update(FS_CONFIG_PATH=str(root / "config.json"), FS_LEDGER_PATH=str(root / "sessions.local.yaml"),
                   FS_DATA_DIR=str(root / "data"), FS_ENV_PATH=str(root / ".env"), PYTHONUTF8="1")
        for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_DEVICE_NAME"):
            env.pop(key, None)
        return env

    def _patch_plan(self, path: Path) -> None:
        plan = yaml.safe_load(path.read_text(encoding="utf-8"))
        items = [it for sec in plan["sections"] for it in sec["items"]]
        if self.args.get("include_all"):
            for it in items:
                it.pop("include", None)
        for number, timer in (self.args.get("timers") or {}).items():
            items[int(number) - 1].setdefault("timer", {}).update(timer)
        path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # --------------------------------------------------------------- actions

    def act(self, name: str, arg: Any = None) -> None:
        path = f"/api/actions/{name}" + (f"/{arg}" if arg is not None else "")
        r = self.http.post(self.base + path, headers={"X-Automation-Source": "demo"}, timeout=10)
        if not r.ok:
            raise RuntimeError(f"action {path} refused: {r.status_code} {r.text[:200]}")

    def chat(self, sender: str, text: str) -> None:
        """Post one message the way the Zoom chat reader does (no 'sim' chip on the presenter)."""
        self.rows += 1
        body = {"batch": f"demo-{self.rows}", "baseline": False, "source": "zoom",
                "messages": [{"sender": sender, "text": text, "time": time.strftime("%H:%M")}]}
        self.http.post(self.base + "/api/chat/messages", json=body, timeout=10).raise_for_status()

    def tick(self) -> None:
        """Keep the presenter's 'Zoom chat · reading' chip green."""
        self.http.post(self.base + "/api/chat/heartbeat", timeout=10,
                       json={"state": "reading", "detail": "Reading the meeting chat", "rows": self.rows})


def make_driver(args: dict) -> FacilitationSuiteDriver:
    return FacilitationSuiteDriver(args)
