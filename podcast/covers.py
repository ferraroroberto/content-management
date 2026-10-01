"""Stage: cover images in the owner's Affinity house style.

* Episode thumbnail (1920×1080): black, both names in the owner's handwriting
  font in yellow, round headshots with a white ring, the light-bulb mark,
  the episode promise underneath.
* Episode text card (1920×1080): the same pair on the right, a white pill
  label + optional date on the left, a short blurb underneath.
* Clip cover (1080×1080): a frame of the 1:1 clip with the title in a white
  rounded box, yellow Sora Bold with a black outline.

Fonts and images are local files named in ``podcast.fonts`` / ``podcast.host``
(the handwriting font is personal and never enters this repo).
"""

from __future__ import annotations

import logging
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from podcast.clips import load_clips
from podcast.episode import Episode, work_dir
from podcast.media import frame_at
from podcast.metrics import StageRecord
from podcast.package import load_episode_copy
from podcast.render import clip_outputs

logger = logging.getLogger("podcast.covers")

YELLOW = (254, 237, 1)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
CANVAS = (1920, 1080)
CIRCLE = 540
RING = 14
HAND_WEIGHT = 3   # same-colour stroke: the Affinity cards set the handwriting face heavier


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Greedy word wrap to ``max_width`` pixels."""
    lines: list[str] = []
    for word in text.split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and font.getlength(trial) <= max_width:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def _centered_lines(draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont,
                    cx: int, top: int, line_h: int, fill, stroke: int = 0, stroke_fill=BLACK) -> int:
    for i, line in enumerate(lines):
        draw.text((cx, top + i * line_h), line, font=font, fill=fill, anchor="ma",
                  stroke_width=stroke, stroke_fill=stroke_fill)
    return top + len(lines) * line_h


def circle_photo(img: Image.Image, diameter: int = CIRCLE, ring: int = RING) -> Image.Image:
    """Centre-square crop → round photo with a white ring, on transparency."""
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    photo = img.convert("RGB").crop((left, top, left + side, top + side))
    inner = diameter - 2 * ring
    photo = photo.resize((inner, inner), Image.LANCZOS)
    scale = 4  # supersampled masks for smooth edges
    mask = Image.new("L", (diameter * scale, diameter * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * scale - 1, diameter * scale - 1), fill=255)
    mask = mask.resize((diameter, diameter), Image.LANCZOS)
    inner_mask = Image.new("L", (inner * scale, inner * scale), 0)
    ImageDraw.Draw(inner_mask).ellipse((0, 0, inner * scale - 1, inner * scale - 1), fill=255)
    inner_mask = inner_mask.resize((inner, inner), Image.LANCZOS)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (diameter, diameter), WHITE + (255,)), (0, 0), mask)
    out.paste(photo, (ring, ring), inner_mask)
    return out


def _name_lines(name: str) -> list[str]:
    first, _, rest = name.upper().partition(" ")
    return [first, rest] if rest else [first]


def _pair(canvas: Image.Image, guest_photo: Image.Image, host_photo: Image.Image, names: tuple[str, str],
          centers: tuple[int, int], bulb: Image.Image, hand: str) -> None:
    """Two round photos with names above and the bulb between (shared by both cards)."""
    draw = ImageDraw.Draw(canvas)
    b = bulb.convert("RGBA")
    b.thumbnail((250, 250), Image.LANCZOS)
    # a name line is centred on its photo, so it must stay inside the half-gap
    # beside the bulb: long surnames shrink the font instead of running into it
    max_w = centers[1] - centers[0] - b.width - 40
    for photo, name, cx in zip((guest_photo, host_photo), names, centers):
        canvas.alpha_composite(circle_photo(photo), (cx - CIRCLE // 2, 535 - CIRCLE // 2))
        lines = _name_lines(name)
        size = 100
        while size > 60 and max(_font(hand, size).getlength(line) for line in lines) + 2 * HAND_WEIGHT > max_w:
            size -= 4
        _centered_lines(draw, lines, _font(hand, size), cx, 236 - 80 * len(lines), 80, YELLOW, HAND_WEIGHT, YELLOW)
    mid = (centers[0] + centers[1]) // 2
    canvas.alpha_composite(b, (mid - b.width // 2, 260 - b.height // 2))


def thumbnail(guest_photo: Image.Image, host_photo: Image.Image, names: tuple[str, str], title: str,
              bulb: Image.Image, hand: str) -> Image.Image:
    canvas = Image.new("RGBA", CANVAS, BLACK + (255,))
    _pair(canvas, guest_photo, host_photo, names, (665, 1255), bulb, hand)
    font = _font(hand, 98)
    _centered_lines(ImageDraw.Draw(canvas), wrap(title.upper(), font, 1400), font, 960, 840, 80, YELLOW,
                    HAND_WEIGHT, YELLOW)
    return canvas.convert("RGB")


def text_card(guest_photo: Image.Image, host_photo: Image.Image, names: tuple[str, str], label: str,
              when: str, blurb: str, bulb: Image.Image, hand: str) -> Image.Image:
    canvas = Image.new("RGBA", CANVAS, BLACK + (255,))
    _pair(canvas, guest_photo, host_photo, names, (945, 1535), bulb, hand)
    draw = ImageDraw.Draw(canvas)
    pill_font = _font(hand, 90)
    pill_lines = wrap(label.upper(), pill_font, 520)
    pill_h = 80 * len(pill_lines) + 90
    pill_top = 455 - pill_h // 2
    draw.rounded_rectangle((30, pill_top, 630, pill_top + pill_h), radius=70, fill=WHITE)
    _centered_lines(draw, pill_lines, pill_font, 330, pill_top + 42, 80, BLACK, 1, BLACK)
    if when:
        when_font = _font(hand, 80)
        _centered_lines(draw, [w.upper() for w in when.split("\n")], when_font, 330, pill_top + pill_h + 60,
                        86, YELLOW)
    blurb_font = _font(hand, 40)
    _centered_lines(draw, wrap(blurb.upper(), blurb_font, 1180), blurb_font, 1258, 815, 34, YELLOW, 1, YELLOW)
    return canvas.convert("RGB")


def clip_cover(frame: Image.Image, title: str, font_path: str) -> Image.Image:
    """Square frame with the title in a white rounded box near the bottom."""
    side = min(frame.size)
    left, top = (frame.width - side) // 2, (frame.height - side) // 2
    img = frame.convert("RGB").crop((left, top, left + side, top + side)).resize((1080, 1080), Image.LANCZOS)
    draw = ImageDraw.Draw(img)
    font = _font(font_path, 92)
    lines = wrap(title, font, 640)
    line_h = 108
    width = int(max(font.getlength(line) for line in lines)) + 110
    height = line_h * len(lines) + 60
    left, bottom = (1080 - width) // 2, 1040
    draw.rounded_rectangle((left, bottom - height, left + width, bottom), radius=28, fill=WHITE)
    _centered_lines(draw, lines, font, 540, bottom - height + 22, line_h, YELLOW, stroke=6)
    return img


def _guest_photo(ep: Episode, scratch: Path) -> Image.Image:
    if ep.guest_headshot and ep.guest_headshot.exists():
        return Image.open(ep.guest_headshot)
    grab = frame_at(ep.tracks["guest"], ep.start_s + 120, scratch / "guest_frame.png")
    logger.info("ℹ️ no guest_headshot in episode.json — using a frame of the guest track")
    return Image.open(grab)


def run(ep: Episode, cfg: dict, rec: StageRecord, *, force: bool = False) -> None:
    """Episode thumbnail + text card, then one cover per clip (kept on disk unless ``force``)."""
    fonts, host = cfg["fonts"], cfg["host"]
    scratch = work_dir(cfg, ep)
    copy = load_episode_copy(ep)
    bulb = Image.open(host["lightbulb"])
    guest_photo, host_photo = _guest_photo(ep, scratch), Image.open(host["headshot"])
    names = (ep.guest, ep.host_name)
    thumbnail(guest_photo, host_photo, names, copy["thumbnail_title"], bulb, fonts["handwriting"]).save(
        ep.package / f"{ep.base_name} (1920x1080)_thumbnail.png")
    text_card(guest_photo, host_photo, names, ep.card_label, ep.card_when, copy["card_blurb"], bulb,
              fonts["handwriting"]).save(ep.package / f"{ep.base_name} (1920x1080)_text.png")
    clips = load_clips(ep)
    for clip in clips:
        out = clip_outputs(ep, clip, "covers")[0]
        if out.exists() and not force:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        # Source-track frame (no burned captions), same speaker as the 1:1 clip.
        frame = frame_at(ep.tracks[clip["speaker"]], clip["start"] + 1.5, scratch / "cover_frame.png")
        clip_cover(Image.open(frame), clip["title"], fonts["cover"]).save(out)
    logger.info("✅ covers: thumbnail, text card, %d clip covers", len(clips))
