import { useEffect, useState } from 'react';
import { apiBaseUrl } from '../config';

// Content workspace (FR-3.1/3.4/3.5, FR-4.5 / INV-3 / INV-4 fail-closed).
//
// The marketing operator enters a prompt → POST /ai/content/generate returns a
// BATCH of candidates → the operator KEEPS (== approve; lands in the library +
// conditions the next batch) or DISCARDS each one.
//
// The grounding/safety eval is enforced VISUALLY and fail-closed:
//   - surfaced candidate (surfaced:true, validation.passed:true) → renders its
//     copy with keep/discard controls.
//   - BLOCKED candidate (surfaced:false, failed_rules) → renders a blocked state
//     showing the failing rule and offers NO keep action (INV-4: the gate
//     blocks, never softens; a blocked candidate is never keepable).
//   - DEGRADED batch (degraded:true — no-LLM / kill-switch / cost-cap, NFR-3) →
//     a degraded notice renders over the deterministic fallback set; the AI
//     generate result is presented as the fallback, not a live LLM batch.
//
// Native fetch only (≤2 runtime deps). The deterministic core owns all writes
// (INV-2) — this UI only proposes a batch and records the human keep/discard.

// One candidate in a generated batch (matches the API built in parallel).
interface ContentCandidateView {
  proposal_id: string;
  copy: string;
  channel: string;
  surfaced: boolean;
  degraded: boolean;
  failed_rules: string[];
  validation: { passed: boolean } | null;
}

// POST /ai/content/generate response.
interface GenerateResponse {
  batch_id: string;
  candidates: ContentCandidateView[];
  blocked_count: number;
  degraded: boolean;
}

// One kept+validated asset in the content library (GET /content/library).
interface LibraryAsset {
  id: string;
  title: string;
  asset_type: string;
  search_text: string;
}

// POST /proposals/{id}/decision response.
interface DecisionResponse {
  proposal_id: string;
  action: string;
}

type DecisionKind = 'approve' | 'discard';

type BatchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: GenerateResponse };

type LibraryState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; assets: LibraryAsset[] };

