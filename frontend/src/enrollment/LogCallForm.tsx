import { useState } from 'react';
import { apiFetch } from '../config';
import { Button } from '../ui';

// LogCallForm — the ONE shared "log a call / contact outcome" form (R1; CLAUDE.md
// §7 reuse mandate). Extracted from DealView's inline contact-outcome flow so the
// SAME implementation is consumed by both DealView (enrollment deal panel) and the
// redesign DetailPanel (dashboard right column). It POSTs the append-only spine
// event `POST /families/{id}/contact-outcome` with `{channel, disposition, note}`;
// the deterministic core owns the write (INV-2). On success it clears the note and
// notifies the parent (onLogged) so it can refresh. It does NOT own the closed-out
// guard or the confirm-presumed-lost gate — those stay in DealView's wrapper.
//
// The testids are kept as `deal-outcome-*` so DealView's existing acceptance tests
// (which drive the channel/disposition/note + submit) stay green after the extract.

const OUTCOME_CHANNELS: readonly { value: string; label: string }[] = [
  { value: 'sms', label: 'Text' },
  { value: 'email', label: 'Email' },
  { value: 'call', label: 'Call' },
];
const OUTCOME_DISPOSITIONS: readonly { value: string; label: string }[] = [
  { value: 'no_answer', label: 'No answer' },
  { value: 'no_reply', label: 'No reply' },
  { value: 'voicemail', label: 'Left voicemail' },
  { value: 'reached', label: 'Reached' },
  { value: 'committed_to_pay', label: 'Committed to pay' },
  { value: 'wrong_number', label: 'Wrong number' },
  { value: 'declined', label: 'Declined' },
];

interface LogCallFormProps {
  familyId: string;
  // Notified after a successful contact-outcome write so the parent can refresh
  // (re-fetch the deal_view, bump a notes-timeline nonce, refresh the board, …).
  onLogged?: () => void;
}

export default function LogCallForm({
  familyId,
  onLogged,
}: LogCallFormProps): JSX.Element {
  const [channel, setChannel] = useState('sms');
  const [disposition, setDisposition] = useState('no_answer');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function logOutcome(): void {
    setBusy(true);
    setError(null);
    apiFetch(`/families/${familyId}/contact-outcome`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel, disposition, note }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`log outcome failed: ${res.status}`);
        return res.json() as Promise<unknown>;
      })
      .then(() => {
        setNote('');
        onLogged?.();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'unknown error');
      })
      .finally(() => setBusy(false));
  }

  return (
    <div data-testid="log-call-form">
      <div
        style={{
          display: 'flex',
          gap: 'var(--s-2)',
          flexWrap: 'wrap',
          alignItems: 'center',
        }}
      >
        <select
          aria-label="Channel"
          data-testid="deal-outcome-channel"
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
          style={{ fontFamily: 'inherit', fontSize: 'var(--fs-sm)' }}
        >
          {OUTCOME_CHANNELS.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Outcome"
          data-testid="deal-outcome-disposition"
          value={disposition}
          onChange={(e) => setDisposition(e.target.value)}
          style={{ fontFamily: 'inherit', fontSize: 'var(--fs-sm)' }}
        >
          {OUTCOME_DISPOSITIONS.map((d) => (
            <option key={d.value} value={d.value}>
              {d.label}
            </option>
          ))}
        </select>
        <input
          aria-label="Note"
          data-testid="deal-outcome-note"
          placeholder="note (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          style={{
            flex: '1 1 140px',
            fontFamily: 'inherit',
            fontSize: 'var(--fs-sm)',
            padding: '4px 8px',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-sm)',
          }}
        />
        <Button
          data-testid="deal-outcome-submit"
          onClick={logOutcome}
          disabled={busy}
        >
          {busy ? 'Logging…' : 'Log'}
        </Button>
      </div>
      {error !== null && (
        <span
          data-testid="deal-outcome-error"
          role="alert"
          style={{
            display: 'block',
            marginTop: 'var(--s-2)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--signal-ink)',
          }}
        >
          {error}
        </span>
      )}
    </div>
  );
}
