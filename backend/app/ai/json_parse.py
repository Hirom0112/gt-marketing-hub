"""Parse-boundary normalizer for live model text (INV-2).

The AI graphs validate a live model's text against a Pydantic schema
(`model_validate_json`) or `json.loads`. Both require the raw `{`/`[` — but
models routinely wrap their JSON in a Markdown ```json fence, so a fenced-but-
otherwise-valid payload was rejected at the boundary (the `v1_schema` gate
failure). :func:`strip_code_fence` unwraps a whole-output fence so the real JSON
reaches the validator. It is deliberately conservative: only a fence that wraps
the ENTIRE (stripped) output is removed, and non-fenced text passes through
verbatim — so genuine garbage still fails the schema and INV-2 fail-closed holds.

Pure, no I/O, no SDK import — safe to import anywhere.
"""

from __future__ import annotations

_FENCE = "```"


def strip_code_fence(text: str) -> str:
    """Return ``text`` with a whole-output Markdown code fence removed, else verbatim.

    Unwraps ```` ```json\\n…\\n``` ```` and bare ```` ```\\n…\\n``` ```` (any
    language tag on the opening fence is dropped). Surrounding whitespace is
    tolerated. If the output is not fence-wrapped — or is a degenerate lone
    fence marker — the original ``text`` is returned unchanged so the downstream
    schema validation still rejects malformed payloads (INV-2).
    """
    stripped = text.strip()
    if not stripped.startswith(_FENCE):
        return text

    # Drop the opening fence line (``` plus an optional language tag).
    first_newline = stripped.find("\n")
    if first_newline == -1:
        # A lone ``` with no body — nothing to unwrap; leave it to fail to parse.
        return text
    inner = stripped[first_newline + 1 :]

    # Drop the closing fence if present (everything from the last ``` onward).
    closing = inner.rfind(_FENCE)
    if closing != -1:
        inner = inner[:closing]

    return inner.strip()
