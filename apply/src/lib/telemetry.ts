// Per-step telemetry hook — fires metadata-only apply_events for a screen.
//
// On mount: step_viewed (with a fresh timer). Provides field helpers
// (field_focused / field_left_empty / validation_error_shown) and a
// step_completed emitter. A document-level beforeunload/visibilitychange fires
// last_step_before_exit for the current step. Every event is the metadata-only
// `ApplyEvent` shape: NEVER the selected value, NEVER a child key.
//
// `performance.now()` is fine here (normal React app, not a workflow script).

import { useCallback, useEffect, useRef } from 'react';
import { emitEvent, type ApplyEvent, type MinimalSupabase } from './apply';

export interface StepTelemetry {
  fieldFocused: (field_key: string) => void;
  fieldLeftEmpty: (field_key: string) => void;
  validationError: (field_key: string) => void;
  stepCompleted: () => void;
}

export function useStepTelemetry(
  sb: MinimalSupabase | null,
  familyId: string | null,
  step: string,
): StepTelemetry {
  const startRef = useRef<number>(performance.now());

  const fire = useCallback(
    (
      event_type: ApplyEvent['event_type'],
      field_key: string | null,
    ) => {
      if (!sb || !familyId) return;
      void emitEvent(sb, {
        family_id: familyId,
        step,
        field_key,
        event_type,
        time_on_step_ms: Math.round(performance.now() - startRef.current),
      });
    },
    [sb, familyId, step],
  );

  // step_viewed on mount; reset the per-step timer.
  useEffect(() => {
    startRef.current = performance.now();
    fire('step_viewed', null);
  }, [fire]);

  // last_step_before_exit if the applicant abandons mid-step.
  useEffect(() => {
    if (!sb || !familyId) return;
    const onExit = () => fire('last_step_before_exit', null);
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
    fieldFocused: (field_key) => fire('field_focused', field_key),
    fieldLeftEmpty: (field_key) => fire('field_left_empty', field_key),
    validationError: (field_key) => fire('validation_error_shown', field_key),
    stepCompleted: () => fire('step_completed', null),
  };
}
