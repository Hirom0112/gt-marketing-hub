import { useCallback, useEffect, useState } from 'react';
import {
  Check,
  ClipboardCheck,
  Database,
  HelpCircle,
  Lock,
  Radio,
  XCircle,
} from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Chip } from '../ui';
import { EmptyState } from '../dashboard/EmptyState';

// Consolidated Decision Queue workspace (TODO_v2 §B2). A leader/admin-only review
// surface: it reads the leader-gated GET /decisions (an OPEN-only queue) and lets a
// leader approve / reject / need-info each item. Every verdict POSTs to
// /decisions/{id}/action and refreshes the list. The card layout REUSES the
// enrollment review-card (ActionPanel's `.proposal` article: a surface-2 well with a
// source label, a readable payload summary, and the decision button row) so the
// queue reads as the same product — no new card system.
//
// Fail-closed posture: a 403 (an operator who somehow reaches this) renders a
// "Leadership only" empty state, never a crash; any other fetch error renders a quiet
// error and never takes down the dashboard. The deterministic backend owns all state
// writes (INV-2) — this panel only surfaces the queue and records the human decision.

/** One Decision-Queue row (the wire shape of the backend DecisionResponse). */
export interface Decision {
  id: string;
  source: string;
  payload: Record<string, unknown>;
  state: 'open' | 'decided' | 'in_flight';
  /** Present on some feeds; not part of the OPEN-list projection — optional. */
  created_at?: string;
}

type DecisionAction = 'approve' | 'reject' | 'need_info';

type LoadState =
  | { status: 'loading' }
  | { status: 'forbidden' } // 403 · leader/admin only
  | { status: 'error'; message: string }
  | { status: 'ready'; decisions: Decision[] };

interface DecisionQueueWorkspaceProps {
  /** Fired after a successful action so the caller (App) can refresh the nav
   *  open-count badge — the queue owns its own list refresh regardless. */
  onChanged?: () => void;
  /** Whether the current seat may DECIDE (spec Module 11: leader-only). An admin
   *  has full module access and VIEWS the queue, but decision-making is reserved
   *  to leadership, so App passes `session.role === 'leader'`. Defaults to true so
   *  the standalone surface (and the Home preview widget) keep the action row; the
   *  backend gates the decide POST to leader-only as defense-in-depth regardless. */
  canDecide?: boolean;
}

