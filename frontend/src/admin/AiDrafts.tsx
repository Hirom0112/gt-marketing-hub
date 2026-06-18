import { useState } from 'react';
import { Mail, MessageSquare, Sparkles } from 'lucide-react';
import { apiFetch } from '../config';
import { Button } from '../ui';

// AI drafts (admin-dashboard redesign §10; D-1). Generates an EMAIL and an SMS
// draft via POST /ai/enrollment/draft-ungated {family_id, channel}. There is NO
// eval gate and NO auto-send on this surface (brief + D-1): the body is always
// surfaced into an editable textarea and the human copies/sends manually. A small
// note appears when the response is `degraded` (the metered LLM was unavailable /
// capped and an operator template stands in). The proposal is still logged
// server-side for the audit (NFR-6); the panel never writes state itself (INV-2).

interface UngatedDraftResponse {
  proposal_id: string;
  channel: string;
  degraded: boolean;
  body: string;
  claims: unknown[];
}

type DraftStatus = 'idle' | 'loading' | 'error';

interface DraftState {
  status: DraftStatus;
  body: string;
  degraded: boolean;
  error?: string;
}

const EMPTY: DraftState = { status: 'idle', body: '', degraded: false };

interface DraftBlockProps {
  familyId: string;
  channel: 'email' | 'sms';
  label: string;
  icon: typeof Mail;
}

function DraftBlock({ familyId, channel, label, icon: Icon }: DraftBlockProps): JSX.Element {
  const [state, setState] = useState<DraftState>(EMPTY);

  function generate(): void {
    setState((s) => ({ ...s, status: 'loading', error: undefined }));
    apiFetch(`/ai/enrollment/draft-ungated`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ family_id: familyId, channel }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`draft failed: ${res.status}`);
        return res.json() as Promise<UngatedDraftResponse>;
      })
      .then((data) => {
        setState({
          status: 'idle',
          body: data.body,
          degraded: data.degraded === true,
        });
      })
      .catch((err: unknown) => {
        setState((s) => ({
          ...s,
          status: 'error',
          error: err instanceof Error ? err.message : 'unknown error',
        }));
      });
  }

  return (
    <div className="admin-draft" data-testid={`ai-draft-${channel}`}>
      <div className="admin-toolbar">
        <span
          className="lab"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-1)' }}
        >
          <Icon size={11} aria-hidden /> {label}
        </span>
        <Button
          icon={Sparkles}
          data-testid={`ai-draft-generate-${channel}`}
          onClick={generate}
          disabled={state.status === 'loading'}
          style={{ marginLeft: 'auto' }}
        >
          {state.status === 'loading' ? 'Drafting…' : 'Draft'}
        </Button>
      </div>
      {state.degraded && (
        <span
          data-testid={`ai-draft-degraded-${channel}`}
          className="lab"
          style={{ color: 'var(--gate-ink)' }}
        >
          LLM unavailable — operator template stands in. Edit before you send.
        </span>
      )}
      {state.status === 'error' && (
        <span
          data-testid={`ai-draft-error-${channel}`}
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not draft: {state.error}
        </span>
      )}
      <textarea
        className="admin-draft-area"
        data-testid={`ai-draft-body-${channel}`}
        aria-label={`${label} draft`}
        placeholder={`Generate a ${label.toLowerCase()}, then edit before sending manually.`}
        value={state.body}
        onChange={(e) => setState((s) => ({ ...s, body: e.target.value }))}
      />
    </div>
  );
}

export default function AiDrafts({ familyId }: { familyId: string }): JSX.Element {
  return (
    <div data-testid="ai-drafts" style={{ display: 'grid', gap: 'var(--s-3)' }}>
      <DraftBlock familyId={familyId} channel="email" label="Email" icon={Mail} />
      <DraftBlock
        familyId={familyId}
        channel="sms"
        label="SMS"
        icon={MessageSquare}
      />
    </div>
  );
}
