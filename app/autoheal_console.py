r"""Visible-console wrapper that runs the ``/schedule-autoheal`` skill headless.

The planning tab spawns this script with ``CREATE_NEW_CONSOLE`` so it owns a
real, visible window the user can watch. Inside that window it runs::

    claude -p "/schedule-autoheal <args>" --model <m>
        --permission-mode bypassPermissions --verbose

and **tees** the agent's combined output to BOTH its own console (live, what the
user watches) and a log file the Streamlit app tails into the in-app panel.

Two details are load-bearing, both learned from the app-launcher sister project
(``codebase-audit-fleet`` job, ``docs/lessons-launcher-owned-pty.md``):

- ``--verbose`` makes ``claude -p`` stream turn-by-turn activity instead of
  buffering one silent line until the final result flush — without it the
  window looks dead for minutes.
- ``--permission-mode bypassPermissions`` lets the unattended heal edit files,
  run ``gh``, and call MCP tools with no human at the prompt.

Run standalone via ``launch_autoheal.bat`` or as
``python -m app.autoheal_console --log <path> --skill-cmd "/schedule-autoheal all --dry-run"``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as a script (python app/autoheal_console.py via the app or launch_autoheal.bat),
# so sys.path[0] is app/, not the repo root — without this the `from config.console`
# import below raises ModuleNotFoundError and the wrapper dies before opening its log.
# Mirrors app/app.py's own sys.path bootstrap.
sys.path.insert(0, str(REPO_ROOT))
DEFAULT_MODEL = "claude-opus-4-8"


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compact(value: Any, limit: int = 220) -> str:
    try:
        s = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(value)
    return _truncate(s, limit)


def _format_event(line: str) -> Optional[str]:
    """Turn one ``--output-format stream-json`` line into a readable feed line.

    ``claude -p --verbose`` (plain text) block-buffers and flushes everything at
    exit — the console looks dead the whole run. ``--output-format stream-json``
    emits one JSON event the instant it happens (init, each assistant
    thinking/text/tool_use block, each tool_result, the final result), so we get
    true live progress. We render each event compactly; unrecognised lines are
    passed through raw so banners / errors still show. Returns ``None`` for
    events not worth a line (e.g. rate-limit pings).
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return line  # non-JSON (a banner or an arg error) — show it verbatim

    etype = obj.get("type")
    if etype == "system" and obj.get("subtype") == "init":
        return f"▶ claude session {str(obj.get('session_id', ''))[:8]} · model {obj.get('model', '')}"
    if etype == "assistant":
        out: list[str] = []
        for block in obj.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "thinking":
                think = block.get("thinking", "").strip()
                if think:
                    out.append(f"💭 {_truncate(think, 300)}")
            elif bt == "text":
                text = block.get("text", "").strip()
                if text:
                    out.append(text)
            elif bt == "tool_use":
                out.append(f"🔧 {block.get('name', '?')}({_compact(block.get('input', {}))})")
        return "\n".join(out) if out else None
    if etype == "user":
        out = []
        for block in obj.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                content = str(content).strip()
                if content:
                    out.append(f"   ↳ {_truncate(content, 300)}")
        return "\n".join(out) if out else None
    if etype == "result":
        dur = obj.get("duration_ms")
        suffix = f" · {dur} ms" if dur else ""
        result = (obj.get("result") or "").strip()
        head = f"✅ result: {obj.get('subtype', '')}{suffix}"
        return f"{head}\n{result}" if result else head
    return None


def _emit(fh: Any, text: str) -> None:
    """Write one line to both the log file and this process's console."""
    data = f"{text}\n".encode("utf-8", "replace")
    fh.write(data)
    fh.flush()
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        try:
            buf.write(data)
            buf.flush()
        except (OSError, ValueError):
            pass


def run(skill_cmd: str, log_path: Path, model: str, claude_exe: str,
        remote_control: bool = True, remote_name: str = "autoheal") -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        claude_exe, "-p", skill_cmd,
        "--model", model,
        "--permission-mode", "bypassPermissions",
        # stream-json (requires --verbose) flushes one event per step in realtime,
        # unlike plain text which buffers until exit. We pretty-print each event.
        "--output-format", "stream-json",
        "--verbose",
    ]
    if remote_control:
        # Surfaces the run in the Claude mobile/web app so it can be watched and
        # driven remotely (e.g. answering the "not confident" escalation from the
        # phone instead of only via Slack). Optional name makes it findable.
        argv += ["--remote-control", remote_name]
    with log_path.open("wb") as fh:
        _emit(fh, f"$ {' '.join(argv)}")
        _emit(fh, f"# started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        _emit(fh, "")
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except OSError as exc:
            _emit(fh, f"[autoheal spawn error] {exc} — is the `claude` CLI on PATH?")
            return 127
        assert proc.stdout is not None
        # readline streams each JSON event as claude emits it (no 4 KB buffering).
        for raw in iter(proc.stdout.readline, b""):
            text = _format_event(raw.decode("utf-8", "replace").rstrip("\n"))
            if text:
                _emit(fh, text)
        rc = proc.wait()
        _emit(fh, f"\n# exit code {rc}  ·  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return rc


def main() -> int:
    from config.console import force_utf8_stdio
    force_utf8_stdio()

    parser = argparse.ArgumentParser(description="Run /schedule-autoheal headless in a visible console.")
    parser.add_argument("--skill-cmd", required=True,
                        help='full slash command, e.g. "/schedule-autoheal all --dry-run"')
    parser.add_argument("--log", default=None,
                        help="path to tee the combined output to (default: results/planning/autoheal-<ts>.log)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--claude-exe", default="claude", help="claude CLI executable (default: on PATH)")
    parser.add_argument("--no-remote-control", action="store_true",
                        help="disable Remote Control (default: enabled, so the run is viewable from the Claude mobile app)")
    parser.add_argument("--remote-name", default="autoheal", help="Remote Control session name")
    args = parser.parse_args()
    if args.log:
        log_path = Path(args.log)
    else:
        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        log_path = REPO_ROOT / "results" / "planning" / f"autoheal-{ts}.log"
    return run(args.skill_cmd, log_path, args.model, args.claude_exe,
               remote_control=not args.no_remote_control, remote_name=args.remote_name)


if __name__ == "__main__":
    raise SystemExit(main())
