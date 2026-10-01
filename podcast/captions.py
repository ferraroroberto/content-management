"""Burned-in caption files (ASS) in the owner's caption style: Sora ExtraBold,
white with a thick black outline, word-timed karaoke: a chunk of a few words
shows while it is spoken and each word turns yellow ``#FDEC01`` as it is said.

1:1 clips get one short line in the lower third; 9:16 stacked clips get up
to two lines sitting on the seam between the two speakers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from podcast.transcribe import drop_backchannel

HIGHLIGHT_BGR = "&H0001ECFD&"   # #FDEC01 in ASS (&HAABBGGRR)
WHITE_BGR = "&H00FFFFFF&"

_ASS_UNSAFE = re.compile(r"[{}\\]")


@dataclass(frozen=True)
class Layout:
    name: str
    width: int
    height: int
    font_size: int
    max_words: int
    line_chars: int     # widest line, in characters, that fits between the side margins
    lines: int
    alignment: int      # ASS numpad alignment
    margin_v: int
    outline: int
    spacing: int
    line_pitch: int = 0  # baseline-to-baseline distance when a chunk spans two lines


# Matched against the owner's published clips (#333): rendering their caption
# words with these values reproduces the reference glyph boxes within ~3 px.
# 1:1: one line, baseline at y≈950. 9:16: two lines 110 px apart, the block's
# bottom just under the seam. libass renders up to ~50 px/char at 126 and ~56
# at 154, so 18 and 16 characters per line stay inside the 960 px between margins.
LAYOUTS = {
    "1x1": Layout("1x1", 1080, 1080, 126, 4, 18, 1, 2, 104, 6, 1),
    "9x16": Layout("9x16", 1080, 1920, 154, 7, 16, 2, 2, 923, 7, 1, 110),
}


def _line_count(words: list[dict], line_chars: int) -> int:
    """Lines a greedy fill of ``line_chars`` characters per line needs (an over-long word gets its own)."""
    count, used = 1, -1
    for w in words:
        need = len(w["w"]) + (1 if used >= 0 else 0)
        if used >= 0 and used + need > line_chars:
            count, used = count + 1, len(w["w"])
        else:
            used = max(used, 0) + need
    return count


def chunk_words(words: list[dict], *, max_words: int, line_chars: int, lines: int = 1,
                max_gap: float = 0.6) -> list[list[dict]]:
    """Split words into caption chunks at sentence ends, pauses, speaker changes and size limits.

    A chunk grows only while it still fits ``lines`` lines of ``line_chars``.
    Commas do not end a chunk: the owner's captions run across them."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            prev = cur[-1]
            if (len(cur) >= max_words or _line_count(cur + [w], line_chars) > lines
                    or w["s"] - prev["e"] > max_gap or w["spk"] != prev["spk"]
                    or prev["w"].endswith((".", "?", "!", ";", ":"))):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _chunk_lines(chunk: list[dict], highlight: int, layout: Layout) -> list[str]:
    parts = []
    for i, w in enumerate(chunk):
        word = _ASS_UNSAFE.sub("", w["w"])
        parts.append(f"{{\\c{HIGHLIGHT_BGR}}}{word}{{\\c{WHITE_BGR}}}" if i == highlight else word)
    if layout.lines > 1 and _line_count(chunk, layout.line_chars) > 1:
        # the break with the shortest longer line: balanced like the reference
        # captions, and never wider than the greedy fill the chunker checked
        def longest(k: int) -> int:
            return max(len(" ".join(w["w"] for w in chunk[:k])), len(" ".join(w["w"] for w in chunk[k:])))
        split = min(range(1, len(parts)), key=longest)
        return [" ".join(parts[:split]), " ".join(parts[split:])]
    return [" ".join(parts)]


def build_ass(words: list[dict], clip_start: float, clip_end: float, layout: Layout,
              font_name: str = "Sora ExtraBold") -> str:
    """ASS document for the words inside ``[clip_start, clip_end)``, times relative to the clip.

    Karaoke: the chunk stays on screen while each of its words is
    highlighted in turn, from the word's start until the next one starts."""
    inside = [w for w in drop_backchannel(words) if clip_start <= w["s"] < clip_end]
    chunks = chunk_words(inside, max_words=layout.max_words, line_chars=layout.line_chars, lines=layout.lines)
    duration = clip_end - clip_start
    events = []
    for i, chunk in enumerate(chunks):
        nxt = chunks[i + 1][0]["s"] - clip_start if i + 1 < len(chunks) else duration
        last = chunk[-1]["e"] - clip_start
        end = min(nxt, last + 0.4, duration) if nxt - last > 0.6 else min(nxt, duration)
        starts = [w["s"] - clip_start for w in chunk] + [end]
        for k in range(len(chunk)):
            a, b = starts[k], max(starts[k], min(starts[k + 1], end))
            if b - a < 0.01:
                continue
            # one event per line, bottom line on the style's margin: libass's own
            # line height for \N is far looser than the reference captions. Own layer
            # per row, or libass's collision handling shoves the lines apart.
            lines = _chunk_lines(chunk, k, layout)
            for row, text in enumerate(lines):
                margin = layout.margin_v + layout.line_pitch * (len(lines) - 1 - row)
                events.append(f"Dialogue: {row},{_ass_time(a)},{_ass_time(b)},Cap,,0,0,{margin},,{text}")
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {layout.width}",
        f"PlayResY: {layout.height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font_name},{layout.font_size},{WHITE_BGR},{WHITE_BGR},&H00000000&,&H00000000&,"
        f"0,0,0,0,100,100,{layout.spacing},0,1,{layout.outline},0,{layout.alignment},60,60,{layout.margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])


def caption_text(words: list[dict], clip_start: float, clip_end: float) -> str:
    """Plain text of what the captions show (used to score caption accuracy)."""
    return " ".join(w["w"] for w in drop_backchannel(words) if clip_start <= w["s"] < clip_end)
