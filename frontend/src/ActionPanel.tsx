import { useState } from 'react';
import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Check,
  Mail,
  Pencil,
  ShieldAlert,
  ShieldOff,
  Trash2,
  Zap,
} from 'lucide-react';
import { apiBaseUrl } from './config';
import { Button } from './ui';

// Enrollment AI action panel (FR-2.4) + fail-closed eval gate (FR-4.5 / INV-3).
//
// The operator requests an AI draft for a family (POST /ai/enrollment/draft).
// The eval gate is enforced VISUALLY and fail-closed: the deterministic core
// surfaces a proposal ONLY if the grounding/safety eval passed (`surfaced`).
//
//   - surfaced + not degraded  → render the draft body with approve/edit/discard.
//   - RED eval (surfaced:false, failed_rules) → NO approvable draft; show the
//     blocked state + offer the deterministic operator template (INV-3 fail
//     closed: a red eval disables the action in the UI).
//   - degraded (no-LLM / kill-switch / cost-cap, NFR-3) → same fail-closed
//     posture: drafting unavailable, deterministic template offered.
//
// Native fetch only (≤2 runtime deps). Presentational + small fetch handlers;
// no state libraries. The deterministic core owns all writes (INV-2) — this
// panel only proposes a draft and records the human decision. S8 Wave 2 re-skin:
// the live-action panel from the reference — mono action buttons with icons, a
// drafted-body well, and signal/gate-tinted fail-closed surfaces.

// One grounded claim attached to a proposal body (ARCHITECTURE §5.2).
interface ProposalClaim {
  text: string;
  source_ref: string | null;
}

// The surfaced proposal payload (present only when the eval gate passed).
interface Proposal {
  action: string;
  family_id: string;
  body: string;
  claims: ProposalClaim[];
}

// POST /ai/enrollment/draft response (matches the API built in parallel).
interface DraftResponse {
  proposal_id: string;
  surfaced: boolean; // true only if the eval gate PASSED
  degraded: boolean; // true if no-LLM / kill-switch / cost-cap → deterministic
  failed_rules: string[]; // e.g. ["v2_grounding"] when blocked
  proposal: Proposal | null;
}

// POST /proposals/{id}/decision response.
interface DecisionResponse {
  decision_id?: string;
  action: string;
  seam_status?: string;
}

type DecisionKind = 'approve' | 'edit' | 'discard';

// The drafting actions an operator can request. Action keys map 1:1 to the
// backend `action` field; each carries the icon the reference uses.
const DRAFT_ACTIONS: ReadonlyArray<
  readonly [key: string, label: string, icon: LucideIcon]
> = [
  ['email', 'Draft email', Mail],
  ['sms', 'Draft SMS', Zap],
];

type DraftState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: DraftResponse };

interface ActionPanelProps {
  familyId: string;
}

