import { useState } from 'react';
import { apiFetch } from '../config';
import { Button } from '../ui';

// Log a call (admin-dashboard redesign §12). Reuses the EXACT contact-outcome
// flow DealView already drives — POST /families/{id}/contact-outcome with the same
// channel/disposition taxonomy (mirrors the backend ContactChannel /
// ContactDisposition enums). The deterministic core owns the append-only spine
// write (INV-2); this panel only records what happened + the resulting action and
// signals the parent (onLogged) to refresh. Extracted into its own component so the
// new detail panel reuses the flow without the rest of DealView's admin chrome.

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

interface LogCallPanelProps {
  familyId: string;
  // Notified after a successful contact-outcome write so the parent can refresh.
  onLogged?: () => void;
}

export default function LogCallPanel({
  familyId,
  onLogged,
}: LogCallPanelProps): JSX.Element {
  const [channel, setChannel] = useState('sms');
  const [disposition, setDisposition] = useState('no_answer');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loggedAt, setLoggedAt] = useState<number | null>(null);

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
        setLoggedAt(Date.now());
        onLogged?.();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'unknown error');
      })
      .finally(() => setBusy(false));
  }

  return (
    <div data-testid="admin-log-outcome">
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
          data-testid="admin-outcome-channel"
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
          data-testid="admin-outcome-disposition"
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
          data-testid="admin-outcome-note"
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
          data-testid="admin-outcome-submit"
          onClick={logOutcome}
          disabled={busy}
        >
          {busy ? 'Logging…' : 'Log'}
        </Button>
      </div>
      {loggedAt !== null && error === null && (
        <span
          data-testid="admin-outcome-ok"
          role="status"
          className="lab"
          style={{ display: 'block', marginTop: 'var(--s-2)', color: 'var(--flow-ink)' }}
        >
          Logged.
        </span>
      )}
      {error !== null && (
        <span
          data-testid="admin-outcome-error"
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
