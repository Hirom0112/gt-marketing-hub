import { useEffect, useState } from 'react';
import {
  Ban,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Button, Card, Chip, PlaceholderBadge } from '../ui';

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
// body/source_ref/tags power the expand-to-read + link-to-source affordance;
// they are optional because a kept-from-generation asset may omit a source.
interface LibraryAsset {
  id: string;
  title: string;
  asset_type: string;
  channel?: string | null;
  body?: string | null;
  source_ref?: string | null;
  tags?: string[];
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
  // The library search query — re-runs the FR-3.4 search over the kept archive.
  const [libraryQuery, setLibraryQuery] = useState('');

  // Load (and refresh) the library of kept+validated assets, filtered by the
  // search query (the backend searches title/body/tags via ?q=).
  useEffect(() => {
    let cancelled = false;
    setLibrary({ status: 'loading' });
    fetch(`${apiBaseUrl}/content/library?q=${encodeURIComponent(libraryQuery)}`)
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
  }, [libraryNonce, libraryQuery]);

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
    fetch(`${apiBaseUrl}/content/${proposalId}/decision`, {
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
    <section
      aria-label="Content workspace"
      data-testid="content-workspace"
      style={{ display: 'grid', gap: 'var(--s-4)' }}
    >
      <header
        style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}
      >
        <Sparkles size={16} aria-hidden style={{ color: 'var(--signal)' }} />
        <h2 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, margin: 0 }}>
          Content workspace
        </h2>
      </header>

      {/* The generator prompt + the staged-pipeline chip row. */}
      <Card style={{ display: 'grid', gap: 'var(--s-3)' }}>
        <p className="lab" style={{ margin: 0 }}>
          Tell the generator what you want — generate many, keep the good ones
        </p>
        <textarea
          data-testid="content-prompt"
          aria-label="Content prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe the content to generate…"
          rows={3}
          style={{
            fontFamily: 'var(--sans)',
            fontSize: 'var(--fs-body)',
            width: '100%',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
            padding: 'var(--s-3)',
            background: 'var(--surface-2)',
            color: 'var(--ink)',
            resize: 'vertical',
          }}
        />
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-3)',
            flexWrap: 'wrap',
          }}
        >
          <Button
            variant="primary"
            icon={Sparkles}
            data-testid="content-generate"
            onClick={generate}
            disabled={batch.status === 'loading'}
          >
            Generate batch
          </Button>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-2)',
              flexWrap: 'wrap',
            }}
          >
            <span className="lab">Pipeline</span>
            <Chip tone="flow">Concepts · live</Chip>
            <Chip tone="gate">Images · placeholder</Chip>
            <Chip tone="gate">Video · placeholder</Chip>
          </div>
        </div>
      </Card>

      {batch.status === 'loading' && (
        <p data-testid="batch-loading" className="lab">
          Generating candidates…
        </p>
      )}

      {batch.status === 'error' && (
        <Card style={{ borderColor: 'var(--signal)' }}>
          <p
            data-testid="batch-error"
            role="alert"
            style={{ color: 'var(--signal-ink)', margin: 0 }}
          >
            Could not generate: {batch.message}
          </p>
        </Card>
      )}

      {batch.status === 'ready' && (
        <BatchResult
          data={batch.data}
          decisions={decisions}
          onKeep={(id) => decide(id, 'approve')}
          onDiscard={(id) => decide(id, 'discard')}
        />
      )}

      <ImageBatchPlaceholder />

      <LibraryPanel
        state={library}
        query={libraryQuery}
        onQueryChange={setLibraryQuery}
      />
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
    <div
      className="content-batch"
      data-testid="content-batch"
      style={{ display: 'grid', gap: 'var(--s-3)' }}
    >
      {data.degraded && (
        <Card
          style={{
            borderColor: 'var(--gate)',
            background: 'var(--gate-wash)',
          }}
        >
          <div
            data-testid="batch-degraded"
            role="status"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-2)',
              flexWrap: 'wrap',
              color: 'var(--gate-ink)',
              fontSize: 'var(--fs-sm)',
            }}
          >
            <PlaceholderBadge label="DEGRADED" />
            <span>
              Generation is in <strong>degraded mode</strong> (no-LLM /
              kill-switch / cost cap). These are deterministic fallback
              candidates, not a live AI batch.
            </span>
          </div>
        </Card>
      )}

      <ul
        className="candidate-list"
        style={{
          listStyle: 'none',
          margin: 0,
          padding: 0,
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 'var(--s-3)',
        }}
      >
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
      style={{ listStyle: 'none' }}
    >
      <Card style={{ display: 'grid', gap: 'var(--s-3)', height: '100%' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 'var(--s-2)',
          }}
        >
          <Chip tone="neutral">{candidate.channel}</Chip>
          {candidate.degraded && <PlaceholderBadge label="FALLBACK" />}
        </div>
        <p
          className="candidate-copy"
          data-testid={`candidate-copy-${id}`}
          style={{ fontSize: 'var(--fs-body)', margin: 0 }}
        >
          {candidate.copy}
        </p>
        {decision ? (
          <p
            data-testid={`candidate-decided-${id}`}
            role="status"
            className="mono"
            style={{
              fontSize: 'var(--fs-sm)',
              margin: 0,
              color:
                decision === 'approve' ? 'var(--flow-ink)' : 'var(--muted)',
            }}
          >
            {decision === 'approve' ? '✓ Kept — added to library' : 'Discarded'}
          </p>
        ) : (
          <div
            className="candidate-controls"
            style={{ display: 'flex', gap: 'var(--s-2)' }}
          >
            <Button
              variant="signal"
              icon={Check}
              data-testid={`keep-${id}`}
              onClick={onKeep}
            >
              Keep → library
            </Button>
            <Button icon={Trash2} data-testid={`discard-${id}`} onClick={onDiscard}>
              Discard
            </Button>
          </div>
        )}
      </Card>
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
      style={{ listStyle: 'none' }}
    >
      <Card
        style={{
          borderColor: 'var(--signal)',
          background: 'var(--signal-wash)',
          display: 'grid',
          gap: 'var(--s-2)',
          height: '100%',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
            color: 'var(--signal-ink)',
            fontSize: 'var(--fs-sm)',
            fontWeight: 600,
          }}
        >
          <Ban size={15} aria-hidden style={{ flexShrink: 0 }} />
          Blocked by the content gate — cannot be kept
        </div>
        {candidate.failed_rules.length > 0 && (
          <ul
            className="failed-rules"
            data-testid={`failed-rules-${id}`}
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'flex',
              flexWrap: 'wrap',
              gap: 'var(--s-1)',
            }}
          >
            {candidate.failed_rules.map((rule) => (
              <li key={rule}>
                <Chip tone="signal">{rule}</Chip>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </li>
  );
}

// Image-batch placeholder grid (OUT-1 / INV-9): live media gen is not in v1.
// The PlaceholderBadge marks the surface as simulated; we render no fabricated
// asset, only the dashed tile grid that production would fill.
function ImageBatchPlaceholder(): JSX.Element {
  return (
    <Card style={{ display: 'grid', gap: 'var(--s-3)' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--s-2)',
        }}
      >
        <span className="lab">Image batch · keep what you want</span>
        <PlaceholderBadge />
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(72px, 1fr))',
          gap: 'var(--s-2)',
        }}
      >
        {Array.from({ length: 8 }, (_, i) => (
          <div
            key={i}
            style={{
              aspectRatio: '1',
              borderRadius: 'var(--r-md)',
              background: 'var(--surface-2)',
              border: '1px dashed var(--line-strong)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--muted)',
            }}
          >
            <ImageIcon size={18} aria-hidden />
          </div>
        ))}
      </div>
      <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}>
        A media-gen batch lands here in production; keepers flow to the library
        as brand references.
      </p>
    </Card>
  );
}

