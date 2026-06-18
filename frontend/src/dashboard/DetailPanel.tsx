import { useEffect, useState } from 'react';
import {
  Building2,
  GraduationCap,
  Mail,
  MapPin,
  Phone,
  Sparkles,
  Tag,
  TrendingUp,
  Users,
} from 'lucide-react';
import { apiFetch } from '../config';
import { Card, Chip, PlaceholderBadge } from '../ui';
import { fundingLabel, humanizeSegment } from '../enrollment/format';
import CompletionRing from '../enrollment/CompletionRing';
import SeamDot, { type SeamStatus } from '../enrollment/SeamDot';
import CloseTipsPanel from '../enrollment/CloseTipsPanel';
import NotesTimeline from '../enrollment/NotesTimeline';
import LogCallForm from '../enrollment/LogCallForm';
import AiDrafts from './AiDrafts';
import EmptyState from './EmptyState';

// The right-column CONTEXTUAL DETAIL PANEL (admin-dashboard redesign). For a
// selected family it fetches GET /families/{id} (deal_view + lead) and GET
// /students, then renders the twelve sections in the exact brief order. It REUSES
// the existing primitives + sub-panels (CloseTipsPanel, NotesTimeline, SeamDot,
// CompletionRing) and the shared contact-outcome form (LogCallForm). It deliberately
// does NOT mount FundingTracker — the funding/TEFA gate block is removed; only the
// small inline funding TYPE field (§5) remains. Read-only fetches (INV-2).

const PLACEHOLDER = '—';

// The deal_view projection (the subset the panel renders). API-composed; the
// contact/location/guardian fields are the D-6/D-7 enrichment.
interface DealView {
  display_name: string;
  funding_type: string;
  attribution_source: string;
  crm_seam_status: string;
  conversion_band?: string | null;
  conversion_score?: number | null;
  conversion_top_factor_label?: string | null;
  primary_contact_name?: string | null;
  primary_contact_synthetic_email?: string | null;
  primary_contact_synthetic_phone?: string | null;
  guardian_1_relationship?: string | null;
  secondary_contact_name?: string | null;
  secondary_contact_synthetic_email?: string | null;
  secondary_contact_synthetic_phone?: string | null;
  guardian_2_relationship?: string | null;
  neighborhood?: string | null;
  region?: string | null;
  state?: string | null;
}

interface FamilyResponse {
  deal_view: DealView;
}

interface StudentRow {
  student_id: string;
  family_id: string;
  synthetic_first_name: string;
  grade: string;
  current_stage: string;
}
interface HouseholdGroup {
  family_id: string;
  students: StudentRow[];
}
interface StudentBoardResponse {
  households: HouseholdGroup[];
}

function isStudentBoard(value: unknown): value is StudentBoardResponse {
  if (typeof value !== 'object' || value === null) return false;
  return Array.isArray((value as Record<string, unknown>).households);
}

// The per-child funnel stage → a completion percentage for the ring. The funnel is
// interest → apply → enroll → tuition (Stage enum); each step is a quarter of the
// way to a closed, funded enrollment.
const STAGE_PCT: Record<string, number> = {
  interest: 25,
  apply: 50,
  enroll: 75,
  tuition: 100,
};

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; deal: DealView };

interface DetailPanelProps {
  // null ⇒ nothing selected on the left; the panel shows the shared empty state.
  familyId: string | null;
}

// A titled section wrapper — a small mono label + the section body.
function Section({
  title,
  icon: Icon,
  testId,
  children,
}: {
  title: string;
  icon: typeof Users;
  testId: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <section className="admin-section" data-testid={testId}>
      <div className="admin-section-title">
        <Icon size={12} aria-hidden /> {title}
      </div>
      {children}
    </section>
  );
}

function Parent({
  name,
  relationship,
}: {
  name: string | null | undefined;
  relationship: string | null | undefined;
}): JSX.Element {
  return (
    <div className="admin-kv">
      <span className="admin-kv-name">{name ?? PLACEHOLDER}</span>
      {relationship != null && relationship !== '' && (
        <span className="admin-kv-sub">{humanizeSegment(relationship)}</span>
      )}
    </div>
  );
}

