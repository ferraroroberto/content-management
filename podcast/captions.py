"""Burned-in caption files (ASS) in the owner's caption style: Sora ExtraBold,
white with a thick black outline, one keyword per chunk in yellow ``#FDEC01``.

1:1 clips get one short line in the lower third; 9:16 stacked clips get up
to two lines centred on the seam between the two speakers.
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
    max_chars: int
    lines: int
    alignment: int      # ASS numpad alignment
    margin_v: int


# Sizes measured on the owner's published clips (#333 spike): ~104 px Sora
# ExtraBold; 1:1 one line with its baseline near y≈950, 9:16 two lines on the seam.
LAYOUTS = {
    "1x1": Layout("1x1", 1080, 1080, 104, 4, 20, 1, 2, 100),
    "9x16": Layout("9x16", 1080, 1920, 104, 7, 32, 2, 5, 0),
}


def chunk_words(words: list[dict], *, max_words: int, max_chars: int,
                max_gap: float = 0.6) -> list[list[dict]]:
    """Split words into caption chunks at punctuation, pauses, speaker changes and size limits."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            prev = cur[-1]
            text_len = len(" ".join(x["w"] for x in cur)) + 1 + len(w["w"])
            if (len(cur) >= max_words or text_len > max_chars or w["s"] - prev["e"] > max_gap
                    or w["spk"] != prev["spk"] or prev["w"].endswith((".", ",", "?", "!", ";", ":"))):
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


def _chunk_text(chunk: list[dict], keyword: int, lines: int) -> str:
    parts = []
    for i, w in enumerate(chunk):
        word = _ASS_UNSAFE.sub("", w["w"])
        parts.append(f"{{\\c{HIGHLIGHT_BGR}}}{word}{{\\c{WHITE_BGR}}}" if i == keyword else word)
    if lines > 1 and len(parts) > 3:
        split = (len(parts) + 1) // 2
        return " ".join(parts[:split]) + "\\N" + " ".join(parts[split:])
    return " ".join(parts)


def build_ass(words: list[dict], clip_start: float, clip_end: float, layout: Layout,
              font_name: str = "Sora ExtraBold") -> str:
    """ASS document for the words inside ``[clip_start, clip_end)``, times relative to the clip."""
    inside = [w for w in drop_backchannel(words) if clip_start <= w["s"] < clip_end]
    chunks = chunk_words(inside, max_words=layout.max_words, max_chars=layout.max_chars)
    duration = clip_end - clip_start
    events = []
    for i, chunk in enumerate(chunks):
        start = chunk[0]["s"] - clip_start
        nxt = chunks[i + 1][0]["s"] - clip_start if i + 1 < len(chunks) else duration
        end = min(nxt, chunk[-1]["e"] - clip_start + 0.4) if nxt - (chunk[-1]["e"] - clip_start) > 0.6 else nxt
        text = _chunk_text(chunk, pick_keyword(chunk), layout.lines)
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(min(end, duration))},Cap,,0,0,0,,{text}")
    outline = max(4, layout.font_size // 10)
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
        f"0,0,0,0,100,100,0,0,1,{outline},0,{layout.alignment},60,60,{layout.margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
        "",
    ])


def caption_text(words: list[dict], clip_start: float, clip_end: float) -> str:
    """Plain text of what the captions show (used to score caption accuracy)."""
    return " ".join(w["w"] for w in drop_backchannel(words) if clip_start <= w["s"] < clip_end)
