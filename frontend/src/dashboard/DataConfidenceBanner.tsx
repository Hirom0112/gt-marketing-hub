import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { apiFetch } from '../config';

// Cross-module data-confidence banner (TODO_v2 §A4). Quiet, non-blocking chrome
// that surfaces ONE thing: when the CRM↔cockpit sync parity has dropped below the
// trusted threshold, the operator must know the numbers they're reading may be
// stale. It reads GET /crm/status (the same endpoint HouseholdReconcileBoard
// reads), and renders ONLY when the backend says `data_confidence_banner` is
// true — the threshold decision lives server-side (INV-11 spirit: no magic
// number duplicated in the client). When parity is healthy, or the status read
// fails, it renders NOTHING — a banner that can't load its own status must never
// block the dashboard (fail-safe). Reuses the existing .dash-banner theme class
// with a warning (signal) treatment, matching the CRM-down notice already in
// HouseholdReconcileBoard.

// GET /crm/status (backend app/api/crm_status.py). Extends the shape read by
// HouseholdReconcileBoard with the three parity fields A4 added.
interface CrmStatus {
  crm_mode: string;
  kill_switch: boolean;
  effective_mode: string;
  token_configured: boolean;
  calls_per_run_cap: number;
  parity_overall: number;
  parity_by_field: Record<string, number>;
  data_confidence_banner: boolean;
}

export default function DataConfidenceBanner(): JSX.Element | null {
  const [status, setStatus] = useState<CrmStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch('/crm/status')
      .then((res) => {
        if (!res.ok) throw new Error(`crm status request failed: ${res.status}`);
        return res.json() as Promise<CrmStatus>;
      })
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        // Fail safe: a status read that fails leaves the banner hidden — it must
        // never crash or block the dashboard it sits on top of.
        if (!cancelled) setStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (status === null || !status.data_confidence_banner) return null;

  // parity_overall is a 0..1 ratio → one-decimal percent (e.g. 0.842 → 84.2%).
  const parityPct = Math.round(status.parity_overall * 1000) / 10;

  return (
    <div
      className="dash-banner"
      data-testid="data-confidence-banner"
      role="alert"
      style={{
        background: 'var(--signal-wash)',
        border: '1px solid var(--signal)',
        color: 'var(--signal-ink)',
      }}
    >
      <AlertTriangle size={16} aria-hidden style={{ flexShrink: 0 }} />
      <span style={{ flex: 1, minWidth: 0 }}>
        CRM↔cockpit sync parity has dropped to{' '}
        <strong data-testid="data-confidence-parity">{parityPct}%</strong>, below
        the trusted threshold · figures may be stale until the seam reconciles.
      </span>
    </div>
  );
}