function LibraryPanel({
  state,
  query,
  onQueryChange,
}: {
  state: LibraryState;
  query: string;
  onQueryChange: (q: string) => void;
}): JSX.Element {
  // Which asset is expanded to show its full copy (one at a time).
  const [expandedId, setExpandedId] = useState<string | null>(null);
  return (
    <Card
      className="content-library"
      data-testid="content-library"
      style={{ display: 'grid', gap: 'var(--s-3)' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
        <FolderOpen size={15} aria-hidden style={{ color: 'var(--flow)' }} />
        <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: 0 }}>
          Library
        </h3>
        <span className="lab" style={{ marginLeft: 'auto' }}>
          GT's proven, on-brand copy — search and reuse
        </span>
      </div>

      {/* Search the kept archive (FR-3.4 — ?q= over title/body/tags). */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r-md)',
          background: 'var(--surface-2)',
          padding: '6px 10px',
        }}
      >
        <Search size={14} aria-hidden style={{ color: 'var(--muted)', flexShrink: 0 }} />
        <input
          data-testid="library-search"
          aria-label="Search the content library"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search proven copy — e.g. gifted, TEFA, socialization…"
          style={{
            flex: 1,
            border: 'none',
            outline: 'none',
            background: 'transparent',
            color: 'var(--ink)',
            fontFamily: 'var(--sans)',
            fontSize: 'var(--fs-sm)',
          }}
        />
      </div>

      {state.status === 'loading' && (
        <p data-testid="library-loading" className="lab">
          Loading library…
        </p>
      )}
      {state.status === 'error' && (
        <p
          data-testid="library-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', margin: 0 }}
        >
          Could not load library: {state.message}
        </p>
      )}
      {state.status === 'ready' &&
        (state.assets.length === 0 ? (
          <p
            data-testid="library-empty"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}
          >
            {query ? `No assets match “${query}”.` : 'No kept assets yet.'}
          </p>
        ) : (
          <ul
            className="library-list"
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'grid',
              gap: 'var(--s-2)',
            }}
          >
            {state.assets.map((asset) => (
              <LibraryRow
                key={asset.id}
                asset={asset}
                expanded={expandedId === asset.id}
                onToggle={() =>
                  setExpandedId((id) => (id === asset.id ? null : asset.id))
                }
              />
            ))}
          </ul>
        ))}
    </Card>
  );
}

