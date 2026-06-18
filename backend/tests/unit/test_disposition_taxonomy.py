"""Contact-disposition taxonomy extension (D-16; REDESIGN_PLAN R4 task 1).

The agent call-outcome dropdown (sales-agent brief, Tab "Call Outcome") needs four
labels the existing :class:`ContactDisposition` enum lacks — Appointment Scheduled,
Interested, Not Interested, Follow-Up Needed. D-16 adds them ADDITIVELY and the
recovery deriver must classify each sensibly:

- ``appointment_scheduled`` / ``interested`` = ENGAGEMENT — a positive live contact,
  the same family as ``REACHED`` / ``COMMITTED_TO_PAY``: it is NOT silence (never
  accrues toward presumed-lost) and reads as a worked/engaged contact.
- ``follow_up_needed`` = ENGAGEMENT too — a logged live contact that keeps the family
  active (not silence), the rep is still on it.
- ``not_interested`` = DECLINE — the explicit-decline family, like ``DECLINED``.

This is a STRICT-TDD (CLAUDE §4.1) red test: it asserts the new classification AND
that every PRE-EXISTING disposition keeps its current class (the regression guard the
plan's risk note requires — the enum extension must not perturb the silence rule).
"""

from __future__ import annotations

from app.core.recovery_state import DispositionClass, classify_disposition
from app.observability.log_store import NO_RESPONSE_DISPOSITIONS, ContactDisposition


def test_new_dispositions_exist_on_the_enum() -> None:
    """The four D-16 labels are now members of ContactDisposition (additive)."""
    assert ContactDisposition.APPOINTMENT_SCHEDULED.value == "appointment_scheduled"
    assert ContactDisposition.INTERESTED.value == "interested"
    assert ContactDisposition.NOT_INTERESTED.value == "not_interested"
    assert ContactDisposition.FOLLOW_UP_NEEDED.value == "follow_up_needed"


def test_new_engagement_dispositions_classify_as_engagement() -> None:
    """appointment/interested/follow-up = engagement (a positive live contact)."""
    assert (
        classify_disposition(ContactDisposition.APPOINTMENT_SCHEDULED) is DispositionClass.ENGAGED
    )
    assert classify_disposition(ContactDisposition.INTERESTED) is DispositionClass.ENGAGED
    assert classify_disposition(ContactDisposition.FOLLOW_UP_NEEDED) is DispositionClass.ENGAGED


def test_not_interested_classifies_as_decline() -> None:
    """not_interested trends toward decline — same class as the existing DECLINED."""
    assert classify_disposition(ContactDisposition.NOT_INTERESTED) is DispositionClass.DECLINED
    assert classify_disposition(ContactDisposition.DECLINED) is DispositionClass.DECLINED


def test_existing_engagement_dispositions_unchanged() -> None:
    """REACHED / COMMITTED_TO_PAY keep their engagement class (regression)."""
    assert classify_disposition(ContactDisposition.REACHED) is DispositionClass.ENGAGED
    assert classify_disposition(ContactDisposition.COMMITTED_TO_PAY) is DispositionClass.ENGAGED


def test_existing_no_response_dispositions_unchanged() -> None:
    """NO_ANSWER / NO_REPLY / VOICEMAIL stay SILENT, and the silence SET is intact.

    The presumed-lost accrual reads ``NO_RESPONSE_DISPOSITIONS``; the extension must
    not add a NEW disposition to it (an engagement/decline outcome is not silence),
    so the set is exactly the three pre-existing no-response values.
    """
    assert classify_disposition(ContactDisposition.NO_ANSWER) is DispositionClass.SILENT
    assert classify_disposition(ContactDisposition.NO_REPLY) is DispositionClass.SILENT
    assert classify_disposition(ContactDisposition.VOICEMAIL) is DispositionClass.SILENT
    assert NO_RESPONSE_DISPOSITIONS == frozenset(
        {
            ContactDisposition.NO_ANSWER,
            ContactDisposition.NO_REPLY,
            ContactDisposition.VOICEMAIL,
        }
    )


def test_wrong_number_classifies_as_silent() -> None:
    """WRONG_NUMBER is no live contact (no engagement, no explicit decline) ⇒ silent."""
    assert classify_disposition(ContactDisposition.WRONG_NUMBER) is DispositionClass.SILENT


def test_every_disposition_has_a_class() -> None:
    """classify_disposition is total — every enum member maps to a class (no gaps)."""
    for disp in ContactDisposition:
        assert isinstance(classify_disposition(disp), DispositionClass)
