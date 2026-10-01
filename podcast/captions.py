"""Burned-in caption files (ASS) in the owner's caption style: Sora ExtraBold,
white with a thick black outline, one keyword per chunk in yellow ``#FDEC01``.

1:1 clips get one short line in the lower third; 9:16 stacked clips get up
to two lines sitting on the seam between the two speakers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from podcast.transcribe import drop_backchannel

HIGHLIGHT_BGR = "&H0001ECFD&"   # #FDEC01 in ASS (&HAABBGGRR)
WHITE_BGR = "&H00FFFFFF&"

STOPWORDS = frozenset("""
a about after again all also an and any are as at be because been before being but by can
could did do does doing don't down during each even every for from had has have having he her
here hers him his how i i'm if in into is isn't it it's its just let's like make many me more
most much my no not now of off on once one only or other our out over really right said same
say see she should so some something still such than that that's the their them then there
these they thing things think this those through to too um uh up us very was we we're well
were what when where which while who why will with would yeah yes you you're your
""".split())

_STRIP = re.compile(r"[^\w'-]")
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


def pick_keyword(chunk: list[dict]) -> int:
    """Index of the chunk's most salient word (longest non-stopword, ≥5 letters), or -1."""
    best, best_len = -1, 4
    for i, w in enumerate(chunk):
        core = _STRIP.sub("", w["w"]).lower()
        if core not in STOPWORDS and len(core) > best_len:
            best, best_len = i, len(core)
    return best


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _chunk_lines(chunk: list[dict], keyword: int, layout: Layout) -> list[str]:
    parts = []
    for i, w in enumerate(chunk):
        word = _ASS_UNSAFE.sub("", w["w"])
        parts.append(f"{{\\c{HIGHLIGHT_BGR}}}{word}{{\\c{WHITE_BGR}}}" if i == keyword else word)
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
    """ASS document for the words inside ``[clip_start, clip_end)``, times relative to the clip."""
    inside = [w for w in drop_backchannel(words) if clip_start <= w["s"] < clip_end]
    chunks = chunk_words(inside, max_words=layout.max_words, line_chars=layout.line_chars, lines=layout.lines)
    duration = clip_end - clip_start
    events = []
    for i, chunk in enumerate(chunks):
        start = chunk[0]["s"] - clip_start
        nxt = chunks[i + 1][0]["s"] - clip_start if i + 1 < len(chunks) else duration
        end = min(nxt, chunk[-1]["e"] - clip_start + 0.4) if nxt - (chunk[-1]["e"] - clip_start) > 0.6 else nxt
        lines = _chunk_lines(chunk, pick_keyword(chunk), layout)
        # one event per line, bottom line on the style's margin: libass's own
        # line height for \N is far looser than the reference captions. Own layer
        # per row, or libass's collision handling shoves the lines apart.
        for row, text in enumerate(lines):
            margin = layout.margin_v + layout.line_pitch * (len(lines) - 1 - row)
            events.append(f"Dialogue: {row},{_ass_time(start)},{_ass_time(min(end, duration))},"
                          f"Cap,,0,0,{margin},,{text}")
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