// One library row: a clickable header (title + channel + type) that expands to
// reveal the full copy, its tags, and a link out to the original GT source —
// turning the inventory into a reusable, traceable brand-asset shelf (FR-3.4).
function LibraryRow({
  asset,
  expanded,
  onToggle,
}: {
  asset: LibraryAsset;
  expanded: boolean;
  onToggle: () => void;
}): JSX.Element {
  return (
    <li
      className="library-asset"
      data-testid={`library-asset-${asset.id}`}
      style={{
        borderRadius: 'var(--r-sm)',
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        data-testid={`library-asset-toggle-${asset.id}`}
        aria-expanded={expanded}
        onClick={onToggle}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          padding: '8px 10px',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
          color: 'var(--ink)',
        }}
      >
        {expanded ? (
          <ChevronDown size={14} aria-hidden style={{ flexShrink: 0, color: 'var(--muted)' }} />
        ) : (
          <ChevronRight size={14} aria-hidden style={{ flexShrink: 0, color: 'var(--muted)' }} />
        )}
        <FileText size={14} aria-hidden style={{ color: 'var(--muted)', flexShrink: 0 }} />
        <span
          className="library-asset-title"
          style={{ flex: 1, fontSize: 'var(--fs-sm)', fontWeight: 600 }}
        >
          {asset.title}
        </span>
        {asset.channel ? <Chip tone="flow">{asset.channel}</Chip> : null}
        <Chip tone="neutral">{asset.asset_type}</Chip>
      </button>
      {expanded && (
        <div
          data-testid={`library-asset-detail-${asset.id}`}
          style={{ padding: '0 10px 10px 10px', display: 'grid', gap: 'var(--s-2)' }}
        >
          {asset.body ? (
            <p
              className="library-asset-body"
              style={{
                fontSize: 'var(--fs-sm)',
                color: 'var(--ink-soft)',
                margin: 0,
                whiteSpace: 'pre-wrap',
                maxHeight: 220,
                overflow: 'auto',
                borderLeft: '2px solid var(--line-strong)',
                paddingLeft: 'var(--s-3)',
              }}
            >
              {asset.body}
            </p>
          ) : (
            <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}>
              No stored copy for this asset.
            </p>
          )}
          {asset.tags && asset.tags.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--s-1)' }}>
              {asset.tags.map((tag) => (
                <Chip key={tag} tone="neutral">
                  {tag}
                </Chip>
              ))}
            </div>
          )}
          {asset.source_ref && (
            <a
              data-testid={`library-asset-source-${asset.id}`}
              href={asset.source_ref}
              target="_blank"
              rel="noreferrer"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 'var(--s-1)',
                fontSize: 'var(--fs-sm)',
                color: 'var(--flow-ink)',
              }}
            >
              <ExternalLink size={13} aria-hidden /> View original GT source
            </a>
          )}
        </div>
      )}
    </li>
  );
}
