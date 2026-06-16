// Per-step telemetry hook — fires metadata-only apply_events for a screen, now
// at step → form → field depth.
//
// On mount: step_viewed (with a fresh timer). Provides field helpers
// (field_focused / field_left_empty / validation_error_shown / field_changed),
// sub-form helpers (form_viewed / form_completed) and a step_completed emitter.
// A document-level beforeunload/visibilitychange fires last_step_before_exit for
// the current step AND the current sub-form. Every event is the metadata-only
// `ApplyEvent` shape: NEVER the selected value, NEVER a child key.
//
// `nav_seq` is a MONOTONIC PER-SESSION counter, incremented on every emitted
// event (across all steps/forms) so navigation order is reconstructable. It is
// module-level + reset-able so a fresh App mount (e.g. each test) starts clean.
//
// `performance.now()` is fine here (normal React app, not a workflow script).

import { useCallback, useEffect, useRef } from 'react';
import { emitEvent, type ApplyEvent, type MinimalSupabase } from './apply';

// Monotonic per-session navigation sequence. Module-scoped so it spans steps
// within one App lifetime; resetNavSeq() lets a fresh mount start at 0.
let navSeqCounter = 0;
export function nextNavSeq(): number {
  navSeqCounter += 1;
  return navSeqCounter;
}
export function resetNavSeq(): void {
  navSeqCounter = 0;
}

export interface StepTelemetry {
  fieldFocused: (field_key: string) => void;
  fieldLeftEmpty: (field_key: string) => void;
  fieldChanged: (field_key: string) => void;
  validationError: (field_key: string) => void;
  stepCompleted: () => void;
  /** Entering a sub-form (left-rail / section). Carries the form_key. */
  formViewed: (form_key: string) => void;
  /** Submitting a sub-form. Carries the form_key. */
  formCompleted: (form_key: string) => void;
  /** Field helpers bound to a specific sub-form (carry both form_key + field_key). */
  forForm: (form_key: string) => FormTelemetry;
}

export interface FormTelemetry {
  fieldFocused: (field_key: string) => void;
  fieldLeftEmpty: (field_key: string) => void;
  fieldChanged: (field_key: string) => void;
  validationError: (field_key: string) => void;
}

export function useStepTelemetry(
  sb: MinimalSupabase | null,
  familyId: string | null,
  step: string,
): StepTelemetry {
  const startRef = useRef<number>(performance.now());
  // Tracks the sub-form the applicant is currently inside, so an abandon fires
  // last_step_before_exit carrying the right form_key.
  const currentFormRef = useRef<string | null>(null);

  const fire = useCallback(
    (
      event_type: ApplyEvent['event_type'],
      form_key: string | null,
      field_key: string | null,
    ) => {
      if (!sb || !familyId) return;
      void emitEvent(sb, {
        family_id: familyId,
        step,
        form_key,
        field_key,
        event_type,
        time_on_step_ms: Math.round(performance.now() - startRef.current),
        nav_seq: nextNavSeq(),
      });
    },
    [sb, familyId, step],
  );

  // step_viewed on mount; reset the per-step timer.
  useEffect(() => {
    startRef.current = performance.now();
    fire('step_viewed', null, null);
  }, [fire]);

  // last_step_before_exit if the applicant abandons mid-step — carries whatever
  // sub-form they were last inside (null if none).
  useEffect(() => {
    if (!sb || !familyId) return;
    const onExit = () => fire('last_step_before_exit', currentFormRef.current, null);
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') onExit();
    };
    window.addEventListener('beforeunload', onExit);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('beforeunload', onExit);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [sb, familyId, fire]);

  return {
    fieldFocused: (field_key) => fire('field_focused', null, field_key),
    fieldLeftEmpty: (field_key) => fire('field_left_empty', null, field_key),
    fieldChanged: (field_key) => fire('field_changed', null, field_key),
    validationError: (field_key) => fire('validation_error_shown', null, field_key),
    stepCompleted: () => fire('step_completed', null, null),
    formViewed: (form_key) => {
      currentFormRef.current = form_key;
      fire('form_viewed', form_key, null);
    },
    formCompleted: (form_key) => {
      fire('form_completed', form_key, null);
      currentFormRef.current = null;
    },
    forForm: (form_key) => ({
      fieldFocused: (field_key) => fire('field_focused', form_key, field_key),
      fieldLeftEmpty: (field_key) => fire('field_left_empty', form_key, field_key),
      fieldChanged: (field_key) => fire('field_changed', form_key, field_key),
      validationError: (field_key) =>
        fire('validation_error_shown', form_key, field_key),
    }),
  };
}