export default function ActionPanel({ familyId }: ActionPanelProps): JSX.Element {
  const [state, setState] = useState<DraftState>({ status: 'idle' });
  const [editing, setEditing] = useState(false);
  const [editedBody, setEditedBody] = useState('');
  const [decision, setDecision] = useState<DecisionResponse | null>(null);

  function requestDraft(action: string): void {
    setDecision(null);
    setEditing(false);
    setState({ status: 'loading' });
    fetch(`${apiBaseUrl}/ai/enrollment/draft`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ family_id: familyId, action }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`draft request failed: ${res.status}`);
        return res.json() as Promise<DraftResponse>;
      })
      .then((data) => {
        setEditedBody(data.proposal?.body ?? '');
        setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }

  function submitDecision(
    proposalId: string,
    kind: DecisionKind,
    editedPayload?: string,
  ): void {
    fetch(`${apiBaseUrl}/proposals/${proposalId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action: kind,
        ...(editedPayload !== undefined ? { edited_payload: editedPayload } : {}),
      }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`decision request failed: ${res.status}`);
        return res.json() as Promise<DecisionResponse>;
      })
      .then((data) => setDecision(data))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }

  return (
    <section aria-label="AI action panel" data-testid="action-panel">
      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <Zap size={11} aria-hidden /> Actions — generated live, eval-gated
      </div>
      <h2 style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
        AI actions
      </h2>

      <div
        className="draft-actions"
        style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap' }}
      >
        {DRAFT_ACTIONS.map(([key, label, icon], i) => (
          <Button
            key={key}
            variant={i === 0 ? 'primary' : 'default'}
            icon={icon}
            data-testid={`draft-${key}`}
            onClick={() => requestDraft(key)}
            disabled={state.status === 'loading'}
          >
            {label}
          </Button>
        ))}
      </div>

      {state.status === 'loading' && (
        <p
          data-testid="draft-loading"
          className="mono"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
        >
          Requesting AI draft…
        </p>
      )}

      {state.status === 'error' && (
        <p
          data-testid="draft-error"
          role="alert"
          style={{
            marginTop: 'var(--s-3)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--signal-ink)',
          }}
        >
          Could not request draft: {state.message}
        </p>
      )}

      {state.status === 'ready' && (
        <DraftResult
          data={state.data}
          editing={editing}
          editedBody={editedBody}
          decision={decision}
          onStartEdit={() => setEditing(true)}
          onChangeEdited={setEditedBody}
          onApprove={() => submitDecision(state.data.proposal_id, 'approve')}
          onSaveEdit={() =>
            submitDecision(state.data.proposal_id, 'edit', editedBody)
          }
          onDiscard={() => submitDecision(state.data.proposal_id, 'discard')}
        />
      )}
    </section>
  );
}

interface DraftResultProps {
  data: DraftResponse;
  editing: boolean;
  editedBody: string;
  decision: DecisionResponse | null;
  onStartEdit: () => void;
  onChangeEdited: (value: string) => void;
  onApprove: () => void;
  onSaveEdit: () => void;
  onDiscard: () => void;
}

// The deterministic operator template the UI falls back to whenever the AI
// draft is unavailable (red eval or degraded mode) — never an LLM call, always
// an operator-authored starting point (INV-3 fail closed, NFR-3 fallback).
function TemplateFallback(): JSX.Element {
  return (
    <div
      className="template-fallback"
      data-testid="template-fallback"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3)',
        borderRadius: 'var(--r-md)',
        border: '1px solid var(--line)',
        background: 'var(--surface-2)',
      }}
    >
      <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink-soft)', marginBottom: 'var(--s-2)' }}>
        AI drafting is unavailable. Continue with the operator template:
      </p>
      <Button data-testid="use-template">Use operator template</Button>
    </div>
  );
}

// A fail-closed banner shell — a tinted wash with an icon + heading + body.
function FailClosedBanner({
  testId,
  role,
  tone,
  icon: Icon,
  children,
}: {
  testId: string;
  role: 'alert' | 'status';
  tone: 'signal' | 'gate';
  icon: LucideIcon;
  children: ReactNode;
}): JSX.Element {
  const wash = tone === 'signal' ? 'var(--signal-wash)' : 'var(--gate-wash)';
  const solid = tone === 'signal' ? 'var(--signal)' : 'var(--gate)';
  const ink = tone === 'signal' ? 'var(--signal-ink)' : 'var(--gate-ink)';
  return (
    <div
      data-testid={testId}
      role={role}
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        borderRadius: 'var(--r-md)',
        background: wash,
        border: `1px solid ${solid}`,
        color: ink,
      }}
    >
      <div
        className="lab"
        style={{
          color: ink,
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          marginBottom: 'var(--s-1)',
        }}
      >
        <Icon size={11} aria-hidden /> Action disabled — fail closed
      </div>
      {children}
    </div>
  );
}

function DraftResult({
  data,
  editing,
  editedBody,
  decision,
  onStartEdit,
  onChangeEdited,
  onApprove,
  onSaveEdit,
  onDiscard,
}: DraftResultProps): JSX.Element {
  // Fail-closed: kill-switch / cost-cap / no-LLM degraded mode disables the AI
  // draft entirely and offers the deterministic template (NFR-3).
  if (data.degraded) {
    return (
      <FailClosedBanner
        testId="proposal-degraded"
        role="status"
        tone="gate"
        icon={ShieldOff}
      >
        <p style={{ fontSize: 'var(--fs-sm)' }}>
          AI drafting is in <strong>degraded mode</strong> (no-LLM /
          kill-switch / cost cap). The AI draft action is disabled.
        </p>
        <TemplateFallback />
      </FailClosedBanner>
    );
  }

  // Fail-closed: a RED eval (gate did not surface the proposal) disables the AI
  // draft and shows the failed rule (INV-3). No approvable body is rendered.
  if (!data.surfaced || data.proposal === null) {
    return (
      <FailClosedBanner
        testId="proposal-blocked"
        role="alert"
        tone="signal"
        icon={ShieldAlert}
      >
        <p style={{ fontSize: 'var(--fs-sm)' }}>
          The AI draft was <strong>blocked by the eval gate</strong> and cannot
          be sent.
        </p>
        {data.failed_rules.length > 0 && (
          <ul
            className="failed-rules"
            data-testid="failed-rules"
            style={{
              margin: 'var(--s-2) 0 0',
              paddingLeft: 'var(--s-5)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            {data.failed_rules.map((rule) => (
              <li key={rule} className="mono">
                {rule}
              </li>
            ))}
          </ul>
        )}
        <TemplateFallback />
      </FailClosedBanner>
    );
  }

  // Surfaced: the eval passed — render the approvable draft.
  const proposal = data.proposal;

  if (decision) {
    return (
      <p
        data-testid="decision-recorded"
        role="status"
        style={{
          marginTop: 'var(--s-3)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          fontSize: 'var(--fs-sm)',
          color: 'var(--flow-ink)',
          background: 'var(--flow-wash)',
          border: '1px solid var(--flow)',
          borderRadius: 'var(--r-md)',
          padding: 'var(--s-2) var(--s-3)',
        }}
      >
        <Check size={13} aria-hidden /> Decision recorded: {decision.action}
        {decision.seam_status ? ` (seam: ${decision.seam_status})` : ''}
      </p>
    );
  }

  return (
    <article
      data-testid="proposal"
      className="proposal"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3)',
        borderRadius: 'var(--r-md)',
        border: '1px solid var(--line)',
        background: 'var(--surface-2)',
      }}
    >
      {editing ? (
        <textarea
          data-testid="proposal-edit"
          value={editedBody}
          onChange={(e) => onChangeEdited(e.target.value)}
          rows={6}
          style={{
            width: '100%',
            fontFamily: 'var(--sans)',
            fontSize: 'var(--fs-body)',
            color: 'var(--ink)',
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-2)',
            resize: 'vertical',
          }}
        />
      ) : (
        <p
          data-testid="proposal-body"
          style={{ fontSize: 'var(--fs-body)', color: 'var(--ink)', whiteSpace: 'pre-wrap' }}
        >
          {proposal.body}
        </p>
      )}

      {proposal.claims.length > 0 && (
        <ul
          className="proposal-claims"
          data-testid="proposal-claims"
          style={{
            margin: 'var(--s-3) 0 0',
            paddingLeft: 'var(--s-5)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--muted)',
          }}
        >
          {proposal.claims.map((claim) => (
            <li key={claim.text}>
              {claim.text}
              {claim.source_ref ? ` — ${claim.source_ref}` : ''}
            </li>
          ))}
        </ul>
      )}

      <div
        className="proposal-decisions"
        style={{ display: 'flex', gap: 'var(--s-2)', marginTop: 'var(--s-3)', flexWrap: 'wrap' }}
      >
        {editing ? (
          <Button
            variant="signal"
            icon={Check}
            data-testid="save-edit"
            onClick={onSaveEdit}
          >
            Save &amp; approve edit
          </Button>
        ) : (
          <Button
            variant="signal"
            icon={Check}
            data-testid="approve-action"
            onClick={onApprove}
          >
            Approve
          </Button>
        )}
        <Button icon={Pencil} data-testid="edit-action" onClick={onStartEdit}>
          Edit
        </Button>
        <Button icon={Trash2} data-testid="discard-action" onClick={onDiscard}>
          Discard
        </Button>
      </div>
    </article>
  );
}
