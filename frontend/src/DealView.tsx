import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Mail,
  Phone,
  UploadCloud,
  XCircle,
} from 'lucide-react';
import { hubspotContactUrl, hubspotDealUrl, apiFetch } from './config';
import { Button, Chip } from './ui';
import RecencyChip from './enrollment/RecencyChip';
import CompletionRing from './enrollment/CompletionRing';
import SeamDot, { type SeamStatus } from './enrollment/SeamDot';
import { fmtDay, fundingLabel, humanizeSegment } from './enrollment/format';

// Deal view (FR-2.2). Fetches GET /families/{id} and surfaces the deal_view
// summary: stall reason, funding type, conversion likelihood (DH-1 — REPLACES the
// old MAP signal: band + score + top contributing factor), attribution source, and
// CRM seam status. Native fetch only (≤12-dep budget). Read-only (INV-2). Interest-
// stage families have no app_form, so stall_reason / conversion fields can be null
// — those render as an em-dash placeholder, never literal "null".
// S8 Wave 2 re-skin: matches the reference deal panel — a name header with a
// funding chip, a "Why they haven't converted" stall callout in a signal wash,
// and a two-column field grid built from the Field primitive.

// The deal_view object nested in the FastAPI /families/{id} response.
interface DealViewData {
  display_name: string;
  stall_reason: string | null;
  funding_type: string;
  // DH-1 conversion-likelihood signal — REPLACES the old `map_score` MAP signal:
  // who is most likely to enroll (a [0,1] score + a coarse band) and the top
  // contributing factor, surfaced "to use it to close". Composed in the API layer.
  conversion_score?: number | null;
  conversion_band?: string | null;
  conversion_top_factor_label?: string | null;
  attribution_source: string;
  crm_seam_status: string;
  // S9 Wave 4 drop-off + recency projection (api-composed; may be null for an
  // interest-stage family with no app_form / no recency yet).
  completion_pct?: number | null;
  forms_signed?: number | null;
  forms_total?: number | null;
  next_unsigned_form?: string | null;
  contact_status?: string | null;
  // S12 W1 — the derived recovery state (A-19), composed in the API layer.
  recovery_state?: 'stalled' | 'working' | 'recovered' | 'dismissed' | null;
}

// The household's primary CONTACT — the person a rep actually calls (synthetic,
// INV-1). Sourced from the lead row already in the /families/{id} response; the
// household display_name ("The Rivera Family") is not callable on its own.
interface LeadContact {
  synthetic_first_name?: string | null;
  synthetic_last_name?: string | null;
  synthetic_email?: string | null;
  synthetic_phone?: string | null;
  region?: string | null;
  grade_interest?: string | null;
  num_children?: number | null;
}

// We read deal_view + the lead contact; the rest of the family response is ignored.
interface FamilyResponse {
  deal_view: DealViewData;
  lead?: LeadContact | null;
}

// DH-5 — the per-child grain for the selected family. Each child runs its own
// funnel (one application per child), so the deal panel must show WHICH children
// the family has and WHERE EACH left off (current_stage). Sourced from the same
// GET /students board StudentBoard reads (A-24) — no new shape invented. We read
// only the identity + stage fields here; the board's score/recoverability terms
// are ignored (this is the close panel, not the triage board). Read-only (INV-2).
interface DealStudentRow {
  student_id: string;
  family_id: string;
  display_label: string;
  synthetic_first_name: string;
  grade: string;
  current_stage: string;
}

interface DealHouseholdGroup {
  family_id: string;
  students: DealStudentRow[];
}

interface StudentBoardResponse {
  households: DealHouseholdGroup[];
}

// LA-23 — one append-only ownership-history fact from GET /families/{id}/assignments
// (the `lead_assignment` rows). A reassignment NEVER overwrites: each assign /
// reassign / unassign is its own from→to/reason row, so the timeline is the durable
// audit record of who owned this lead, when, and why (NFR-6). `from_rep_id` null ⇒
// out of the intake pool; `to_rep_id` null ⇒ back to intake. Read-only (INV-2).
interface AssignmentEvent {
  assignment_id: string;
  from_rep_id: string | null;
  to_rep_id: string | null;
  routed_role: string | null;
  assigned_by: string;
  reason: string;
  occurred_at: string | null;
}

// A response counts as the assignment history only if it is an array of rows
// carrying the discriminating `assignment_id` + `reason` — so the blanket family
// payload (served for every url by a test stub) does NOT masquerade as history.
// Fail safe on any other shape: render no timeline rather than crash.
function isAssignmentList(value: unknown): value is AssignmentEvent[] {
  return (
    Array.isArray(value) &&
    value.every(
      (r) =>
        typeof r === 'object' &&
        r !== null &&
        typeof (r as Record<string, unknown>).assignment_id === 'string' &&
        typeof (r as Record<string, unknown>).reason === 'string',
    )
  );
}

