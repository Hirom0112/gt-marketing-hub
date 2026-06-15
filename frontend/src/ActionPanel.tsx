import { useState } from 'react';
import { apiBaseUrl } from './config';

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
// panel only proposes a draft and records the human decision.

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
// backend `action` field.
const DRAFT_ACTIONS: ReadonlyArray<readonly [key: string, label: string]> = [
  ['email', 'Draft email'],
  ['sms', 'Draft SMS'],
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
      <h2>AI actions</h2>

      <div className="draft-actions">
        {DRAFT_ACTIONS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            data-testid={`draft-${key}`}
            onClick={() => requestDraft(key)}
            disabled={state.status === 'loading'}
          >
            {label}
          </button>
        ))}
      </div>

      {state.status === 'loading' && (
        <p data-testid="draft-loading">Requesting AI draft…</p>
      )}

      {state.status === 'error' && (
        <p data-testid="draft-error" role="alert">
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
          onApprove={() =>
            submitDecision(state.data.proposal_id, 'approve')
          }
          onSaveEdit={() =>
            submitDecision(state.data.proposal_id, 'edit', editedBody)
          }
          onDiscard={() =>
            submitDecision(state.data.proposal_id, 'discard')
          }
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
    <div className="template-fallback" data-testid="template-fallback">
      <p>AI drafting is unavailable. Continue with the operator template:</p>
      <button type="button" data-testid="use-template">
        Use operator template
      </button>
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
      <div data-testid="proposal-degraded" role="status">
        <p>
          AI drafting is in <strong>degraded mode</strong> (no-LLM /
          kill-switch / cost cap). The AI draft action is disabled.
        </p>
        <TemplateFallback />
      </div>
    );
  }

  // Fail-closed: a RED eval (gate did not surface the proposal) disables the AI
  // draft and shows the failed rule (INV-3). No approvable body is rendered.
  if (!data.surfaced || data.proposal === null) {
    return (
      <div data-testid="proposal-blocked" role="alert">
        <p>
          The AI draft was <strong>blocked by the eval gate</strong> and cannot
          be sent.
        </p>
        {data.failed_rules.length > 0 && (
          <ul className="failed-rules" data-testid="failed-rules">
            {data.failed_rules.map((rule) => (
              <li key={rule}>{rule}</li>
            ))}
          </ul>
        )}
        <TemplateFallback />
      </div>
    );
  }

  // Surfaced: the eval passed — render the approvable draft.
  const proposal = data.proposal;

  if (decision) {
    return (
      <p data-testid="decision-recorded" role="status">
        Decision recorded: {decision.action}
        {decision.seam_status ? ` (seam: ${decision.seam_status})` : ''}
      </p>
    );
  }

  return (
    <article data-testid="proposal" className="proposal">
      {editing ? (
        <textarea
          data-testid="proposal-edit"
          value={editedBody}
          onChange={(e) => onChangeEdited(e.target.value)}
        />
      ) : (
        <p data-testid="proposal-body">{proposal.body}</p>
      )}

      {proposal.claims.length > 0 && (
        <ul className="proposal-claims" data-testid="proposal-claims">
          {proposal.claims.map((claim) => (
            <li key={claim.text}>
              {claim.text}
              {claim.source_ref ? ` — ${claim.source_ref}` : ''}
            </li>
          ))}
        </ul>
      )}

      <div className="proposal-decisions">
        {editing ? (
          <button type="button" data-testid="save-edit" onClick={onSaveEdit}>
            Save &amp; approve edit
          </button>
        ) : (
          <button type="button" data-testid="approve-action" onClick={onApprove}>
            Approve
          </button>
        )}
        <button type="button" data-testid="edit-action" onClick={onStartEdit}>
          Edit
        </button>
        <button type="button" data-testid="discard-action" onClick={onDiscard}>
          Discard
        </button>
      </div>
    </article>
  );
}
