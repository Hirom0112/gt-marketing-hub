import { useEffect, useState } from 'react';
import { Lightbulb, ShieldAlert } from 'lucide-react';
import { apiFetch } from '../config';
import { Button } from '../ui';

// Eval-gated "how to close" tips (S9 Wave 5; FR-4.3 / INV-2/3/4).
//
// The operator requests grounded "how to close this family" tips
// (POST /ai/enrollment/close-tips). Every tip is a schema-validated PROPOSAL
// grounded in the family's app_form.extracted_fields — never a state write
// (INV-2). The eval gate is enforced VISUALLY and fail-closed in two layers:
//
//   1. SUITE-LEVEL kill (FR-4.5 / INV-3): the panel fetches GET /evals and reads
//      `disabled['close_tips']`. When that consolidated row is RED the "Close
//      tips" action is DISABLED with the standard disabled treatment (a red
//      notice + a disabled button) — a red eval disables the action in the UI.
//   2. PER-PROPOSAL gate (INV-4): when the action runs, the backend surfaces tips
//      ONLY if the grounding/safety gate passed. On a block (`surfaced:false`) the
//      panel shows the failed rules and no approvable tips — blocked, not softened.
//
// Native fetch only (≤2 runtime deps). Read/propose only — this panel proposes
// tips and renders the server's verdict; the deterministic core owns all writes.

// The consolidated eval whose RED row disables the close-tips action (INV-3).
const GATING_EVAL = 'close_tips';

interface EvalScoreboard {
  rows: { eval_name: string; score: number; threshold: number; passed: boolean }[];
  overall_green: boolean;
  disabled: { [evalName: string]: boolean };
}

// One grounded tip + the extracted_fields key it rests on (V-2 grounding).
interface CloseTip {
  text: string;
  source_ref: string | null;
}

// The surfaced close-tips proposal (present only when the eval gate passed).
interface CloseTipsProposal {
  family_id: string;
  tips: CloseTip[];
}

// POST /ai/enrollment/close-tips response (matches the API contract).
interface CloseTipsResponse {
  proposal_id: string;
  surfaced: boolean; // true only if the eval gate PASSED
  degraded: boolean; // true if no-LLM / kill-switch / cost-cap
  failed_rules: string[]; // e.g. ["v2_grounding", "close_tips_grounding"]
  proposal: CloseTipsProposal | null;
}

type TipsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: CloseTipsResponse };

interface CloseTipsPanelProps {
  familyId: string;
}

