import { useEffect, useState } from 'react';
import { LogOut } from 'lucide-react';
import { apiFetch } from '../config';
import { dropOffPath } from './format';

// Per-family drop-off telemetry (S15 W2). Surfaces the last apply-flow position
// before exit from GET /families/{id}/drop-off — "Last step before exit:
// Enroll · Data Collection Consent · Signature". Metadata only: step/form/field
// are STRUCTURAL ids, never a typed value or child key (INV-1/INV-6).
//
// Degrades cleanly (intentional, not broken):
//   · HTTP 204 (no telemetry / in-memory fallback) ⇒ a quiet "No drop-off
//     telemetry" line, never an error or an infinite spinner.
//   · network error / non-ok ⇒ silent (render nothing) — the drop-off is an
//     enrichment, not the panel's primary content.
//   · an unknown payload shape (a stray fetch that resolved to some OTHER body)
//     ⇒ treated as no-telemetry, never masquerades as a drop-off point.

// The GET /families/{id}/drop-off response. 204 ⇒ no body (handled as empty).
interface DropOffPoint {
  family_id: string;
  step: string;
  form_key?: string | null;
  field_key?: string | null;
  event_type: string;
  occurred_at?: string | null;
}

// Only a payload carrying a non-empty `step` string is a drop-off point — so a
// stray fetch stub that serves some other object for every URL does NOT pose as
// telemetry. Same fail-safe posture as DealView's isCrmStatus.
function isDropOffPoint(value: unknown): value is DropOffPoint {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.step === 'string' && v.step.length > 0;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'ready'; point: DropOffPoint };

export default function DropOffPanel({
  familyId,
}: {
  familyId: string;
}): JSX.Element | null {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/families/${familyId}/drop-off`)
      .then((res) => {
        // 204 (no telemetry) or any non-ok ⇒ empty; never throw on these.
        if (res.status === 204 || !res.ok) return null;
        return res.json() as Promise<unknown>;
      })
      .then((data) => {
        if (cancelled) return;
        setState(
          isDropOffPoint(data)
            ? { status: 'ready', point: data }
            : { status: 'empty' },
        );
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'empty' });
      });
    return () => {
      cancelled = true;
    };
  }, [familyId]);

  // While loading, render nothing (no spinner) — the enrichment fills in quietly.
  if (state.status === 'loading') return null;

  if (state.status === 'empty') {
    return (
      <div
        data-testid="dropoff-panel-empty"
        className="lab"
        style={{ marginTop: 'var(--s-3)', color: 'var(--muted)' }}
      >
        No drop-off telemetry
      </div>
    );
  }

  const { step, form_key, field_key } = state.point;
  const path = dropOffPath(step, form_key, field_key);

  return (
    <div
      data-testid="dropoff-panel"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-md)',
      }}
    >
      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          color: 'var(--muted)',
        }}
      >
        <LogOut size={11} aria-hidden /> Last step before exit
      </div>
      <div
        data-testid="dropoff-path"
        className="mono"
        style={{
          marginTop: 'var(--s-1)',
          fontSize: 'var(--fs-sm)',
          color: 'var(--ink)',
        }}
      >
        {path}
      </div>
    </div>
  );
}
