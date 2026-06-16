"""Unit tests for the model-text parse helper (INV-2 parse boundary).

`strip_code_fence` is the pure normalizer the AI graphs apply to a live model's
text BEFORE schema validation. Live models routinely wrap their JSON in a
Markdown ```json fence; `model_validate_json` / `json.loads` need the raw `{`/`[`,
so a fenced-but-otherwise-valid payload was being REJECTED at the boundary (the
`v1_schema` gate failure). The helper unwraps a whole-output fence and is a no-op
on raw JSON and on genuine garbage (so INV-2 still rejects malformed output).
"""

from __future__ import annotations

from app.ai.json_parse import strip_code_fence


def test_strips_json_tagged_fence() -> None:
    """A ```json fence wrapping the whole payload is unwrapped to the raw object."""
    fenced = '```json\n{"action": "email", "body": "hi"}\n```'
    assert strip_code_fence(fenced) == '{"action": "email", "body": "hi"}'


def test_strips_bare_fence() -> None:
    """A bare ``` fence (no language tag) is unwrapped too."""
    fenced = '```\n[{"id": 1}]\n```'
    assert strip_code_fence(fenced) == '[{"id": 1}]'


def test_strips_fence_with_surrounding_whitespace() -> None:
    """Leading/trailing whitespace around the fence is tolerated."""
    fenced = '\n  ```json\n{"a": 1}\n```  \n'
    assert strip_code_fence(fenced) == '{"a": 1}'


def test_raw_json_is_unchanged() -> None:
    """Raw JSON (no fence) passes through verbatim — the helper is a no-op."""
    raw = '{"action": "email", "body": "hi"}'
    assert strip_code_fence(raw) == raw


def test_genuine_garbage_is_unchanged() -> None:
    """Non-fenced garbage is left as-is so the schema boundary still rejects it (INV-2)."""
    garbage = "not json at all <<garbage>>"
    assert strip_code_fence(garbage) == garbage


def test_lone_fence_marker_is_unchanged() -> None:
    """A degenerate lone ``` with no body is left as-is (it will fail to parse)."""
    assert strip_code_fence("```") == "```"


def test_inner_triple_backtick_free_content_only_outer_stripped() -> None:
    """Only the outer fence is removed; inner JSON content is preserved intact."""
    fenced = '```json\n{"body": "see the docs"}\n```'
    assert strip_code_fence(fenced) == '{"body": "see the docs"}'