export default function CloseTipsPanel({
  familyId,
}: CloseTipsPanelProps): JSX.Element {
  // The suite-level kill state for the close_tips row (true ⇒ red ⇒ disabled).
  const [gatingRed, setGatingRed] = useState(false);
  const [state, setState] = useState<TipsState>({ status: 'idle' });

  // Fetch the consolidated scoreboard to learn whether close_tips is RED. The
  // per-proposal gate still guards every request; this only drives the UI's
  // fail-closed disabled treatment (INV-3). A failed fetch leaves the action
  // enabled (the per-proposal gate is the hard guard).
  useEffect(() => {
    let cancelled = false;
    apiFetch(`/evals`)
      .then((res) => {
        if (!res.ok) throw new Error(`evals request failed: ${res.status}`);
        return res.json() as Promise<EvalScoreboard>;
      })
      .then((data) => {
        if (!cancelled) setGatingRed(data.disabled[GATING_EVAL] === true);
      })
      .catch(() => {
        /* leave enabled; the per-proposal gate is the hard guard */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function requestTips(): void {
    setState({ status: 'loading' });
    apiFetch(`/ai/enrollment/close-tips`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ family_id: familyId }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`close-tips request failed: ${res.status}`);
        return res.json() as Promise<CloseTipsResponse>;
      })
      .then((data) => setState({ status: 'ready', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }

  return (
    <section aria-label="Close tips panel" data-testid="close-tips-panel">
      <div
        className="lab"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <Lightbulb size={11} aria-hidden /> How to close — grounded, eval-gated
      </div>

      {gatingRed && (
        // INV-3 fail closed: the consolidated close_tips eval is RED ⇒ the action
        // is disabled with the standard disabled treatment (red notice).
        <p
          data-testid="close-tips-eval-blocked"
          role="alert"
          style={{
            display: 'flex',
            gap: 'var(--s-2)',
            alignItems: 'flex-start',
            margin: '0 0 var(--s-3)',
            padding: 'var(--s-3)',
            borderRadius: 'var(--r-md)',
            background: 'var(--signal-wash)',
            border: '1px solid var(--signal)',
            color: 'var(--signal-ink)',
            fontSize: 'var(--fs-sm)',
          }}
        >
          <ShieldAlert size={16} aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            The <strong>{GATING_EVAL}</strong> eval is <strong>red</strong> — the
            close-tips action is disabled until the eval passes. Fail closed: a red
            eval disables the action in the UI.
          </span>
        </p>
      )}

      <Button
        variant="primary"
        icon={Lightbulb}
        data-testid="close-tips-action"
        onClick={requestTips}
        disabled={gatingRed || state.status === 'loading'}
      >
        How to close this family
      </Button>

      {state.status === 'loading' && (
        <p
          data-testid="close-tips-loading"
          className="mono"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
        >
          Generating grounded close tips…
        </p>
      )}

      {state.status === 'error' && (
        <p
          data-testid="close-tips-error"
          role="alert"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
        >
          Could not generate tips: {state.message}
        </p>
      )}

      {state.status === 'ready' && <CloseTipsResult data={state.data} />}
    </section>
  );
}

function CloseTipsResult({ data }: { data: CloseTipsResponse }): JSX.Element {
  // Fail-closed: blocked by the per-proposal gate (or suite kill) ⇒ no tips.
  if (!data.surfaced || data.proposal === null) {
    return (
      <div
        data-testid="close-tips-blocked"
        role="alert"
        style={{
          marginTop: 'var(--s-3)',
          padding: 'var(--s-3) var(--s-4)',
          borderRadius: 'var(--r-md)',
          background: 'var(--signal-wash)',
          border: '1px solid var(--signal)',
          color: 'var(--signal-ink)',
        }}
      >
        <div
          className="lab"
          style={{
            color: 'var(--signal-ink)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            marginBottom: 'var(--s-1)',
          }}
        >
          <ShieldAlert size={11} aria-hidden /> Tips blocked — fail closed
        </div>
        <p style={{ fontSize: 'var(--fs-sm)' }}>
          The close tips were <strong>blocked by the eval gate</strong> (a tip was
          ungrounded or unsafe) and cannot be shown.
        </p>
        {data.failed_rules.length > 0 && (
          <ul
            className="failed-rules"
            data-testid="close-tips-failed-rules"
            style={{ margin: 'var(--s-2) 0 0', paddingLeft: 'var(--s-5)', fontSize: 'var(--fs-sm)' }}
          >
            {data.failed_rules.map((rule) => (
              <li key={rule} className="mono">
                {rule}
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  // Surfaced: the eval passed — render the grounded tips read-only (advisory).
  return (
    <ul
      data-testid="close-tips-list"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3)',
        paddingLeft: 'var(--s-5)',
        borderRadius: 'var(--r-md)',
        border: '1px solid var(--line)',
        background: 'var(--surface-2)',
        fontSize: 'var(--fs-body)',
        color: 'var(--ink)',
        display: 'grid',
        gap: 'var(--s-2)',
      }}
    >
      {data.proposal.tips.map((tip) => (
        <li key={tip.text}>
          {tip.text}
          {tip.source_ref && (
            <span className="mono" style={{ color: 'var(--muted)', fontSize: 'var(--fs-sm)' }}>
              {' '}
              — {tip.source_ref}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