export default function ContentWorkspace(): JSX.Element {
  const [prompt, setPrompt] = useState('');
  const [batch, setBatch] = useState<BatchState>({ status: 'idle' });
  const [library, setLibrary] = useState<LibraryState>({ status: 'loading' });
  // proposal_id → recorded decision; keeps the kept/discarded affordance.
  const [decisions, setDecisions] = useState<Record<string, DecisionKind>>({});
  const [libraryNonce, setLibraryNonce] = useState(0);

  // Load (and refresh) the library of kept+validated assets.
  useEffect(() => {
    let cancelled = false;
    setLibrary({ status: 'loading' });
    fetch(`${apiBaseUrl}/content/library?q=`)
      .then((res) => {
        if (!res.ok) throw new Error(`library request failed: ${res.status}`);
        return res.json() as Promise<LibraryAsset[]>;
      })
      .then((assets) => {
        if (!cancelled) setLibrary({ status: 'ready', assets });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setLibrary({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [libraryNonce]);

  function generate(): void {
    setDecisions({});
    setBatch({ status: 'loading' });
    fetch(`${apiBaseUrl}/ai/content/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`generate request failed: ${res.status}`);
        return res.json() as Promise<GenerateResponse>;
      })
      .then((data) => setBatch({ status: 'ready', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setBatch({ status: 'error', message });
      });
  }

  function decide(proposalId: string, kind: DecisionKind): void {
    fetch(`${apiBaseUrl}/proposals/${proposalId}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: kind }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`decision request failed: ${res.status}`);
        return res.json() as Promise<DecisionResponse>;
      })
      .then(() => {
        setDecisions((prev) => ({ ...prev, [proposalId]: kind }));
        // A kept candidate lands in the library — refresh it.
        if (kind === 'approve') setLibraryNonce((n) => n + 1);
      })
      .catch(() => {
        // Decision failures leave the candidate keepable for a retry.
      });
  }

  return (
    <section aria-label="Content workspace" data-testid="content-workspace">
      <h2>Content workspace</h2>

      <div className="content-generate">
        <textarea
          data-testid="content-prompt"
          aria-label="Content prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe the content to generate…"
        />
        <button
          type="button"
          data-testid="content-generate"
          onClick={generate}
          disabled={batch.status === 'loading'}
        >
          Generate batch
        </button>
      </div>

      {batch.status === 'loading' && (
        <p data-testid="batch-loading">Generating candidates…</p>
      )}

      {batch.status === 'error' && (
        <p data-testid="batch-error" role="alert">
          Could not generate: {batch.message}
        </p>
      )}

      {batch.status === 'ready' && (
        <BatchResult
          data={batch.data}
          decisions={decisions}
          onKeep={(id) => decide(id, 'approve')}
          onDiscard={(id) => decide(id, 'discard')}
        />
      )}

      <LibraryPanel state={library} />
    </section>
  );
}

interface BatchResultProps {
  data: GenerateResponse;
  decisions: Record<string, DecisionKind>;
  onKeep: (proposalId: string) => void;
  onDiscard: (proposalId: string) => void;
}

function BatchResult({
  data,
  decisions,
  onKeep,
  onDiscard,
}: BatchResultProps): JSX.Element {
  return (
    <div className="content-batch" data-testid="content-batch">
      {data.degraded && (
        <p data-testid="batch-degraded" role="status">
          Generation is in <strong>degraded mode</strong> (no-LLM / kill-switch
          / cost cap). These are deterministic fallback candidates, not a live
          AI batch.
        </p>
      )}

      <ul className="candidate-list">
        {data.candidates.map((candidate) =>
          candidate.surfaced && candidate.validation?.passed ? (
            <SurfacedCandidate
              key={candidate.proposal_id}
              candidate={candidate}
              decision={decisions[candidate.proposal_id]}
              onKeep={() => onKeep(candidate.proposal_id)}
              onDiscard={() => onDiscard(candidate.proposal_id)}
            />
          ) : (
            <BlockedCandidate key={candidate.proposal_id} candidate={candidate} />
          ),
        )}
      </ul>
    </div>
  );
}

interface SurfacedCandidateProps {
  candidate: ContentCandidateView;
  decision: DecisionKind | undefined;
  onKeep: () => void;
  onDiscard: () => void;
}

function SurfacedCandidate({
  candidate,
  decision,
  onKeep,
  onDiscard,
}: SurfacedCandidateProps): JSX.Element {
  const id = candidate.proposal_id;
  return (
    <li
      className="candidate surfaced"
      data-testid={`candidate-${id}`}
      data-decision={decision ?? 'pending'}
    >
      <p className="candidate-copy" data-testid={`candidate-copy-${id}`}>
        {candidate.copy}
      </p>
      {decision ? (
        <p data-testid={`candidate-decided-${id}`} role="status">
          {decision === 'approve' ? 'Kept — added to library' : 'Discarded'}
        </p>
      ) : (
        <div className="candidate-controls">
          <button type="button" data-testid={`keep-${id}`} onClick={onKeep}>
            Keep
          </button>
          <button
            type="button"
            data-testid={`discard-${id}`}
            onClick={onDiscard}
          >
            Discard
          </button>
        </div>
      )}
    </li>
  );
}

// A blocked candidate (INV-4 fail closed): the grounding/safety gate BLOCKED it.
// Its copy is not offered for keeping — only the blocked state and failing rule
// render. There is deliberately NO keep control here.
function BlockedCandidate({
  candidate,
}: {
  candidate: ContentCandidateView;
}): JSX.Element {
  const id = candidate.proposal_id;
  return (
    <li
      className="candidate blocked"
      data-testid={`candidate-blocked-${id}`}
      role="alert"
    >
      <p>
        This candidate was <strong>blocked by the content gate</strong> and
        cannot be kept.
      </p>
      {candidate.failed_rules.length > 0 && (
        <ul className="failed-rules" data-testid={`failed-rules-${id}`}>
          {candidate.failed_rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function LibraryPanel({ state }: { state: LibraryState }): JSX.Element {
  return (
    <div className="content-library" data-testid="content-library">
      <h3>Library</h3>
      {state.status === 'loading' && (
        <p data-testid="library-loading">Loading library…</p>
      )}
      {state.status === 'error' && (
        <p data-testid="library-error" role="alert">
          Could not load library: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.assets.length === 0 ? (
          <p data-testid="library-empty">No kept assets yet.</p>
        ) : (
          <ul className="library-list">
            {state.assets.map((asset) => (
              <li
                key={asset.id}
                className="library-asset"
                data-testid={`library-asset-${asset.id}`}
              >
                <span className="library-asset-title">{asset.title}</span>
                <span className="library-asset-type">{asset.asset_type}</span>
              </li>
            ))}
          </ul>
        ))}
    </div>
  );
}