// The GET /enrollment/agents roster, read ONLY to resolve an agent_id → display
// name for the timeline. A missing / unknown shape degrades gracefully (the row
// falls back to a short id) — names are a nicety, never a dependency.
interface AgentRosterRow {
  agent_id: string;
  name?: string | null;
  synthetic_name?: string | null;
}

function isAgentRoster(value: unknown): value is { agents: AgentRosterRow[] } {
  if (typeof value !== 'object' || value === null) return false;
  return Array.isArray((value as Record<string, unknown>).agents);
}

// A response only counts as a student-board payload if it carries the
// discriminating `households` array — so the blanket fetch stubs that serve the
// /families payload for EVERY url (and the /crm/status, /seed payloads) do NOT
// masquerade as the board and render bogus children. Fail SAFE on an unknown
// shape: render no per-child section rather than crash (mirrors `isCrmStatus`).
function isStudentBoard(value: unknown): value is StudentBoardResponse {
  if (typeof value !== 'object' || value === null) return false;
  return Array.isArray((value as Record<string, unknown>).households);
}

// POST /enrollment/families/{id}/seed response (S10 W3). The live HubSpot Deal +
// Contact ids are the proof-of-capture the capture panel deep-links; seam_status
// flips to `synced` once the push lands.
interface SeedResponse {
  family_id: string;
  simulated: boolean;
  deal_id: string;
  contact_id: string | null;
  stage: string;
  seam_status: string;
}

type SeedState =
  | { status: 'idle' }
  | { status: 'seeding' }
  | { status: 'error'; message: string }
  | { status: 'captured'; data: SeedResponse };

// GET /crm/status (S14 W4) — the read-only CRM seam state the operator UI reads
// to fail closed on the live-push action (INV-3 pattern; INV-8 kill switch). NO
// secret: `token_configured` is a bool, the token itself is never surfaced.
interface CrmStatus {
  crm_mode: 'simulate' | 'live';
  kill_switch: boolean;
  // What the registry would ACTUALLY select — `simulate` when the kill switch is
  // on even though crm_mode=live, so the indicator reflects real behavior.
  effective_mode: 'simulate' | 'live';
  token_configured: boolean;
  calls_per_run_cap: number;
}

// A response shape only counts as a CrmStatus if it carries the discriminating
// fields — so a stray GET that resolves to some OTHER payload (e.g. a test fetch
// stub that serves the family object for every URL) does NOT masquerade as CRM
// status and silently disable the action. Fail OPEN on an unknown shape: absent /
// malformed status ⇒ no kill-switch banner, the action stays enabled (the kill
// switch only fail-closes on a POSITIVE kill_switch=true from the real endpoint).
function isCrmStatus(value: unknown): value is CrmStatus {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.kill_switch === 'boolean' &&
    typeof v.effective_mode === 'string' &&
    typeof v.crm_mode === 'string'
  );
}

interface DealViewProps {
  familyId: string;
  // Bump to force a re-fetch (e.g. after an approved follow-up updates recency).
  refreshKey?: number;
  // The audited dismiss reasons (S12 W4; A-19) for the "Dismiss this family"
  // picker. The dismiss WRITE is owned by the parent (one route) — DealView only
  // offers the reason and calls back; it never writes (INV-2).
  dismissReasons?: readonly string[];
  onDismiss?: (familyId: string, reason: string) => void;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: DealViewData; contact: LeadContact | null };

const PLACEHOLDER = '—';

function display(value: string | null): string {
  return value ?? PLACEHOLDER;
}

// A labelled read-only value whose VALUE element carries the testid the
// acceptance test reads. (The Field primitive doesn't forward a testid, so this
// thin local field mirrors its look while keeping the assertion target.)
function DealField({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId: string;
}): JSX.Element {
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
      }}
    >
      <div className="lab">{label}</div>
      <div
        className="mono"
        data-testid={testId}
        style={{ fontSize: 'var(--fs-sm)', marginTop: 2, color: 'var(--ink)' }}
      >
        {value}
      </div>
    </div>
  );
}

