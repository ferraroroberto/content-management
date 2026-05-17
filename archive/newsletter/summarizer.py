"""3-line plain-text summary of an article."""

from __future__ import annotations

from archive.newsletter import llm

PROMPT_TEMPLATE = """Summarize the article below in exactly 3 lines of plain text.
No bullets, no numbering, no preamble — three sentences on three separate lines.

Title: {title}

Article:
{body}
"""


def summarize(*, base_url: str, model: str, title: str, body_text: str,
              max_body_chars: int = 8000) -> str:
    body = (body_text or "")[:max_body_chars]
    prompt = PROMPT_TEMPLATE.format(title=title or "", body=body)
    return llm.call(base_url=base_url, model=model, prompt=prompt, max_tokens=400)