export default function DecisionQueueWorkspace({
  onChanged,
  canDecide = true,
}: DecisionQueueWorkspaceProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  // `silent` refreshes (after an action or an enrichment) keep the current
  // rendered tree mounted — they do NOT blank to the loading state. This both
  // avoids a flicker AND preserves the OpenDataTrigger's inline result state
  // (the loading branch doesn't render the trigger, so a hard reload would
  // unmount and reset it).
  const load = useCallback((silent = false) => {
    if (!silent) setState({ status: 'loading' });
    apiFetch('/decisions')
      .then((res) => {
        // Fail-closed: an operator reaching this surface is 403 — render the
        // "Leadership only" empty state, never a crash.
        if (res.status === 403) {
          setState({ status: 'forbidden' });
          return null;
        }
        if (!res.ok) throw new Error(`decisions request failed: ${res.status}`);
        return res.json() as Promise<Decision[]>;
      })
      .then((data) => {
        if (data === null) return; // forbidden · already handled
        setState({ status: 'ready', decisions: data });
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function act(id: string, action: DecisionAction, comment?: string): void {
    apiFetch(`/decisions/${id}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...(comment ? { comment } : {}) }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`action failed: ${res.status}`);
        return res.json();
      })
      .then(() => {
        load(true); // silent refresh of the queue (keep the tree mounted)
        onChanged?.(); // let App re-pull the nav badge count
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setState({ status: 'error', message });
      });
  }

  if (state.status === 'loading') {
    return (
      <section data-testid="decision-queue" aria-label="Decision queue">
        <QueueHeader count={null} />
        <p
          data-testid="decision-queue-loading"
          className="mono"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
        >
          Loading the decision queue…
        </p>
      </section>
    );
  }

  // Fail-closed: leader/admin only — an operator (403) sees this, not a crash.
  if (state.status === 'forbidden') {
    return (
      <section data-testid="decision-queue" aria-label="Decision queue">
        <div data-testid="decision-queue-forbidden">
          <EmptyState
            icon={<Lock size={18} aria-hidden />}
            title="Leadership only"
            body="The decision queue is available to leadership and admin seats."
          />
        </div>
      </section>
    );
  }

  // Fail-safe: an error renders quietly and never crashes the dashboard.
  if (state.status === 'error') {
    return (
      <section data-testid="decision-queue" aria-label="Decision queue">
        <QueueHeader count={null} />
        <p
          data-testid="decision-queue-error"
          role="alert"
          style={{ marginTop: 'var(--s-3)', fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
        >
          Could not load the decision queue: {state.message}
        </p>
      </section>
    );
  }

  // Only OPEN decisions are actionable (the backend list is OPEN-only; filter
  // defensively so a non-open never renders an approvable card).
  const open = state.decisions.filter((d) => d.state === 'open');

  return (
    <section data-testid="decision-queue" aria-label="Decision queue">
      <QueueHeader count={open.length} />

      <OpenDataTrigger onChanged={() => load(true)} />

      {open.length === 0 ? (
        <div data-testid="decision-queue-empty" style={{ marginTop: 'var(--s-3)' }}>
          <EmptyState
            icon={<ClipboardCheck size={18} aria-hidden />}
            title="Queue clear"
            body="No decisions are waiting on leadership right now."
          />
        </div>
      ) : (
        <div
          data-testid="decision-queue-list"
          style={{ display: 'grid', gap: 'var(--s-3)', marginTop: 'var(--s-3)' }}
        >
          {open.map((decision) => (
            <DecisionCard
              key={decision.id}
              decision={decision}
              onAct={act}
              canDecide={canDecide}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function QueueHeader({ count }: { count: number | null }): JSX.Element {
  return (
    <div
      className="lab"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--s-1)',
        color: 'var(--muted)',
      }}
    >
      <ClipboardCheck size={12} aria-hidden /> Decision queue
      {count !== null && (
        <span data-testid="decision-open-count" style={{ color: 'var(--ink)' }}>
          {' '}
          · {count} open
        </span>
      )}
    </div>
  );
}

// A human-readable one-line value for a payload field. Objects/arrays are
// JSON-stringified so a structured payload still reads at a glance.
function summarizeValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

// ---------------------------------------------------------------------------
// Open-Data enrichment (TODO_v2 §E1). When a Texas-district Open Data query
// boosts a recommendation the backend enqueues an `open_data_enrichment`
// decision whose payload carries the district enrichment, the recommendation
// change, the provenance, and the `data_source` (live OpenData vs seeded). The
// card renders that change + provenance + a SOURCE BADGE so a leader sees both
// the decision AND where the signal came from (live vs simulated, INV-9).

interface DistrictEnrichment {
  district_id: string;
  d_rating: string;
  staar_proficiency: number;
  per_pupil_spend: number;
  enrollment: number;
}

interface EnrichmentPayload {
  district_id: string;
  enrichment: DistrictEnrichment;
  recommendation: {
    base_priority: number;
    new_priority: number;
    delta: number;
  };
  provenance: { reason: string; signals: string[] };
  data_source: 'live' | 'seeded';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

// Defensively narrow a raw decision payload to the enrichment shape. Returns
// null on any shape mismatch so a malformed payload falls back to the generic
// dl rendering rather than crashing the card (fail-safe).
function parseEnrichment(payload: Record<string, unknown>): EnrichmentPayload | null {
  const enr = payload.enrichment;
  const rec = payload.recommendation;
  const prov = payload.provenance;
  const ds = payload.data_source;
  if (!isRecord(enr) || !isRecord(rec) || !isRecord(prov)) return null;
  if (ds !== 'live' && ds !== 'seeded') return null;
  const signals = Array.isArray(prov.signals)
    ? prov.signals.filter((s): s is string => typeof s === 'string')
    : [];
  return {
    district_id: String(payload.district_id ?? enr.district_id ?? '—'),
    enrichment: {
      district_id: String(enr.district_id ?? '—'),
      d_rating: String(enr.d_rating ?? '—'),
      staar_proficiency: Number(enr.staar_proficiency ?? 0),
      per_pupil_spend: Number(enr.per_pupil_spend ?? 0),
      enrollment: Number(enr.enrollment ?? 0),
    },
    recommendation: {
      base_priority: Number(rec.base_priority ?? 0),
      new_priority: Number(rec.new_priority ?? 0),
      delta: Number(rec.delta ?? 0),
    },
    provenance: {
      reason: String(prov.reason ?? '—'),
      signals,
    },
    data_source: ds,
  };
}

// The SOURCE BADGE. `live` → a distinct flow-tone "Live OpenData" chip (the
// query hit the real Texas Open Data portal); `seeded` → the muted INV-9
// gate-tone "Seeded" chip — the SAME simulated-surface treatment the app uses
// elsewhere (PlaceholderBadge / "CRM: Simulated"), telling the leader the
// signal came from the synthetic seed, not a live drain.
function SourceBadge({ dataSource }: { dataSource: 'live' | 'seeded' }): JSX.Element {
  const live = dataSource === 'live';
  return (
    <span data-testid="enrichment-source-badge" data-source={dataSource}>
      <Chip
        tone={live ? 'flow' : 'gate'}
        title={
          live
            ? 'Live OpenData · the query hit the real Texas Open Data portal.'
            : 'Seeded · the signal came from the synthetic seed, not a live drain (INV-9).'
        }
      >
        {live ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Radio size={9} aria-hidden /> Live OpenData
          </span>
        ) : (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <Database size={9} aria-hidden /> Seeded
          </span>
        )}
      </Chip>
    </span>
  );
}

// The enrichment detail body: the district vitals (rating / STAAR / enrollment),
// the recommendation change (base → new, +delta), and the provenance (reason +
// signal chips).
function OpenDataEnrichmentDetail({
  enrichment,
}: {
  enrichment: EnrichmentPayload;
}): JSX.Element {
  const { enrichment: e, recommendation: r, provenance: p } = enrichment;
  const deltaSign = r.delta > 0 ? '+' : '';
  return (
    <div data-testid="enrichment-detail" style={{ fontSize: 'var(--fs-sm)' }}>
      <div
        data-testid="enrichment-district"
        style={{ color: 'var(--ink)', fontWeight: 600 }}
      >
        District {enrichment.district_id}
      </div>

      <dl
        data-testid="enrichment-vitals"
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr',
          gap: '2px var(--s-3)',
          margin: 'var(--s-2) 0 0',
        }}
      >
        <dt className="mono" style={{ color: 'var(--muted)' }}>
          rating
        </dt>
        <dd data-testid="enrichment-rating" style={{ margin: 0, color: 'var(--ink)' }}>
          {e.d_rating}
        </dd>
        <dt className="mono" style={{ color: 'var(--muted)' }}>
          STAAR
        </dt>
        <dd data-testid="enrichment-staar" style={{ margin: 0, color: 'var(--ink)' }}>
          {(e.staar_proficiency * 100).toFixed(0)}%
        </dd>
        <dt className="mono" style={{ color: 'var(--muted)' }}>
          enrollment
        </dt>
        <dd
          data-testid="enrichment-enrollment"
          style={{ margin: 0, color: 'var(--ink)' }}
        >
          {e.enrollment.toLocaleString()}
        </dd>
      </dl>

      <div
        data-testid="enrichment-recommendation"
        style={{
          marginTop: 'var(--s-3)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
        }}
      >
        <span className="mono" style={{ color: 'var(--muted)' }}>
          priority
        </span>
        <span style={{ color: 'var(--ink)' }}>
          {r.base_priority} → {r.new_priority}
        </span>
        <Chip tone="flow" title="Priority boost from the Open Data signal.">
          {deltaSign}
          {r.delta}
        </Chip>
      </div>

      <div data-testid="enrichment-provenance" style={{ marginTop: 'var(--s-3)' }}>
        <div style={{ color: 'var(--ink)' }}>{p.reason}</div>
        {p.signals.length > 0 && (
          <div
            data-testid="enrichment-signals"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 'var(--s-1)',
              marginTop: 'var(--s-2)',
            }}
          >
            {p.signals.map((signal) => (
              <Chip key={signal} tone="neutral" title={`Open Data signal: ${signal}`}>
                {signal}
              </Chip>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The trigger: a small leader/admin control that runs a Texas-district Open Data
// query (POST /open-data/enrich). On a `recommendation_changed:true` response it
// refreshes the queue (the new card appears) and surfaces the change + source
// inline; on `recommendation_changed:false` it shows a quiet "no change — the
// district is well-rated" note (honest — not every query changes a decision).
// Fail-safe: a fetch error renders a quiet error and never crashes the queue.

interface EnrichResult {
  district_id: string;
  recommendation_changed: boolean;
  new_priority: number;
  data_source: 'live' | 'seeded';
}

type TriggerState =
  | { status: 'idle' }
  | { status: 'running' }
  | { status: 'changed'; result: EnrichResult }
  | { status: 'unchanged'; result: EnrichResult }
  | { status: 'error'; message: string };

function OpenDataTrigger({ onChanged }: { onChanged: () => void }): JSX.Element {
  const [districtId, setDistrictId] = useState('');
  const [trigger, setTrigger] = useState<TriggerState>({ status: 'idle' });

  function run(): void {
    const id = districtId.trim();
    if (id === '') return;
    setTrigger({ status: 'running' });
    apiFetch('/open-data/enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ district_id: id }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`enrich failed: ${res.status}`);
        return res.json() as Promise<{
          district_id: string;
          recommendation_changed: boolean;
          new_priority: number;
          data_source: 'live' | 'seeded';
        }>;
      })
      .then((data) => {
        const result: EnrichResult = {
          district_id: data.district_id,
          recommendation_changed: data.recommendation_changed,
          new_priority: data.new_priority,
          data_source: data.data_source,
        };
        if (data.recommendation_changed) {
          setTrigger({ status: 'changed', result });
          onChanged(); // refresh the queue · the new card appears
        } else {
          setTrigger({ status: 'unchanged', result });
        }
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setTrigger({ status: 'error', message });
      });
  }

  return (
    <div
      data-testid="open-data-trigger"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3)',
        borderRadius: 'var(--r-md)',
        border: '1px solid var(--line)',
        background: 'var(--surface-2)',
      }}
    >
      <div className="lab" style={{ color: 'var(--muted)', marginBottom: 'var(--s-2)' }}>
        <Database size={12} aria-hidden /> Run Open Data enrichment
      </div>
      <div style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          data-testid="open-data-district-input"
          aria-label="District id"
          value={districtId}
          onChange={(e) => setDistrictId(e.target.value)}
          placeholder="Texas district id"
          style={{
            flex: '1 1 200px',
            fontFamily: 'var(--sans)',
            fontSize: 'var(--fs-body)',
            color: 'var(--ink)',
            background: 'var(--surface)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-sm)',
            padding: 'var(--s-2)',
          }}
        />
        <Button
          variant="signal"
          icon={Database}
          data-testid="open-data-run"
          disabled={trigger.status === 'running' || districtId.trim() === ''}
          onClick={run}
        >
          {trigger.status === 'running' ? 'Querying…' : 'Run query'}
        </Button>
      </div>

      {trigger.status === 'changed' && (
        <p
          data-testid="open-data-changed"
          style={{ margin: 'var(--s-2) 0 0', fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
        >
          Recommendation changed for {trigger.result.district_id}: new priority{' '}
          {trigger.result.new_priority}.{' '}
          <SourceBadge dataSource={trigger.result.data_source} /> A new decision card was
          added to the queue.
        </p>
      )}

      {trigger.status === 'unchanged' && (
        <p
          data-testid="open-data-no-change"
          style={{ margin: 'var(--s-2) 0 0', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}
        >
          No change · district {trigger.result.district_id} is well-rated.{' '}
          <SourceBadge dataSource={trigger.result.data_source} />
        </p>
      )}

      {trigger.status === 'error' && (
        <p
          data-testid="open-data-error"
          role="alert"
          style={{ margin: 'var(--s-2) 0 0', fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
        >
          Could not run the Open Data query: {trigger.message}
        </p>
      )}
    </div>
  );
}

interface DecisionCardProps {
  decision: Decision;
  onAct: (id: string, action: DecisionAction, comment?: string) => void;
  /** Only a leader seat may decide (spec Module 11). An admin views read-only. */
  canDecide: boolean;
}

// One review card — the SAME `.proposal` review-card shell ActionPanel uses (a
// surface-2 well + decision button row), repurposed for a queued decision.
function DecisionCard({
  decision,
  onAct,
  canDecide,
}: DecisionCardProps): JSX.Element {
  const [needInfo, setNeedInfo] = useState(false);
  const [comment, setComment] = useState('');
  const [required, setRequired] = useState(false);

  const entries = Object.entries(decision.payload);

  function submitNeedInfo(): void {
    // Need-info REQUIRES a comment — block client-side and surface the required
    // state rather than POSTing an empty comment (the backend would 422 anyway).
    if (comment.trim() === '') {
      setRequired(true);
      return;
    }
    onAct(decision.id, 'need_info', comment.trim());
  }

  return (
    <article
      data-testid="decision-card"
      data-decision={decision.id}
      className="proposal"
      style={{
        padding: 'var(--s-3)',
        borderRadius: 'var(--r-md)',
        border: '1px solid var(--line)',
        background: 'var(--surface-2)',
      }}
    >
      <div
        className="lab"
        data-testid="decision-source"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          color: 'var(--muted)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <span>{decision.source}</span>
        {decision.source === 'open_data_enrichment' &&
          parseEnrichment(decision.payload) !== null && (
            <SourceBadge
              dataSource={parseEnrichment(decision.payload)!.data_source}
            />
          )}
      </div>

      {decision.source === 'open_data_enrichment' &&
      parseEnrichment(decision.payload) !== null ? (
        <OpenDataEnrichmentDetail enrichment={parseEnrichment(decision.payload)!} />
      ) : entries.length > 0 ? (
        <dl
          data-testid="decision-payload"
          style={{
            display: 'grid',
            gridTemplateColumns: 'auto 1fr',
            gap: '2px var(--s-3)',
            margin: 0,
            fontSize: 'var(--fs-sm)',
          }}
        >
          {entries.map(([key, value]) => (
            <div key={key} style={{ display: 'contents' }}>
              <dt className="mono" style={{ color: 'var(--muted)' }}>
                {key}
              </dt>
              <dd style={{ margin: 0, color: 'var(--ink)' }}>
                {summarizeValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p
          data-testid="decision-payload"
          style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}
        >
          No additional detail.
        </p>
      )}

      {canDecide && needInfo && (
        <div style={{ marginTop: 'var(--s-3)' }}>
          <textarea
            data-testid="need-info-comment"
            aria-label="Need-info comment"
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              if (required) setRequired(false);
            }}
            rows={2}
            placeholder="What information is needed?"
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
          {required && (
            <p
              data-testid="need-info-required"
              role="alert"
              style={{
                margin: 'var(--s-1) 0 0',
                fontSize: 'var(--fs-sm)',
                color: 'var(--signal-ink)',
              }}
            >
              A comment is required to request more info.
            </p>
          )}
        </div>
      )}

      {!canDecide ? (
        <p
          data-testid="decision-readonly"
          className="lab"
          style={{
            marginTop: 'var(--s-3)',
            color: 'var(--muted)',
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
          }}
        >
          <Lock size={13} aria-hidden />
          Leadership decides. Your admin seat sees the queue read-only.
        </p>
      ) : (
      <div
        className="proposal-decisions"
        style={{ display: 'flex', gap: 'var(--s-2)', marginTop: 'var(--s-3)', flexWrap: 'wrap' }}
      >
        <Button
          variant="signal"
          icon={Check}
          data-testid="decision-approve"
          onClick={() => onAct(decision.id, 'approve')}
        >
          Approve
        </Button>
        <Button
          icon={XCircle}
          data-testid="decision-reject"
          onClick={() => onAct(decision.id, 'reject')}
        >
          Reject
        </Button>
        {needInfo ? (
          <Button
            icon={HelpCircle}
            data-testid="need-info-submit"
            onClick={submitNeedInfo}
          >
            Send need-info
          </Button>
        ) : (
          <Button
            icon={HelpCircle}
            data-testid="decision-need-info"
            onClick={() => setNeedInfo(true)}
          >
            Need info
          </Button>
        )}
      </div>
      )}
    </article>
  );
}

// ---------------------------------------------------------------------------
// Nav badge feed — a tiny standalone hook the shell (App) uses to drive the
// leadership nav's open-count badge. It reads the SAME leader-gated GET /decisions
// and counts OPEN rows; disabled (no fetch) for any non-leader seat. Fail-safe: a
// 403 or any error yields 0 so the badge never blocks the shell.
export function useOpenDecisionCount(
  enabled: boolean,
): { count: number; refresh: () => void } {
  const [count, setCount] = useState(0);

  const refresh = useCallback(() => {
    if (!enabled) {
      setCount(0);
      return;
    }
    apiFetch('/decisions')
      .then((res) => (res.ok ? (res.json() as Promise<Decision[]>) : null))
      .then((data) => {
        if (data === null) return; // 403 / non-OK · leave the count as-is
        setCount(data.filter((d) => d.state === 'open').length);
      })
      .catch(() => {
        /* fail-safe: keep the last-known count; never crash the shell */
      });
  }, [enabled]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { count, refresh };
}