// DH-1 conversion-likelihood tile — REPLACES the old "MAP signal" tile in the
// deal-view field grid. Shows the coarse band (High/Med/Low) with a tone-coded
// dot, the [0,1] score as a percentage, and the single top contributing factor
// (e.g. "Funding lined up") so the operator sees who is most likely to enroll and
// WHY — the close signal. Mirrors the DealField look. Read-only (INV-2).
function ConversionField({
  band,
  score,
  topFactorLabel,
}: {
  band: string | null;
  score: number | null;
  topFactorLabel: string | null;
}): JSX.Element {
  // Band → tone color (reuses the signal palette). Unknown band ⇒ neutral ink.
  const tone =
    band === 'High'
      ? 'var(--ok, #1a7f37)'
      : band === 'Med'
        ? 'var(--warn, #9a6700)'
        : band === 'Low'
          ? 'var(--signal-ink, #6b7280)'
          : 'var(--ink)';
  const pct = score === null ? null : Math.round(score * 100);
  const headline =
    band === null ? PLACEHOLDER : pct === null ? band : `${band} · ${pct}%`;
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
      }}
    >
      <div className="lab">Conversion likelihood</div>
      <div
        className="mono"
        data-testid="deal-conversion"
        style={{
          fontSize: 'var(--fs-sm)',
          marginTop: 2,
          color: tone,
          fontWeight: 600,
        }}
      >
        {headline}
      </div>
      {topFactorLabel != null && (
        <div
          data-testid="deal-conversion-factor"
          style={{
            fontSize: 'var(--fs-xs, 11px)',
            marginTop: 2,
            color: 'var(--signal-ink, #6b7280)',
          }}
        >
          Top factor: {topFactorLabel}
        </div>
      )}
    </div>
  );
}

// The CRM-seam status as a CLEAN NAMED CHIP with a SeamDot (S12 W4) — never a raw
// UUID (A-17). The seam is the forward step's state: synced (in HubSpot / flow),
// conflict (needs a human / signal), unsynced or anything else (not yet pushed /
// neutral). The `deal-seam-status` testid carries the named status the suite reads.
function SeamField({ status }: { status: string }): JSX.Element {
  const normalized = status.toLowerCase();
  const tone: 'flow' | 'signal' | 'neutral' =
    normalized === 'synced'
      ? 'flow'
      : normalized === 'conflict'
        ? 'signal'
        : 'neutral';
  const dotStatus: SeamStatus =
    normalized === 'synced'
      ? 'synced'
      : normalized === 'conflict'
        ? 'conflict'
        : 'unsynced';
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        padding: '6px 9px',
        background: 'var(--surface-2)',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <div className="lab">HubSpot seam</div>
      <span
        data-testid="deal-seam-status"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)' }}
      >
        <SeamDot status={dotStatus} />
        <Chip tone={tone}>{status}</Chip>
      </span>
    </div>
  );
}

// The household's primary CONTACT bar — who to call, with click-to-dial / email.
// The household label ("The Rivera Family") is not actionable on a call; this
// surfaces the synthetic contact person + phone + email already in the lead row
// (INV-1 — all synthetic). Renders nothing if no lead/contact resolved (fail safe).
function ContactBar({ contact }: { contact: LeadContact | null }): JSX.Element | null {
  if (contact === null) return null;
  const name = [contact.synthetic_first_name, contact.synthetic_last_name]
    .filter(Boolean)
    .join(' ')
    .trim();
  const phone = contact.synthetic_phone ?? null;
  const email = contact.synthetic_email ?? null;
  // Nothing to show ⇒ omit (never an empty bar).
  if (!name && !phone && !email) return null;
  const meta = [
    contact.num_children != null
      ? `${contact.num_children} ${contact.num_children === 1 ? 'child' : 'children'}`
      : null,
    contact.grade_interest ? `Grade ${contact.grade_interest}` : null,
    contact.region ?? null,
  ].filter(Boolean);
  return (
    <div
      data-testid="deal-contact"
      style={{
        marginTop: 'var(--s-2)',
        padding: 'var(--s-2) var(--s-3)',
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-md)',
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 'var(--s-3)',
      }}
    >
      <span
        data-testid="deal-contact-name"
        style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
      >
        {name || PLACEHOLDER}
      </span>
      {phone != null && (
        <a
          href={`tel:${phone}`}
          data-testid="deal-contact-phone"
          className="mono"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--flow-ink, #0b6)',
          }}
        >
          <Phone size={12} aria-hidden /> {phone}
        </a>
      )}
      {email != null && (
        <a
          href={`mailto:${email}`}
          data-testid="deal-contact-email"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--flow-ink, #0b6)',
          }}
        >
          <Mail size={12} aria-hidden /> {email}
        </a>
      )}
      {meta.length > 0 && (
        <span className="lab" data-testid="deal-contact-meta">
          {meta.join(' · ')}
        </span>
      )}
    </div>
  );
}