export default function DetailPanel({ familyId }: DetailPanelProps): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [children, setChildren] = useState<StudentRow[] | null>(null);
  // A refresh nonce bumped after a logged outcome so the notes timeline re-pulls.
  const [, setRefresh] = useState(0);

  useEffect(() => {
    if (familyId === null) return;
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/families/${familyId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`family request failed: ${res.status}`);
        return res.json() as Promise<FamilyResponse>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', deal: data.deal_view });
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
  }, [familyId]);

  useEffect(() => {
    if (familyId === null) return;
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
  }, [familyId]);

  // Nothing selected ⇒ the shared clean empty state (both briefs require it).
  if (familyId === null) {
    return (
      <Card>
        <EmptyState
          icon={<Users size={20} aria-hidden />}
          title="No family selected"
          body="Pick a lead or family on the left to see the full detail here."
        />
      </Card>
    );
  }

  if (state.status === 'loading') {
    return (
      <p data-testid="detail-loading" className="lab">
        Loading the family…
      </p>
    );
  }
  if (state.status === 'error') {
    return (
      <p
        data-testid="detail-error"
        role="alert"
        style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
      >
        Could not load the family: {state.message}
      </p>
    );
  }

  const deal = state.deal;
  const seamNormalized = deal.crm_seam_status.toLowerCase();
  const seamDot: SeamStatus =
    seamNormalized === 'synced'
      ? 'synced'
      : seamNormalized === 'conflict'
        ? 'conflict'
        : 'unsynced';
  const seamLabel = seamNormalized === 'synced' ? 'Synced' : 'Unsynced';
  const convScore = deal.conversion_score ?? null;
  const convBand = deal.conversion_band ?? null;
  const convPct = convScore === null ? null : Math.round(convScore * 100);
  const convHeadline =
    convBand === null
      ? PLACEHOLDER
      : convPct === null
        ? convBand
        : `${convBand} · ${convPct}%`;

  return (
    <Card>
      <div className="admin-panel" data-testid="detail-panel" data-family={familyId}>
        <h2
          data-testid="detail-name"
          style={{ fontSize: 'var(--fs-md)', fontWeight: 700, margin: 0 }}
        >
          {deal.display_name}
        </h2>

        {/* 1 — Parents */}
        <Section title="Parents" icon={Users} testId="detail-parents">
          <Parent
            name={deal.primary_contact_name}
            relationship={deal.guardian_1_relationship}
          />
          {deal.secondary_contact_name != null &&
            deal.secondary_contact_name !== '' && (
              <Parent
                name={deal.secondary_contact_name}
                relationship={deal.guardian_2_relationship}
              />
            )}
        </Section>

        <div className="admin-panel-rule" />

        {/* 2 — Contact: both emails + both phones */}
        <Section title="Contact" icon={Phone} testId="detail-contact">
          <ContactLink
            kind="email"
            value={deal.primary_contact_synthetic_email}
            testId="detail-email-primary"
          />
          <ContactLink
            kind="phone"
            value={deal.primary_contact_synthetic_phone}
            testId="detail-phone-primary"
          />
          <ContactLink
            kind="email"
            value={deal.secondary_contact_synthetic_email}
            testId="detail-email-secondary"
          />
          <ContactLink
            kind="phone"
            value={deal.secondary_contact_synthetic_phone}
            testId="detail-phone-secondary"
          />
        </Section>

        <div className="admin-panel-rule" />

        {/* 3 — Location (aggregate labels only; D-5) */}
        <Section title="Location" icon={MapPin} testId="detail-location">
          <span className="admin-kv-name" data-testid="detail-location-value">
            {[deal.neighborhood, deal.region, deal.state]
              .filter((v) => v != null && v !== '')
              .join(' · ') || PLACEHOLDER}
          </span>
        </Section>

        <div className="admin-panel-rule" />

        {/* 4 — Children & per-child progress */}
        <Section
          title="Children & progress"
          icon={GraduationCap}
          testId="detail-children"
        >
          <span className="admin-kv-sub" data-testid="detail-children-count">
            {children === null
              ? 'Loading children…'
              : children.length === 0
                ? 'No children on file'
                : `${children.length} ${children.length === 1 ? 'child' : 'children'}`}
          </span>
          {children?.map((child) => {
            const pct = STAGE_PCT[child.current_stage.toLowerCase()] ?? 0;
            const grade = child.grade ? `Grade ${child.grade}` : PLACEHOLDER;
            return (
              <div
                key={child.student_id}
                className="admin-child"
                data-testid={`detail-child-${child.student_id}`}
              >
                <CompletionRing pct={pct} />
                <div className="admin-child-meta">
                  <span className="admin-child-name">
                    {child.synthetic_first_name || PLACEHOLDER} · {grade}
                  </span>
                  <span className="admin-kv-sub" data-testid="detail-child-stage">
                    {humanizeSegment(child.current_stage) || PLACEHOLDER}
                  </span>
                </div>
              </div>
            );
          })}
        </Section>

        <div className="admin-panel-rule" />

        {/* 5 — Funding type (inline TYPE field only; the TEFA tracker is removed) */}
        <Section title="Funding type" icon={Tag} testId="detail-funding">
          <span data-testid="detail-funding-value">
            <Chip tone={deal.funding_type.toLowerCase().includes('tefa') ? 'gate' : 'flow'}>
              {fundingLabel(deal.funding_type)}
            </Chip>
          </span>
        </Section>

        <div className="admin-panel-rule" />

        {/* 6 — Conversion factor (likelihood to convert) */}
        <Section title="Conversion factor" icon={TrendingUp} testId="detail-conversion">
          <span className="admin-kv-name" data-testid="detail-conversion-value">
            {convHeadline}
          </span>
          {deal.conversion_top_factor_label != null && (
            <span className="admin-kv-sub" data-testid="detail-conversion-factor">
              Top factor: {deal.conversion_top_factor_label}
            </span>
          )}
        </Section>

        <div className="admin-panel-rule" />

        {/* 7 — Source (attribution) */}
        <Section title="Source" icon={Building2} testId="detail-source">
          <span className="admin-kv-name" data-testid="detail-source-value">
            {deal.attribution_source || PLACEHOLDER}
          </span>
        </Section>

        <div className="admin-panel-rule" />

        {/* 8 — HubSpot sync status */}
        <Section title="HubSpot sync" icon={Sparkles} testId="detail-seam">
          <span
            data-testid="detail-seam-value"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)' }}
          >
            <SeamDot status={seamDot} />
            <Chip tone={seamDot === 'synced' ? 'flow' : seamDot === 'conflict' ? 'signal' : 'neutral'}>
              {seamLabel}
            </Chip>
          </span>
        </Section>

        <div className="admin-panel-rule" />

        {/* 9 — How to close (eval-gated grounded tips) */}
        <div data-testid="detail-close-tips">
          <CloseTipsPanel familyId={familyId} />
        </div>

        <div className="admin-panel-rule" />

        {/* 10 — AI drafts (ungated email + sms, editable; D-1) */}
        <Section title="AI drafts" icon={Sparkles} testId="detail-ai-drafts">
          <AiDrafts familyId={familyId} />
        </Section>

        <div className="admin-panel-rule" />

        {/* 11 — Manual notes */}
        <div data-testid="detail-notes">
          <NotesTimeline familyId={familyId} />
        </div>

        <div className="admin-panel-rule" />

        {/* 12 — Log a call (the shared enrollment/LogCallForm) */}
        <Section title="Log a call" icon={Phone} testId="detail-log-call">
          <LogCallForm
            familyId={familyId}
            onLogged={() => setRefresh((n) => n + 1)}
          />
        </Section>
      </div>
    </Card>
  );
}

// One contact line — a mailto:/tel: link, or a PlaceholderBadge when the field is
// null (the brief's explicit null treatment).
function ContactLink({
  kind,
  value,
  testId,
}: {
  kind: 'email' | 'phone';
  value: string | null | undefined;
  testId: string;
}): JSX.Element {
  if (value == null || value === '') {
    return (
      <span data-testid={testId}>
        <PlaceholderBadge label="none on file" />
      </span>
    );
  }
  const href = kind === 'email' ? `mailto:${value}` : `tel:${value}`;
  const Icon = kind === 'email' ? Mail : Phone;
  return (
    <a href={href} className="admin-contact-link" data-testid={testId}>
      <Icon size={12} aria-hidden /> {value}
    </a>
  );
}
