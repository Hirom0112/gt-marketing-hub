"""The content review gate — nothing publishes without keep/approve (FR-3.5; P-2).

CONTENT_SPEC §2.3 makes the content lifecycle a state machine; FR-3.5 / P-2 make
the human the sole publisher: a candidate cannot advance past review — into the
library, into brand memory, anywhere "published" — without an explicit human
``keep``/``approve``. Nothing is auto-published; ``pending`` and ``discard`` never
advance.

This module is the small, pure predicate the API and keep path consult to enforce
that rule. It owns no state and calls no LLM; the actual promotion lives in
:mod:`app.marketing.keep_discard`. Keeping this guard separate makes "nothing
publishes without approval" a single testable fact (the §5.3 review boundary).
"""

from __future__ import annotations

from app.ai.schemas.content import Decision

# The decisions that ADVANCE a candidate to publication (FR-3.5). Only an explicit
# human keep/approve publishes; everything else (pending, discard, edit, reject)
# does not auto-advance.
_PUBLISHING_DECISIONS: frozenset[str] = frozenset({Decision.KEEP.value, Decision.APPROVE.value})


def publishes(action: Decision | str) -> bool:
    """True iff ``action`` is an explicit keep/approve that publishes (FR-3.5).

    Any other verdict (``pending``, ``discard``, ``edit``, ``reject``) returns
    False — it does not advance the candidate to the library / brand memory.
    """
    value = action.value if isinstance(action, Decision) else action
    return value in _PUBLISHING_DECISIONS


def requires_human_decision(action: Decision | str) -> bool:
    """True iff ``action`` still NEEDS a human keep/approve before publishing (P-2).

    The inverse of :func:`publishes`: a candidate under any non-publishing verdict
    (including the default ``pending``) is BLOCKED from publication until a human
    explicitly keeps/approves it. The API uses this to fail closed — never
    auto-publish (FR-3.5 / INV-2).
    """
    return not publishes(action)