// The recovery-state tag in the panel header (S12 W4; A-19). recovered/working
// read teal (forward progress), dismissed neutral, stalled signal.
function RecoveryTag({ state }: { state: string }): JSX.Element {
  const tone: 'flow' | 'signal' | 'neutral' =
    state === 'recovered' || state === 'working'
      ? 'flow'
      : state === 'stalled'
        ? 'signal'
        : 'neutral';
  const label =
    state === 'working'
      ? 'Working'
      : state === 'recovered'
        ? 'Recovered'
        : state === 'dismissed'
          ? 'Dismissed'
          : 'Stalled';
  return (
    <span data-testid="deal-recovery-state">
      <Chip tone={tone}>{label}</Chip>
    </span>
  );
}

// Per-child progress (DH-5). Lists EACH child of the selected family with its
// grade and the funnel stage it LEFT OFF at (current_stage), humanized with the
// SAME format.ts helper used across the enrollment views ("enroll" → "Enroll").
// The child label reuses the board's `synthetic_first_name` (synthetic only —
// INV-1, no real PII) + grade, the same identity StudentBoard shows. Read-only
// (INV-2). States:
//   - null     ⇒ still loading the board (or it failed / an unknown shape) — the
//                section is omitted so a missing board never breaks the panel.
//   - []       ⇒ the board resolved but this family has no children — an explicit
//                "No children on file" placeholder, never literal "null".
//   - [child…] ⇒ one row per child with its grade + stage chip.
function ChildrenSection({
  children,
}: {
  children: DealStudentRow[] | null;
}): JSX.Element | null {
  if (children === null) return null;
  return (
    <div
      data-testid="deal-children"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-md)',
      }}
    >
      <div className="lab">Per-child progress</div>
      {children.length === 0 ? (
        <div
          data-testid="deal-children-empty"
          style={{
            marginTop: 'var(--s-2)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--muted)',
          }}
        >
          No children on file
        </div>
      ) : (
        <ul
          style={{
            listStyle: 'none',
            margin: 'var(--s-2) 0 0',
            padding: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--s-1)',
          }}
        >
          {children.map((child) => {
            const grade = child.grade ? `Grade ${child.grade}` : PLACEHOLDER;
            const name = child.synthetic_first_name || null;
            const stageLabel = humanizeSegment(child.current_stage) || PLACEHOLDER;
            return (
              <li
                key={child.student_id}
                data-testid={`deal-child-${child.student_id}`}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 'var(--s-2)',
                }}
              >
                <span
                  className="row-name"
                  style={{ fontSize: 'var(--fs-sm)', minWidth: 0 }}
                >
                  {name != null ? `${name} · ${grade}` : grade}
                </span>
                <span
                  className="row-stage"
                  data-testid="deal-child-stage"
                  title="Where this child left off"
                >
                  <Chip>{stageLabel}</Chip>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// LA-23 — the assignment-history timeline. Renders the append-only ownership
// facts newest-LAST (chronological), each as "<from> → <to> · <date>" with the
// human-readable routing reason underneath (every assignment is explainable, §2).
// `from` null ⇒ "Intake" (out of the unassigned pool); `to` null ⇒ "Intake" (back
// to the pool). Names resolve through the roster map; an unresolved id falls back
// to a short id. Omitted entirely when there is no history (fail safe). Read-only.
function AssignmentTimeline({
  events,
  names,
}: {
  events: AssignmentEvent[] | null;
  names: Record<string, string>;
}): JSX.Element | null {
  if (events === null || events.length === 0) return null;
  const label = (repId: string | null): string => {
    if (repId === null) return 'Intake';
    return names[repId] ?? `${repId.slice(0, 8)}…`;
  };
  return (
    <div
      data-testid="deal-assignment-history"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-md)',
      }}
    >
      <div className="lab">Assignment history</div>
      <ul
        style={{
          listStyle: 'none',
          margin: 'var(--s-2) 0 0',
          padding: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--s-2)',
        }}
      >
        {events.map((ev) => (
          <li
            key={ev.assignment_id}
            data-testid={`deal-assignment-${ev.assignment_id}`}
            style={{
              borderLeft: '2px solid var(--line)',
              paddingLeft: 'var(--s-3)',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 'var(--s-2)',
              }}
            >
              <span
                className="mono"
                style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
              >
                {label(ev.from_rep_id)} → {label(ev.to_rep_id)}
                {ev.routed_role != null ? ` · ${ev.routed_role}` : ''}
              </span>
              {ev.occurred_at != null && (
                <span className="lab" style={{ whiteSpace: 'nowrap' }}>
                  {fmtDay(ev.occurred_at)}
                </span>
              )}
            </div>
            <div
              style={{
                marginTop: 2,
                fontSize: 'var(--fs-xs, 11px)',
                color: 'var(--signal-ink, #6b7280)',
              }}
            >
              {ev.reason}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function DealView({
  familyId,
  refreshKey,
  dismissReasons,
  onDismiss,
}: DealViewProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [seed, setSeed] = useState<SeedState>({ status: 'idle' });
  // The "Dismiss this family" reason picker (closed by default).
  const [dismissing, setDismissing] = useState(false);
  // The CRM seam state (S14 W4) — null until /crm/status resolves (or if it is
  // unavailable / an unknown shape, in which case we fail OPEN: no banner, the
  // live-push stays enabled; the kill switch only blocks on a positive true).
  const [crm, setCrm] = useState<CrmStatus | null>(null);
  // DH-5 — this family's children + where each left off. null until /students
  // resolves; an unknown shape / error leaves it null ⇒ no per-child section
  // (fail safe, never a crash or bogus rows).
  const [children, setChildren] = useState<DealStudentRow[] | null>(null);
  // LA-23 — this family's append-only ownership history + an agent_id→name map for
  // the timeline. null until /families/{id}/assignments resolves; any error /
  // unknown shape leaves it null ⇒ no timeline (fail safe). The name map is a
  // nicety (an unresolved id falls back to a short id), so it loads independently.
  const [assignments, setAssignments] = useState<AssignmentEvent[] | null>(null);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});

  function seedToHubSpot(): void {
    setSeed({ status: 'seeding' });
    apiFetch(`/enrollment/families/${familyId}/seed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`seed request failed: ${res.status}`);
        return res.json() as Promise<SeedResponse>;
      })
      .then((data) => setSeed({ status: 'captured', data }))
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        setSeed({ status: 'error', message });
      });
  }

  useEffect(() => {
    // A new family resets the capture state (no stale ids across selections).
    setSeed({ status: 'idle' });
    setDismissing(false);
  }, [familyId]);

  useEffect(() => {
    // Surface the CRM seam state so the live-push action fails closed when the
    // kill switch is on (S14 W4; INV-3/INV-8). Fail OPEN on any error / unknown
    // shape — a missing status never silently disables the action.
    let cancelled = false;
    apiFetch(`/crm/status`)
      .then((res) => (res.ok ? (res.json() as Promise<unknown>) : null))
      .then((data) => {
        if (!cancelled) setCrm(isCrmStatus(data) ? data : null);
      })
      .catch(() => {
        if (!cancelled) setCrm(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/families/${familyId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`family request failed: ${res.status}`);
        return res.json() as Promise<FamilyResponse>;
      })
      .then((data) => {
        if (!cancelled)
          setState({
            status: 'ready',
            data: data.deal_view,
            contact: data.lead ?? null,
          });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [familyId, refreshKey]);

  useEffect(() => {
    // DH-5 — fetch the per-child board (the SAME source StudentBoard reads, A-24)
    // and keep only THIS family's children, so the deal panel can show which
    // children the family has + where EACH left off. scope=all so a recovered /
    // dismissed sibling still appears (the deal panel is the close view, not the
    // active-only triage board). X-Demo-* headers ride along via apiFetch. Fail
    // safe: any error / unknown shape ⇒ no children (never a crash). Read-only.
    let cancelled = false;
    setChildren(null);
    apiFetch(`/students?scope=all`)
      .then((res) => (res.ok ? (res.json() as Promise<unknown>) : null))
      .then((data) => {
        if (cancelled) return;
        if (!isStudentBoard(data)) {
          setChildren(null);
          return;
        }
        const mine = data.households
          .filter((h) => h.family_id === familyId)
          .flatMap((h) => h.students);
        setChildren(mine);
      })
      .catch(() => {
        if (!cancelled) setChildren(null);
      });
    return () => {
      cancelled = true;
    };
  }, [familyId, refreshKey]);

  useEffect(() => {
    // LA-23 — fetch this family's append-only ownership history (owner-scoped
    // server-side, INV-5). Fail safe: any error / unknown shape ⇒ no timeline
    // (never a crash). Read-only (INV-2).
    let cancelled = false;
    setAssignments(null);
    apiFetch(`/families/${familyId}/assignments`)
      .then((res) => (res.ok ? (res.json() as Promise<unknown>) : null))
      .then((data) => {
        if (!cancelled) setAssignments(isAssignmentList(data) ? data : null);
      })
      .catch(() => {
        if (!cancelled) setAssignments(null);
      });
    return () => {
      cancelled = true;
    };
  }, [familyId, refreshKey]);

  useEffect(() => {
    // The agent_id→name map for the timeline (GET /enrollment/agents). Loaded once;
    // a missing / unknown shape just leaves the map empty (the timeline falls back
    // to a short id — names are a nicety, never a dependency). Read-only.
    let cancelled = false;
    apiFetch(`/enrollment/agents`)
      .then((res) => (res.ok ? (res.json() as Promise<unknown>) : null))
      .then((data) => {
        if (cancelled || !isAgentRoster(data)) return;
        const map: Record<string, string> = {};
        for (const a of data.agents) {
          const name = a.name ?? a.synthetic_name;
          if (name) map[a.agent_id] = name;
        }
        setAgentNames(map);
      })
      .catch(() => {
        /* names are optional — leave the map empty */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === 'loading') {
    return (
      <p data-testid="deal-view-loading" className="lab">
        Loading deal…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="deal-view-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load deal: {state.message}
      </p>
    );
  }

  const deal = state.data;
  // Fail closed (INV-8): the kill switch disables the live-push action. Only a
  // POSITIVE kill_switch=true from /crm/status blocks — an absent / unknown status
  // leaves the action enabled (fail open on missing state, never silently off).
  const killSwitchOn = crm?.kill_switch === true;
  // A-23 — show the operator-facing label ("Texas voucher" / "Self-pay"), never
  // the raw enum. Voucher tiers (any TEFA) take the gate tone, self-pay the flow.
  const isTefa = deal.funding_type.toLowerCase().includes('tefa');
  const fundingDisplay = fundingLabel(deal.funding_type);

  // "Where they left off" — show the stage they're ACTUALLY stuck in, not the
  // always-100% application %. The APPLICATION (Interest form) and the 6-form
  // ENROLLMENT packet are two distinct stages: once the application is submitted
  // (completion ≥ 100) the family is in the packet, so the ring + line track FORM
  // progress (e.g. "2 of 6") and "stuck on" names the next unsigned form.
  // Otherwise they're still in the application: the ring + line track the app %.
  const completion = deal.completion_pct;
  const appSubmitted = completion != null && completion >= 100;
  const inEnrollment = appSubmitted && deal.forms_total != null;
  const enrollPct =
    inEnrollment && deal.forms_total
      ? Math.round(((deal.forms_signed ?? 0) / deal.forms_total) * 100)
      : 0;
  const dropoffRingPct = inEnrollment ? enrollPct : (completion ?? 0);
  const showDropoffRing = completion != null || inEnrollment;
  // The "stuck on <form>" signal only makes sense once they're IN the packet —
  // a pre-submit family is stuck in the application, not on form #1.
  const stuckForm =
    inEnrollment && deal.next_unsigned_form != null
      ? deal.next_unsigned_form.replace(/_/g, ' ')
      : null;

  return (
    <section aria-label="Deal view" data-testid="deal-view">
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 'var(--s-2)',
        }}
      >
        <h2
          data-testid="deal-display-name"
          style={{ fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}
        >
          {deal.display_name}
        </h2>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)' }}>
          {deal.recovery_state != null && (
            <RecoveryTag state={deal.recovery_state} />
          )}
          {deal.contact_status != null && (
            <RecencyChip status={deal.contact_status} testId="deal-recency" />
          )}
          <Chip tone={isTefa ? 'gate' : 'flow'}>{fundingDisplay}</Chip>
        </div>
      </div>

      {/* Who to call — the household's primary contact person + click-to-dial,
          so "The Rivera Family" becomes an actionable lead on the phone. */}
      <ContactBar contact={state.contact} />

      <div
        style={{
          marginTop: 'var(--s-3)',
          padding: 'var(--s-3) var(--s-4)',
          background: 'var(--signal-wash)',
          border: '1px solid var(--signal)',
          borderRadius: 'var(--r-md)',
        }}
      >
        <div
          className="lab"
          style={{
            color: 'var(--signal-ink)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
          }}
        >
          <AlertTriangle size={11} aria-hidden /> Why they haven&apos;t converted
        </div>
        <div
          data-testid="deal-stall-reason"
          style={{
            marginTop: 'var(--s-1)',
            fontSize: 'var(--fs-sm)',
            color: 'var(--signal-ink)',
          }}
        >
          {display(deal.stall_reason)}
        </div>
      </div>

      {/* Where they left off — application completion + form progress (FR-2.2;
          S9 Wave 4). Rendered only when the family has application/form data. */}
      {(deal.completion_pct != null || deal.forms_total != null) && (
        <div
          data-testid="deal-dropoff"
          style={{
            marginTop: 'var(--s-3)',
            padding: 'var(--s-3) var(--s-4)',
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
          }}
        >
          <div className="lab">Where they left off</div>
          <div
            style={{
              marginTop: 'var(--s-2)',
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--s-3)',
            }}
          >
            {showDropoffRing && <CompletionRing pct={dropoffRingPct} />}
            <div style={{ minWidth: 0 }}>
              <div
                data-testid="deal-completion"
                className="mono"
                style={{ fontSize: 'var(--fs-sm)', color: 'var(--ink)' }}
              >
                {inEnrollment
                  ? `Application ✓ submitted · Enrollment ${deal.forms_signed ?? 0} of ${deal.forms_total} forms`
                  : completion != null
                    ? `${completion}% application complete`
                    : PLACEHOLDER}
              </div>
              {stuckForm != null && (
                <div
                  data-testid="deal-next-form"
                  style={{
                    marginTop: 'var(--s-1)',
                    fontSize: 'var(--fs-sm)',
                    color: 'var(--signal-ink)',
                  }}
                >
                  Stuck on: {stuckForm}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Per-child progress (DH-5) — WHICH children this family has and where
          EACH left off in the funnel. A single-child family shows its one child;
          a multi-child household (e.g. the Riveras) shows BOTH at their own
          (possibly different) stages. Sourced from GET /students (A-24). */}
      <ChildrenSection children={children} />

      {/* Assignment-history timeline (LA-23) — the per-family ownership audit:
          every assign / reassign as an append-only from→to/reason fact. Omitted
          when the family has no history (an un-routed interest lead). */}
      <AssignmentTimeline events={assignments} names={agentNames} />

      <dl
        className="deal-fields"
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 'var(--s-2)',
          margin: 'var(--s-3) 0 0',
        }}
      >
        <DealField
          label="Funding type"
          value={fundingDisplay}
          testId="deal-funding-type"
        />
        <ConversionField
          band={deal.conversion_band ?? null}
          score={deal.conversion_score ?? null}
          topFactorLabel={deal.conversion_top_factor_label ?? null}
        />
        <DealField
          label="Attribution source"
          value={deal.attribution_source}
          testId="deal-attribution"
        />
        <SeamField
          status={
            seed.status === 'captured'
              ? seed.data.seam_status
              : deal.crm_seam_status
          }
        />
      </dl>

      {/* CRM seam indicator (S14 W4) — surfaces the HubSpot kill switch / CRM mode
          so the operator can SEE the seam state and the live-push fails closed when
          the kill switch is on (INV-3 pattern; INV-8). NO secret is shown. */}
      <CrmSeamBadge crm={crm} />

      {/* "Seed to HubSpot" (S10 W3) — push this synthetic family live into the
          real portal, then surface the captured Deal + Contact ids as deep links.
          The deterministic backend route owns the write (INV-2); this button only
          triggers it and renders the proof. The live-push FAILS CLOSED when the
          HubSpot kill switch is on (S14 W4; INV-8) — disabled with a reason. */}
      <div
        style={{
          marginTop: 'var(--s-3)',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          flexWrap: 'wrap',
        }}
      >
        <Button
          variant="primary"
          icon={UploadCloud}
          data-testid="seed-hubspot"
          onClick={seedToHubSpot}
          disabled={seed.status === 'seeding' || killSwitchOn}
          title={
            killSwitchOn
              ? 'HubSpot kill switch is ON — live sync is disabled (INV-8). Clear HUBSPOT_KILL_SWITCH to re-enable.'
              : undefined
          }
        >
          {seed.status === 'seeding' ? 'Seeding…' : 'Seed to HubSpot'}
        </Button>
        {killSwitchOn && (
          <span
            data-testid="seed-kill-switch-note"
            role="status"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
          >
            Kill switch ON — live sync disabled
          </span>
        )}
        {seed.status === 'error' && (
          <span
            data-testid="seed-error"
            role="alert"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--signal-ink)' }}
          >
            {seed.message}
          </span>
        )}
        {/* Dismiss this family (S12 W4; A-19) — an audited remove from the active
            board. The WRITE is the parent's (one route); this only opens the
            reason picker and calls back. Hidden once already dismissed. */}
        {onDismiss !== undefined &&
          dismissReasons !== undefined &&
          deal.recovery_state !== 'dismissed' &&
          deal.recovery_state !== 'recovered' && (
            <Button
              icon={XCircle}
              data-testid="dismiss-family-start"
              onClick={() => setDismissing((on) => !on)}
            >
              Dismiss this family…
            </Button>
          )}
      </div>

      {dismissing && onDismiss !== undefined && dismissReasons !== undefined && (
        <div
          data-testid="dismiss-family-reasons"
          style={{
            marginTop: 'var(--s-2)',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 'var(--s-2)',
            alignItems: 'center',
            padding: 'var(--s-2) var(--s-3)',
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 'var(--r-md)',
          }}
        >
          <span className="lab">reason:</span>
          {dismissReasons.map((r) => (
            <button
              key={r}
              type="button"
              data-testid={`dismiss-family-reason-${r}`}
              onClick={() => {
                onDismiss(familyId, r);
                setDismissing(false);
              }}
              style={{
                border: '1px solid var(--line)',
                background: 'var(--surface)',
                fontSize: 11.5,
                fontWeight: 600,
                padding: '5px 10px',
                borderRadius: 'var(--r-pill)',
                color: 'var(--ink)',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {r}
            </button>
          ))}
          <button
            type="button"
            data-testid="dismiss-family-cancel"
            onClick={() => setDismissing(false)}
            style={{
              border: '1px solid transparent',
              background: 'transparent',
              color: 'var(--muted)',
              fontSize: 11.5,
              fontWeight: 600,
              padding: '5px 10px',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            cancel
          </button>
        </div>
      )}

      {seed.status === 'captured' && (
        <CapturePanel data={seed.data} />
      )}
    </section>
  );
}

// The CRM seam indicator (S14 W4). Surfaces the effective HubSpot seam so the
// operator SEES the state and the live-push fails closed when the kill switch is
// on (INV-3 pattern; INV-8). NO secret: it reads only the booleans/mode from
// /crm/status. Renders nothing until the status resolves (fail open on absent).
//   - kill switch ON ⇒ a signal-tone "Kill switch ON — live sync disabled" chip.
//   - effective live ⇒ a flow-tone "CRM: LIVE" chip (writes land in the portal).
//   - otherwise      ⇒ a neutral "CRM: Simulated" chip (recorded, never sent).
function CrmSeamBadge({ crm }: { crm: CrmStatus | null }): JSX.Element | null {
  if (crm === null) return null;
  if (crm.kill_switch) {
    return (
      <div data-testid="crm-seam-badge" style={{ marginTop: 'var(--s-3)' }}>
        <span data-testid="crm-seam-state" data-crm-effective={crm.effective_mode}>
          <Chip tone="signal" title="HubSpot kill switch is ON — live writes are disabled (INV-8).">
            Kill switch ON — live sync disabled
          </Chip>
        </span>
      </div>
    );
  }
  const live = crm.effective_mode === 'live';
  return (
    <div data-testid="crm-seam-badge" style={{ marginTop: 'var(--s-3)' }}>
      <span data-testid="crm-seam-state" data-crm-effective={crm.effective_mode}>
        <Chip
          tone={live ? 'flow' : 'neutral'}
          title={
            live
              ? 'CRM seam is LIVE — synthetic pushes land in the real HubSpot portal.'
              : 'CRM seam is simulated — pushes are recorded, never sent (INV-9).'
          }
        >
          {live ? 'CRM: LIVE' : 'CRM: Simulated'}
        </Chip>
      </span>
    </div>
  );
}

// The proof-of-capture panel (S10 W3). Renders the live HubSpot Deal + Contact
// ids returned by the seed route as click-through deep links into the real
// portal, plus the flipped seam badge — "✓ captured in HubSpot."
function CapturePanel({ data }: { data: SeedResponse }): JSX.Element {
  return (
    <div
      data-testid="capture-panel"
      role="status"
      style={{
        marginTop: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        background: 'var(--flow-wash)',
        border: '1px solid var(--flow)',
        borderRadius: 'var(--r-md)',
      }}
    >
      <div
        className="lab"
        style={{
          color: 'var(--flow-ink)',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
        }}
      >
        <CheckCircle2 size={11} aria-hidden /> Captured in HubSpot
        {data.simulated ? ' (simulated)' : ''}
      </div>
      <div
        style={{
          marginTop: 'var(--s-2)',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--s-3)',
        }}
      >
        <CaptureLink
          label="Deal"
          href={hubspotDealUrl(data.deal_id)}
          id={data.deal_id}
          testId="capture-deal-link"
        />
        {data.contact_id != null && (
          <CaptureLink
            label="Contact"
            href={hubspotContactUrl(data.contact_id)}
            id={data.contact_id}
            testId="capture-contact-link"
          />
        )}
        <div>
          <div className="lab">Seam</div>
          <div
            data-testid="capture-seam-status"
            className="mono"
            style={{ fontSize: 'var(--fs-sm)', color: 'var(--flow-ink)', marginTop: 2 }}
          >
            {data.seam_status}
          </div>
        </div>
      </div>
    </div>
  );
}

// One labelled deep link into a live HubSpot record.
function CaptureLink({
  label,
  href,
  id,
  testId,
}: {
  label: string;
  href: string;
  id: string;
  testId: string;
}): JSX.Element {
  return (
    <div>
      <div className="lab">{label}</div>
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        data-testid={testId}
        className="mono"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
          fontSize: 'var(--fs-sm)',
          color: 'var(--flow-ink)',
          marginTop: 2,
        }}
      >
        {id}
        <ExternalLink size={11} aria-hidden />
      </a>
    </div>
  );
}
