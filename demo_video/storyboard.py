"""The ``demo.json`` storyboard: schema, validation and per-cut resolution.

A demo folder holds ``demo.json``, its marks file (beat start/end seconds per
recorded video), a media folder (clips + music) and ``out/``. ``demo.json``
says *what* to show; ``resolve_cut`` turns it into the props the Remotion
template renders, with every copy key replaced by its string and every clip
ref replaced by ``{src, from, rate, crop}``. The template never reads
``demo.json`` itself.

Conventions:

- A string starting with ``@`` anywhere in a scene is a copy key, looked up in
  ``copy[<cut language>]`` (the value may be a string or a list).
- A clip ref is ``{"video": <clip id>, "beat": <beat>, "offset": s, "rate": r}``:
  it starts ``offset`` seconds after the beat's start in that video's marks
  (or after the video's start when ``beat`` is omitted).
- ``{lang}`` in a clip file or the marks path is the cut's language.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

ASPECTS: dict[str, tuple[int, int]] = {"16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
DEMO_FILE = "demo.json"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Crop(_Model):
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)


class ClipRef(_Model):
    video: str
    beat: Optional[str] = None
    offset: float = 0.0
    rate: float = Field(1.0, gt=0)
    crop: Optional[Crop] = None


class Caption(_Model):
    kicker: Optional[str] = None
    title: str
    sub: Optional[str] = None
    color: Optional[str] = None
    size: Optional[float] = None
    width: Optional[float] = None


Text = Union[str, list[Any]]


class _Scene(_Model):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    seconds: float = Field(gt=0)
    fade_s: float = Field(0.27, ge=0)


class Chat(_Model):
    messages: Text
    senders: Optional[Text] = None
    sender_step: int = 7
    every_s: float = Field(0.4, gt=0)
    start_s: float = Field(0.13, ge=0)
    max: int = Field(4, ge=1)


class FullBleed(_Scene):
    type: Literal["full_bleed"]
    clip: ClipRef
    caption: Caption
    zoom: tuple[float, float] = (1.0, 1.07)
    focus: str = "40% 55%"
    chat: Optional[Chat] = None


class TilesStatement(_Scene):
    type: Literal["tiles_statement"]
    tiles: Text
    chats: Text = Field(default_factory=list)
    talkers: list[int] = Field(default_factory=list)
    before: Caption
    after: Caption
    swap_s: float = Field(gt=0)


class TitleCard(_Scene):
    type: Literal["title_card"]
    caption: Caption


class Brand(_Scene):
    type: Literal["brand"]
    sub: str = ""


Device = Literal["laptop", "monitor", "window"]


class StepsDevice(_Scene):
    type: Literal["steps_device"]
    steps: Text
    step_at: list[ClipRef] = Field(default_factory=list)
    device: Device = "laptop"
    clip: ClipRef


class Framed(_Model):
    label: str = ""
    clip: ClipRef


class SplitWindowMonitor(_Scene):
    type: Literal["split_window_monitor"]
    caption: Caption
    window: Framed
    monitor: Framed


class CropZoom(_Scene):
    type: Literal["crop_zoom"]
    caption: Caption
    panel: ClipRef
    window: ClipRef


class SideBySide(_Scene):
    type: Literal["side_by_side"]
    caption: Caption
    windows: list[ClipRef] = Field(min_length=1)
    stagger_s: float = Field(0.67, ge=0)


class DeviceCaption(_Scene):
    type: Literal["device_caption"]
    caption: Caption
    device: Device = "laptop"
    clip: ClipRef


class Segment(_Model):
    clip: ClipRef
    seconds: Optional[float] = Field(None, gt=0)  # None = until the scene ends


class Measure(_Model):
    """Where to read the state on the recording: a box in source px and one colour per state."""
    box: Crop
    palette: list[str]  # "#rrggbb", in the legend's state order
    step_s: float = Field(0.5, gt=0)


class Legend(_Model):
    labels: Text
    colors: list[str]
    segment: int = Field(0, ge=0)
    anchor: ClipRef  # the beat whose start the change times count from
    changes: Optional[list[tuple[float, int]]] = None  # typed by hand, or …
    measure: Optional[Measure] = None  # … measured by the prep stage into the marks file


class WindowStates(_Scene):
    type: Literal["window_states"]
    caption: Caption
    segments: list[Segment] = Field(min_length=1)
    legend: Legend
    speed_chip: bool = True


class Outro(_Scene):
    type: Literal["outro"]
    clip: ClipRef
    tagline: str
    footer: str = ""


Scene = Annotated[
    Union[FullBleed, TilesStatement, TitleCard, Brand, StepsDevice, SplitWindowMonitor, CropZoom, SideBySide,
          DeviceCaption, WindowStates, Outro],
    Field(discriminator="type"),
]


class Anchor(_Model):
    scene: str
    offset_s: float = 0.0


class Track(_Model):
    file: str
    licence: Literal["free", "private-only"]
    start: Union[Anchor, float] = 0.0
    end: Union[Anchor, Literal["end"]] = "end"
    source_start_s: Optional[float] = Field(None, ge=0)
    align_end_tail_s: Optional[float] = Field(None, ge=0)  # trim so the track ends this long before its own end
    volume: float = Field(0.9, ge=0, le=1)
    fade_in_s: float = Field(0.5, ge=0)
    fade_out_s: float = Field(2.5, ge=0)


class Cut(_Model):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    lang: str
    aspect: Literal["16:9", "1:1", "4:5"] = "16:9"
    public: bool = False
    soundtrack: list[Track] = Field(default_factory=list)


class ClipSpec(_Model):
    file: str  # relative to the media folder; may contain {lang}
    marks: Optional[str] = None  # this video's key in the marks file (default: the clip id)
    source: Optional[str] = None  # the raw recording (relative to the demo folder) prep transcodes into `file`


class Privacy(_Model):
    roster: Optional[str] = None  # real names, one per line (.txt/.csv) or a "name" column (.xlsx); never committed
    blocklist: list[str] = Field(default_factory=list)  # terms a public cut must not show (client names, …)
    scan: list[str] = Field(default_factory=list)  # extra files to scan too (chat scripts, seed data)
    allow: list[str] = Field(default_factory=list)  # name tokens the owner accepted (e.g. a common first name)


class Product(_Model):
    name: str
    icon_paths: list[str] = Field(default_factory=list)  # 24×24 SVG path data, Lucide-style strokes
    colors: tuple[str, str] = ("#e53935", "#8e24aa")


class Demo(_Model):
    version: Literal[1] = 1
    title: str
    fps: int = Field(30, ge=1, le=60)
    product: Product
    media_dir: str = "media"
    marks: str = "marks.json"
    clips: dict[str, ClipSpec]
    copy_: dict[str, dict[str, Any]] = Field(alias="copy")
    cuts: list[Cut] = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)
    privacy: Privacy = Field(default_factory=Privacy)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def cut(self, cut_id: str) -> Cut:
        for c in self.cuts:
            if c.id == cut_id:
                return c
        raise KeyError(f"no cut {cut_id!r} (cuts: {', '.join(c.id for c in self.cuts)})")


def load_demo(folder: Path) -> Demo:
    """Parse ``<folder>/demo.json``; raises ``pydantic.ValidationError`` on a bad storyboard."""
    return Demo.model_validate(json.loads((folder / DEMO_FILE).read_text(encoding="utf-8")))


def load_marks(folder: Path, demo: Demo, lang: str) -> dict[str, dict[str, list[float]]]:
    """``{video: {beat: [start, end]}}`` for one language; ``{}`` when the file is absent."""
    path = folder / demo.marks.replace("{lang}", lang)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


STATES_KEY = "_states"


def states_key(ref: ClipRef) -> str:
    return f"{ref.video}:{ref.beat or ''}"


def media_root(folder: Path, demo: Demo) -> Path:
    return folder / demo.media_dir


def _walk(value: Any, path: str = ""):
    """Yield ``(path, value)`` for every leaf and dict in a nested structure."""
    yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def _is_clip(value: Any) -> bool:
    return isinstance(value, dict) and "video" in value and "rate" in value


def validate(demo: Demo, folder: Path) -> list[str]:
    """Cross-reference checks the schema can't do: copy keys, clips, beats, anchors.

    Returns a list of problems (empty = valid). Media files are checked by
    ``missing_media`` instead, so a storyboard can be validated before recording.
    """
    errors: list[str] = []
    scene_ids = [s.id for s in demo.scenes]
    dupes = {i for i in scene_ids if scene_ids.count(i) > 1}
    if dupes:
        errors.append(f"duplicate scene ids: {sorted(dupes)}")
    cut_ids = [c.id for c in demo.cuts]
    if len(set(cut_ids)) != len(cut_ids):
        errors.append("duplicate cut ids")
    dumped = [s.model_dump(mode="json") for s in demo.scenes]
    for cut in demo.cuts:
        copy = demo.copy_.get(cut.lang)
        if copy is None:
            errors.append(f"cut {cut.id}: no copy for language {cut.lang!r}")
            continue
        marks = load_marks(folder, demo, cut.lang)
        for scene in dumped:
            for path, value in _walk(scene):
                where = f"cut {cut.id}, scene {scene['id']}, {path}"
                if isinstance(value, str) and value.startswith("@") and value[1:] not in copy:
                    errors.append(f"{where}: copy key {value!r} missing for {cut.lang!r}")
                if _is_clip(value):
                    spec = demo.clips.get(value["video"])
                    if spec is None:
                        errors.append(f"{where}: unknown video {value['video']!r}")
                    elif value.get("beat"):
                        beats = marks.get(spec.marks or value["video"], {})
                        if value["beat"] not in beats:
                            errors.append(f"{where}: beat {value['beat']!r} not in the {cut.lang!r} marks "
                                          f"for {value['video']!r}")
        for s in demo.scenes:
            if isinstance(s, WindowStates) and s.legend.changes is None:
                if s.legend.measure is None:
                    errors.append(f"cut {cut.id}, scene {s.id}: legend needs `changes` or `measure`")
                elif states_key(s.legend.anchor) not in marks.get(STATES_KEY, {}):
                    errors.append(f"cut {cut.id}, scene {s.id}: legend states not measured yet — run the prep stage")
        for track in cut.soundtrack:
            for anchor in (track.start, track.end):
                if isinstance(anchor, Anchor) and anchor.scene not in scene_ids:
                    errors.append(f"cut {cut.id}: soundtrack {track.file} anchors to unknown scene {anchor.scene!r}")
    return errors


def scene_frames(demo: Demo) -> list[tuple[int, int]]:
    """``(from, dur)`` in frames for every scene, in order."""
    out, t = [], 0
    for s in demo.scenes:
        dur = round(s.seconds * demo.fps)
        out.append((t, dur))
        t += dur
    return out


def total_frames(demo: Demo) -> int:
    return sum(d for _, d in scene_frames(demo))


def clip_files(demo: Demo, lang: str) -> dict[str, str]:
    """Clip id → media-relative file for one language."""
    return {cid: spec.file.replace("{lang}", lang) for cid, spec in demo.clips.items()}


def missing_media(folder: Path, demo: Demo, cut: Cut) -> list[str]:
    """Media files (clips actually used + soundtrack) absent from the media folder."""
    root = media_root(folder, demo)
    used = {v["video"] for s in demo.scenes for _, v in _walk(s.model_dump(mode="json")) if _is_clip(v)}
    files = [f for cid, f in clip_files(demo, cut.lang).items() if cid in used]
    files += [t.file.replace("{lang}", cut.lang) for t in cut.soundtrack]
    return [f for f in files if not (root / f).is_file()]


_KEY = re.compile(r"^@(.+)$")


def resolve_cut(demo: Demo, folder: Path, cut: Cut, *, track_seconds: Optional[dict[str, float]] = None) -> dict:
    """The Remotion props for one cut. Call ``validate`` first; this raises on a bad ref.

    ``track_seconds`` maps a soundtrack file to its length, needed only by
    tracks with ``align_end_tail_s`` (the pipeline probes it with ffprobe).
    """
    copy = demo.copy_[cut.lang]
    marks = load_marks(folder, demo, cut.lang)
    files = clip_files(demo, cut.lang)
    fps = demo.fps

    def text(value: Any) -> Any:
        if isinstance(value, str):
            m = _KEY.match(value)
            return copy[m.group(1)] if m else value
        if isinstance(value, list):
            return [text(v) for v in value]
        if isinstance(value, dict):
            if _is_clip(value):
                return clip(value)
            return {k: text(v) for k, v in value.items() if v is not None}
        return value

    def clip(ref: dict) -> dict:
        spec = demo.clips[ref["video"]]
        start = 0.0
        if ref.get("beat"):
            start = marks[spec.marks or ref["video"]][ref["beat"]][0]
        out = {"src": files[ref["video"]], "from": round(start + ref["offset"], 3), "rate": ref["rate"]}
        if ref.get("crop"):
            out["crop"] = ref["crop"]
        return out

    scenes = []
    for s, (start, dur) in zip(demo.scenes, scene_frames(demo)):
        raw = s.model_dump(mode="json")
        body = {k: text(v) for k, v in raw.items() if k not in ("id", "type", "seconds", "fade_s") and v is not None}
        if s.type == "full_bleed" and body.get("chat"):
            chat = body["chat"]
            senders = chat.pop("senders", None) or []
            step = chat.pop("sender_step", 7)
            chat["messages"] = [
                m if isinstance(m, dict) else {"name": senders[((i + 1) * step) % len(senders)] if senders else "", "text": m}
                for i, m in enumerate(chat["messages"])
            ]
        if s.type == "steps_device":
            body["steps"] = [st if isinstance(st, dict) else {"title": st, "sub": ""} for st in body["steps"]]
            body["step_at"] = [c["from"] for c in body.get("step_at", [])]
        if s.type == "window_states":
            body["legend"]["anchor"] = body["legend"]["anchor"]["from"]
            body["legend"].pop("measure", None)
            if s.legend.changes is None:
                body["legend"]["changes"] = marks[STATES_KEY][states_key(s.legend.anchor)]
            for seg in body["segments"]:
                seg.setdefault("seconds", None)
        scenes.append({"id": s.id, "type": s.type, "from": start, "dur": dur, "fade": round(s.fade_s * fps), **body})

    total = total_frames(demo)
    starts = {s.id: f for s, (f, _) in zip(demo.scenes, scene_frames(demo))}

    def at(anchor: Union[Anchor, float, str]) -> int:
        if anchor == "end":
            return total
        if isinstance(anchor, Anchor):
            return starts[anchor.scene] + round(anchor.offset_s * fps)
        return round(float(anchor) * fps)

    tracks = []
    for t in cut.soundtrack:
        start, end = at(t.start), at(t.end)
        dur = max(1, end - start)
        file = t.file.replace("{lang}", cut.lang)
        if t.source_start_s is not None:
            start_from = round(t.source_start_s * fps)
        elif t.align_end_tail_s is not None:
            length = (track_seconds or {}).get(file)
            if length is None:
                raise ValueError(f"track {file}: align_end_tail_s needs its length (track_seconds)")
            start_from = max(0, round((length - t.align_end_tail_s) * fps) - dur)
        else:
            start_from = 0
        tracks.append({"src": file, "from": start, "dur": dur, "start_from": start_from, "volume": t.volume,
                       "fade_in": round(t.fade_in_s * fps), "fade_out": round(t.fade_out_s * fps)})

    width, height = ASPECTS[cut.aspect]
    return {
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": total,
        "product": demo.product.model_dump(mode="json"),
        "scenes": scenes,
        "tracks": tracks,
    }
