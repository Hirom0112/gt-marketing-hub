'use client';

// Nurture & Lifecycle (Module 5) — the Marketing Lead's data-richest surface, wired
// end-to-end to the FastAPI backbone. Six controlled sub-views (TabBar):
//   5a Overview      — composable widget grid from GET /nurture/overview: T1/T2/T3
//                      counts + reachability, engagement-tier mix (LIVE HUBSPOT), latest
//                      send / SLA / SMS-reply health, top sequence, cold-segment count,
//                      persona×engagement crosstab, pipeline distribution, handoff count.
//   5b Segments      — T1/T2/T3 panels + the engagement×attribute conversion HEATMAP
//                      (income/region dims) + an OWNER-gated segment builder (POST
//                      /nurture/segments/build — the backend computes the audience size).
//   5c Pipeline      — parent/child stage distribution bars, stuck-in-stage alerts,
//                      velocity, marketing→onboarding handoff (LIVE HUBSPOT aggregate).
//   5d Sequences     — read-only synthetic mirror by type, per-step open/click/conv,
//                      health flag + a LEADER-only approve/kill affordance.
//   5e SMS inbox     — thread list with status filters, auto-theme chips (keyword-vs-LLM
//                      surfaced honestly), quick-reply snippets (UI), a "flag → hot family"
//                      (→ Decision Queue) + an "objection → content brief" (→ Module 3).
//   5f SLA tracker   — applicants today, % contacted in 24h, late list (red), per-owner
//                      attributable breakdown, 30-day history.
// Every read falls back to a per-resource seed (lib/nurture-api) so the screen never
// blanks; the LIVE/SAMPLE pill is honest, and each surface renders its provenance badge
// from the backend `source`/`tag_mode` string (LIVE HUBSPOT / SYNTHETIC MIRROR / app_form).

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { canEditWorkstream, moduleById, type Role } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet, apiPost } from '@/lib/api';
import {
  type NurtureOverview,
  type SegmentsResponse,
  type PipelineResponse,
  type SequencesResponse,
  type SmsResponse,
  type SlaResponse,
  type HeatmapCell,
  type NurtureSegment,
  type SmsThread,
  type SegmentBuildRequest,
  type DecisionResponse,
  type ObjectionBriefResponse,
  SEED_OVERVIEW,
  SEED_SEGMENTS,
  SEED_PIPELINE,
  SEED_SEQUENCES,
  SEED_SMS,
  SEED_SLA,
  sourceBadge,
  badgeStyle,
  tagModeLabel,
  engagementTierStyle,
  ENGAGEMENT_TIER_ORDER,
  smsStatusStyle,
  SMS_STATUS_FILTERS,
  themeTagStyle,
  themeLabel,
  seqTypeLabel,
  stageLabel,
  HANDOFF_STAGES,
  attrValueLabel,
  dimensionLabel,
  heatCellStyle,
  fmtInbound,
  BUILDER_TIERS,
  BUILDER_INCOME_OPTIONS,
} from '@/lib/nurture-api';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

interface Toast { msg: string; kind: 'ok' | 'err'; href?: string; }
type Notify = (m: string, k: 'ok' | 'err', href?: string) => void;
type Ctx = { role: Role; canEdit: boolean; isLeader: boolean; refetch: () => void; notify: Notify };

// ============================ the module =====================================
export function NurtureModule() {
  const { session } = useSession();
  const def = moduleById('nurture')!;
  const canEdit = canEditWorkstream(session, 'nurture'); // admin always; operator only if owns 'nurture' (demo: admin only)
  const isLeader = session.role === 'leader' || session.role === 'admin';
  const role = session.role;

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  const [overview, setOverview] = useState<NurtureOverview | null>(null);
  const [segments, setSegments] = useState<SegmentsResponse | null>(null);
  const [pipeline, setPipeline] = useState<PipelineResponse | null>(null);
  const [sequences, setSequences] = useState<SequencesResponse | null>(null);
  const [sms, setSms] = useState<SmsResponse | null>(null);
  const [sla, setSla] = useState<SlaResponse | null>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(() => {
    apiGet<NurtureOverview>('/nurture/overview', role).then((d) => {
      if (d && d.engagement_tier_mix) { setOverview(d); setLive(true); }
      else { setOverview(SEED_OVERVIEW); setLive(false); }
    });
    apiGet<SegmentsResponse>('/nurture/segments', role).then((d) => setSegments(d && Array.isArray(d.segments) ? d : SEED_SEGMENTS));
    apiGet<PipelineResponse>('/nurture/pipeline', role).then((d) => setPipeline(d && Array.isArray(d.stages) ? d : SEED_PIPELINE));
    apiGet<SequencesResponse>('/nurture/sequences', role).then((d) => setSequences(d && Array.isArray(d.sequences) ? d : SEED_SEQUENCES));
    apiGet<SmsResponse>('/nurture/sms', role).then((d) => setSms(d && Array.isArray(d.threads) ? d : SEED_SMS));
    apiGet<SlaResponse>('/nurture/sla', role).then((d) => setSla(d && Array.isArray(d.late) ? d : SEED_SLA));
  }, [role]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback<Notify>((msg, kind, href) => setToast({ msg, kind, href }), []);

  const ov = overview ?? SEED_OVERVIEW;
  const seg = segments ?? SEED_SEGMENTS;
  const pipe = pipeline ?? SEED_PIPELINE;
  const seqs = sequences ?? SEED_SEQUENCES;
  const inbox = sms ?? SEED_SMS;
  const slaData = sla ?? SEED_SLA;
  const ctx: Ctx = { role, canEdit, isLeader, refetch: load, notify };

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <Header idx={def.idx} title={def.title} owner={def.owner} canEdit={canEdit} live={live} />

        {tab === 0 && <OverviewTab ov={ov} />}
        {tab === 1 && <SegmentsTab seg={seg} {...ctx} />}
        {tab === 2 && <PipelineTab pipe={pipe} />}
        {tab === 3 && <SequencesTab seqs={seqs} {...ctx} />}
        {tab === 4 && <SmsTab inbox={inbox} {...ctx} />}
        {tab === 5 && <SlaTab sla={slaData} {...ctx} />}

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>⌖ {def.source} · execution stays in HubSpot — the cockpit owns the dials + reads aggregate</div>
      </section>
    </>
  );
}

// ============================ header band ====================================
function Header({ idx, title, owner, canEdit, live }: { idx: string; title: string; owner: string; canEdit: boolean; live: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 14, borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
      <div>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '1px', color: 'var(--ink-3)', marginBottom: 5 }}>
          MODULE {idx} · OWNER: {owner.toUpperCase()}
        </div>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 16, color: 'var(--ink)' }}>{title}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <StatusPill live={live} />
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: canEdit ? 'var(--gold-soft)' : 'var(--accent-soft)', color: canEdit ? 'var(--gold)' : 'var(--ink-3)' }}>
          {canEdit ? '✎ EDITABLE — your workstream' : '◌ READ-ONLY'}
        </span>
      </div>
    </div>
  );
}

// =============================== 5a · OVERVIEW ===============================
function OverviewTab({ ov }: { ov: NurtureOverview }) {
  const mix = ov.engagement_tier_mix;
  const engBadge = sourceBadge(ov.engagement_source);
  const tierByName = (t: string) => ov.tiers.find((x) => x.tier === t);
  const handoffStage = ov.pipeline_stage_distribution.filter((s) => HANDOFF_STAGES.includes(s.stage));
  const handoffTotal = handoffStage.reduce((a, s) => a + s.count, 0);

  return (
    <>
      {/* top stat row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="24-HR SLA" value={`${ov.sla_compliance_pct}%`} sub="contacted in window · owner-attributable" tone={ov.sla_compliance_pct >= 90 ? 'ok' : 'warn'} />
        <StatTile label="MKTG → ONBOARDING HANDOFF" value={String(ov.handoff_this_week)} sub={`this week · ${handoffTotal} in enroll/tuition`} />
        <StatTile label="SMS REPLIES THIS WEEK" value={String(ov.sms_reply_count_this_week)} sub={`${ov.sms_replied_total} replied all-time`} />
        <StatTile label="SEQUENCES HEALTHY" value={`${ov.sequences_healthy}/${ov.sequences_total}`} sub={ov.top_sequence ? `top: ${ov.top_sequence}` : 'no sequences'} />
      </div>

      {/* engagement-tier mix — LIVE HUBSPOT aggregate */}
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8, flexWrap: 'wrap', gap: 6 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Engagement-tier mix</div>
          <SourceBadge info={engBadge} />
        </div>
        <div style={{ display: 'flex', height: 16, border: '1px solid var(--line)', overflow: 'hidden', marginBottom: 8 }}>
          {ENGAGEMENT_TIER_ORDER.map((t) => {
            const v = t === 'clicked' ? mix.clicked : t === 'opened' ? mix.opened : mix.cold;
            const pct = mix.total ? (100 * v) / mix.total : 0;
            const s = engagementTierStyle(t);
            return <div key={t} title={`${s.label}: ${v}`} style={{ width: `${pct}%`, background: s.color, opacity: t === 'cold' ? 0.4 : 0.85 }} />;
          })}
        </div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          {ENGAGEMENT_TIER_ORDER.map((t) => {
            const v = t === 'clicked' ? mix.clicked : t === 'opened' ? mix.opened : mix.cold;
            const s = engagementTierStyle(t);
            return (
              <span key={t} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--ink-2)' }}>
                <span style={{ width: 10, height: 10, background: s.color, opacity: t === 'cold' ? 0.4 : 0.85, display: 'inline-block' }} />
                {s.label} <b style={{ fontFamily: MONO, color: 'var(--ink)' }}>{v.toLocaleString()}</b>
              </span>
            );
          })}
          <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)' }}>
            {mix.reachable.toLocaleString()} reachable · {mix.reachability_pct}% of {mix.total.toLocaleString()}
          </span>
        </div>
      </div>

      {/* tier panels + crosstab */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 14, marginBottom: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 3 }}>Tier counts &amp; reachability</div>
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 10 }}>cold segments: {ov.cold_segment_count}</div>
          {['T1', 'T2', 'T3'].map((t) => {
            const p = tierByName(t);
            if (!p) return null;
            return (
              <div key={t} style={{ padding: '8px 0', borderTop: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 600 }}>{t} · {p.segment_count} seg</span>
                  <span style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{p.audience_size.toLocaleString()}</span>
                </div>
                <ReachBar pct={p.reachability_pct} />
                <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 3 }}>{p.reachability_pct}% reachable · plan {p.planning_size.toLocaleString()}</div>
              </div>
            );
          })}
        </div>

        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8, flexWrap: 'wrap', gap: 6 }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Engagement × income crosstab</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>conversion % · SUPA × HUBS</span>
          </div>
          <Heatmap cells={ov.engagement_attribute_crosstab} dimension="income" />
        </div>
      </div>

      {/* pipeline distribution mini + cross-links */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Pipeline stage distribution</div>
            <SourceBadge info={engBadge} />
          </div>
          {ov.pipeline_stage_distribution.map((s) => <StageBar key={s.stage} s={s} />)}
        </div>
        <CrossLinks />
      </div>
    </>
  );
}

// =============================== 5b · SEGMENTS ===============================
function SegmentsTab({ seg, role, canEdit, notify, refetch }: { seg: SegmentsResponse } & Ctx) {
  const [dim, setDim] = useState<string>(Object.keys(seg.heatmap)[0] ?? 'income');
  const [showBuilder, setShowBuilder] = useState(false);
  const badge = sourceBadge(seg.source);
  const dims = Object.keys(seg.heatmap);
  const activeDim = seg.heatmap[dim] ? dim : (dims[0] ?? 'income');

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <SourceBadge info={badge} />
        {canEdit ? (
          <button onClick={() => setShowBuilder((s) => !s)} style={{ ...PRIMARY_BTN, cursor: 'pointer' }}>{showBuilder ? '✕ CLOSE BUILDER' : '+ BUILD SEGMENT'}</button>
        ) : (
          <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ SEGMENT BUILDER — OWNER-GATED</span>
        )}
      </div>

      {canEdit && showBuilder && (
        <div style={{ marginBottom: 14 }}>
          <SegmentBuilder role={role} notify={notify} refetch={() => { refetch(); setShowBuilder(false); }} />
        </div>
      )}

      {/* tier panels */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        {['T1', 'T2', 'T3'].map((t) => {
          const p = seg.tiers.find((x) => x.tier === t);
          const subs = seg.segments.filter((s) => s.tier === t);
          return (
            <div key={t} style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 13 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>{t}</span>
                <span style={{ fontFamily: MONO, fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{(p?.audience_size ?? 0).toLocaleString()}</span>
              </div>
              <ReachBar pct={p?.reachability_pct ?? 0} />
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 3, marginBottom: 8 }}>{p?.reachability_pct ?? 0}% reachable · plan {(p?.planning_size ?? 0).toLocaleString()}</div>
              {subs.map((s) => (
                <div key={s.segment_id} style={{ borderTop: '1px solid var(--line)', padding: '6px 0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                    <span style={{ fontSize: 10.5, color: 'var(--ink)', fontWeight: 500 }}>{s.label}</span>
                    <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{s.size.toLocaleString()}</span>
                  </div>
                  <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 2, lineHeight: 1.35 }}>{s.notes}</div>
                </div>
              ))}
              {subs.length === 0 && <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', borderTop: '1px solid var(--line)', paddingTop: 6 }}>No sub-buckets yet.</div>}
            </div>
          );
        })}
      </div>

      {/* the heatmap */}
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Engagement × attribute heatmap</div>
          <div style={{ display: 'flex', gap: 6 }}>
            {dims.map((d) => (
              <button key={d} onClick={() => setDim(d)} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', padding: '4px 10px', border: `1px solid ${d === activeDim ? 'var(--ink)' : 'var(--line-2)'}`, background: d === activeDim ? 'var(--ink)' : 'var(--card)', color: d === activeDim ? 'var(--paper)' : 'var(--ink-2)' }}>{dimensionLabel(d)}</button>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 11 }}>Conversion % per engagement tier × {dimensionLabel(activeDim).toLowerCase()} bucket. Color = conversion rate; clicked-cohort is the hottest predictor.</div>
        <Heatmap cells={seg.heatmap[activeDim] ?? []} dimension={activeDim} />
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Income/region buckets join from the Supabase app_form source of truth (aggregate labels only — never PII). {canEdit ? 'Build a segment to compute a sized, reachability-tagged audience (POST /nurture/segments/build).' : 'The segment builder is owner-gated (the Marketing Lead / admin).'}
      </div>
    </>
  );
}

function SegmentBuilder({ role, notify, refetch }: { role: Role; notify: Notify; refetch: () => void }) {
  const [tier, setTier] = useState<string>(BUILDER_TIERS[0]);
  const [label, setLabel] = useState('');
  const [tiers, setTiers] = useState<string[]>(['clicked']);
  const [incomes, setIncomes] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [saving, setSaving] = useState(false);
  const [size, setSize] = useState<number | null>(null);

  const toggle = (arr: string[], v: string, set: (a: string[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

  const submit = async () => {
    setSaving(true);
    const filters: Record<string, string[]> = {};
    if (incomes.length) filters.income = incomes;
    const body: SegmentBuildRequest = {
      tier,
      label: label.trim() || `${tier} · custom segment`,
      engagement_tiers: tiers.length ? tiers : null,
      attribute_filters: filters,
      notes: notes.trim(),
    };
    const res = await apiPost<NurtureSegment>('/nurture/segments/build', role, body);
    setSaving(false);
    if (!res || !res.segment_id) { notify('Could not build the segment — Marketing Lead (admin) access is required and the backbone must be up.', 'err'); return; }
    setSize(res.size);
    notify(`Built "${res.label}" — ${res.size.toLocaleString()} families · ${Math.round(res.reachability_pct)}% reachable.`, 'ok');
    refetch();
  };

  return (
    <FormCard title="BUILD SEGMENT" tag="OWNER · POST /nurture/segments/build">
      <Row>
        <Field label="TIER"><select value={tier} onChange={(e) => setTier(e.target.value)} style={SELECT}>{BUILDER_TIERS.map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
        <Field label="LABEL"><input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. T1 · Ready, Austin" style={INPUT} /></Field>
      </Row>
      <Field label="ENGAGEMENT TIERS">
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {ENGAGEMENT_TIER_ORDER.map((t) => {
            const on = tiers.includes(t);
            const s = engagementTierStyle(t);
            return <ToggleChip key={t} on={on} onClick={() => toggle(tiers, t, setTiers)} color={s.color} bg={s.bg}>{s.label}</ToggleChip>;
          })}
        </div>
      </Field>
      <Field label="INCOME BUCKETS">
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {BUILDER_INCOME_OPTIONS.map((o) => {
            const on = incomes.includes(o.value);
            return <ToggleChip key={o.value} on={on} onClick={() => toggle(incomes, o.value, setIncomes)} color="var(--ink)" bg="var(--gold-soft)">{o.label}</ToggleChip>;
          })}
        </div>
      </Field>
      <Field label="NOTES"><textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} placeholder="Why this audience, intended sequence…" style={{ ...INPUT, resize: 'vertical' }} /></Field>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{size != null ? `last build: ${size.toLocaleString()} families` : 'the backend computes the audience size'}</span>
        <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'BUILDING…' : 'BUILD AUDIENCE'}</button>
      </div>
    </FormCard>
  );
}

// =============================== 5c · PIPELINE ==============================
function PipelineTab({ pipe }: { pipe: PipelineResponse }) {
  const badge = sourceBadge(pipe.source);
  const h = pipe.handoff;
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="DEALS IN PIPELINE" value={pipe.total.toLocaleString()} sub="across all stages" />
        <StatTile label="STUCK IN STAGE" value={String(pipe.stuck_total)} sub="idle beyond the stuck window" tone={pipe.stuck_total > 0 ? 'warn' : 'ok'} />
        <StatTile label="VELOCITY" value={`${pipe.velocity_pct}%`} sub="reached a handoff stage" />
        <StatTile label="HANDOFF THIS WEEK" value={String(h.weekly)} sub={`${h.monthly} this month · ${h.conversion_pct}% conv`} />
      </div>

      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 12, flexWrap: 'wrap', gap: 6 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Stage distribution &amp; stuck-in-stage alerts</div>
          <SourceBadge info={badge} />
        </div>
        {pipe.stages.map((s) => <StageBar key={s.stage} s={s} showStuck />)}
      </div>

      {/* handoff card */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Marketing → onboarding handoff</div>
          <Link href="/dashboard" style={{ fontFamily: MONO, fontSize: 9, color: 'var(--brand)', textDecoration: 'none' }}>feeds the KPI Scorecard →</Link>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          <MiniStat label="WEEKLY" value={String(h.weekly)} />
          <MiniStat label="MONTHLY" value={String(h.monthly)} />
          <MiniStat label="CUMULATIVE" value={h.cumulative.toLocaleString()} />
          <MiniStat label="CONVERSION" value={`${h.conversion_pct}%`} sub={`of ${h.total_deals.toLocaleString()} deals`} />
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Stage counts + handoff are LIVE HubSpot AGGREGATE reads (deal counts per stage, never a per-person field). Stuck = idle beyond the configured stuck-in-stage window.
      </div>
    </>
  );
}

// =============================== 5d · SEQUENCES =============================
function SequencesTab({ seqs, isLeader, notify }: { seqs: SequencesResponse } & Ctx) {
  const badge = sourceBadge(seqs.source);
  const [openId, setOpenId] = useState<string | null>(null);
  const types = useMemo(() => Array.from(new Set(seqs.sequences.map((s) => s.seq_type))), [seqs]);
  const [fType, setFType] = useState('');
  const shown = seqs.sequences.filter((s) => !fType || s.seq_type === fType);

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <SourceBadge info={badge} />
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>read-only · the Sales-Hub Sequences API is unavailable in this portal</span>
        </div>
        <select value={fType} onChange={(e) => setFType(e.target.value)} style={{ ...SELECT, width: 'auto', minWidth: 150 }}>
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{seqTypeLabel(t)}</option>)}
        </select>
      </div>

      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: SEQ_GRID, fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>SEQUENCE</div>
          <div>TYPE</div>
          <div style={{ textAlign: 'right' }}>AUDIENCE</div>
          <div style={{ textAlign: 'right' }}>STEPS</div>
          <div style={{ textAlign: 'center' }}>HEALTH</div>
        </div>
        {shown.map((q) => {
          const open = openId === q.sequence_id;
          return (
            <div key={q.sequence_id}>
              <div onClick={() => setOpenId(open ? null : q.sequence_id)} style={{ display: 'grid', gridTemplateColumns: SEQ_GRID, alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)', cursor: 'pointer', background: open ? 'var(--card-2)' : 'transparent' }}>
                <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: 'var(--ink-3)', fontSize: 9, width: 8 }}>{open ? '▾' : '▸'}</span>{q.name}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>{seqTypeLabel(q.seq_type)}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{q.audience_size.toLocaleString()}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)' }}>{q.step_count}</div>
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: q.health_flag ? 'var(--warn-soft)' : 'var(--ok-soft)', color: q.health_flag ? 'var(--warn)' : 'var(--ok)' }}>{q.health_flag ? 'WATCH' : 'OK'}</span>
                </div>
              </div>
              {open && (
                <div style={{ padding: '12px 16px 16px 32px', borderBottom: '1px solid var(--line)', background: 'var(--card-2)' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '.5fr 1fr 1fr 1fr', fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', fontWeight: 600, padding: '0 0 6px' }}>
                    <div>STEP</div><div style={{ textAlign: 'right' }}>OPEN %</div><div style={{ textAlign: 'right' }}>CLICK %</div><div style={{ textAlign: 'right' }}>CONV %</div>
                  </div>
                  {q.steps.map((st) => (
                    <div key={st.step} style={{ display: 'grid', gridTemplateColumns: '.5fr 1fr 1fr 1fr', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)', padding: '4px 0', borderTop: '1px solid var(--line)' }}>
                      <div>#{st.step}</div>
                      <div style={{ textAlign: 'right' }}>{st.open_pct}%</div>
                      <div style={{ textAlign: 'right' }}>{st.click_pct}%</div>
                      <div style={{ textAlign: 'right', color: 'var(--ink)' }}>{st.conversion_pct}%</div>
                    </div>
                  ))}
                  <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    {isLeader ? (
                      <>
                        <button onClick={() => notify(`Sequence "${q.name}" approval routed to HubSpot ops (leader control · demo affordance — no live send).`, 'ok')} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--ok)', background: 'var(--ok-soft)', color: 'var(--ok)', padding: '6px 12px' }}>✓ APPROVE</button>
                        <button onClick={() => notify(`Sequence "${q.name}" flagged to KILL — routed to HubSpot ops (leader control · demo affordance).`, 'ok')} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--signal)', background: 'var(--signal-soft)', color: 'var(--signal)', padding: '6px 12px' }}>✕ KILL</button>
                        <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>leader-only · execution stays in HubSpot</span>
                      </>
                    ) : (
                      <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ APPROVE / KILL — LEADER-ONLY</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {shown.length === 0 && <Empty>No sequences of this type.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Per-step open/click/conversion are a SYNTHETIC mirror (the portal has no Sequences API). Approve/kill are leader-only control affordances — the cockpit never sends; HubSpot runs every sequence.
      </div>
    </>
  );
}
const SEQ_GRID = '2fr 1fr .9fr .7fr .8fr';

// =============================== 5e · SMS INBOX ============================
const QUICK_REPLIES = [
  'Happy to walk you through tuition + ESA options — got 10 min today?',
  'Yes, GT School is a fully accredited program — here are the details.',
  'Let me grab a few times for a tour this week.',
  'Great — sending the enrollment + deposit link now.',
];

function SmsTab({ inbox, canEdit, notify, refetch }: { inbox: SmsResponse } & Ctx) {
  const badge = sourceBadge(inbox.source);
  const [filter, setFilter] = useState('');
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of inbox.threads) c[t.status] = (c[t.status] ?? 0) + 1;
    return c;
  }, [inbox]);
  const shown = inbox.threads.filter((t) => {
    if (!filter) return true;
    if (filter === 'no_reply') return t.status === 'no_reply' || !t.replied;
    return t.status === filter;
  });
  // honest tag-mode summary: are any threads LLM-tagged, or all keyword v1?
  const anyLlm = inbox.threads.some((t) => (t.tag_mode ?? '').toLowerCase() === 'llm');

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <SourceBadge info={badge} />
          <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: anyLlm ? 'var(--signal-soft)' : 'var(--accent-soft)', color: anyLlm ? 'var(--signal)' : 'var(--ink-3)' }}>
            THEME TAGGING · {anyLlm ? 'LLM auto-theme' : 'keyword rules v1'}
          </span>
        </div>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{shown.length} of {inbox.threads.length} threads</span>
      </div>

      {/* status filter chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        <FilterChip on={filter === ''} onClick={() => setFilter('')} label={`All ${inbox.threads.length}`} color="var(--ink-2)" bg="var(--accent-soft)" />
        {SMS_STATUS_FILTERS.map((s) => {
          const st = smsStatusStyle(s);
          const n = s === 'no_reply' ? inbox.threads.filter((t) => t.status === 'no_reply' || !t.replied).length : (counts[s] ?? 0);
          return <FilterChip key={s} on={filter === s} onClick={() => setFilter(s)} label={`${st.label} ${n}`} color={st.color} bg={st.bg} />;
        })}
      </div>

      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        {shown.map((t) => <SmsRow key={t.thread_id} t={t} canEdit={canEdit} notify={notify} refetch={refetch} />)}
        {shown.length === 0 && <Empty>No threads match this filter.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Auto-theme tags are derived from the message ({anyLlm ? 'LLM mode' : 'keyword rules v1 — LLM auto-theme deferred'}). {canEdit ? 'Flag → hot family enqueues a Decision-Queue item; objection → brief drafts a Content calendar entry.' : 'Flag / brief actions are owner-gated.'} Quick replies are UI snippets only — the cockpit never sends.
      </div>
    </>
  );
}

function SmsRow({ t, canEdit, notify, refetch }: { t: SmsThread; canEdit: boolean; notify: Notify; refetch: () => void }) {
  const { session } = useSession();
  const role = session.role;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const st = smsStatusStyle(t.status);

  const flagHot = async () => {
    setBusy(true);
    const res = await apiPost<DecisionResponse>(`/nurture/sms/${t.thread_id}/flag-hot-family`, role, {});
    setBusy(false);
    if (!res || !res.id) { notify('Could not flag — owner access required and the backbone must be up.', 'err'); return; }
    notify(`Flagged ${t.contact_label} to hot family → Decision Queue.`, 'ok', '/decision');
    refetch();
  };
  const objectionBrief = async () => {
    const theme = t.theme_tags[0] || 'general';
    setBusy(true);
    const res = await apiPost<ObjectionBriefResponse>('/nurture/sms/objection-brief', role, { theme, title: `Objection brief: ${themeLabel(theme)}` });
    setBusy(false);
    if (!res || !res.entry_id) { notify('Could not draft the brief — owner access required and the backbone must be up.', 'err'); return; }
    notify(`Drafted "${res.title}" (${res.channel}) → Content calendar.`, 'ok', '/content');
  };

  return (
    <div>
      <div onClick={() => setOpen((o) => !o)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 14px', borderBottom: '1px solid var(--line)', cursor: 'pointer', background: open ? 'var(--card-2)' : 'transparent' }}>
        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: st.bg, color: st.color, minWidth: 78, textAlign: 'center' }}>{st.label}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{t.contact_label}</div>
          <div style={{ fontSize: 10.5, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.last_message}</div>
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end', maxWidth: 200 }}>
          {t.theme_tags.length === 0 && <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>untagged</span>}
          {t.theme_tags.map((tag) => {
            const ts = themeTagStyle(tag);
            return <span key={tag} style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: ts.bg, color: ts.color }}>{themeLabel(tag)}</span>;
          })}
        </div>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', minWidth: 90, textAlign: 'right' }}>{fmtInbound(t.inbound_at)}</span>
      </div>
      {open && (
        <div style={{ padding: '12px 14px 14px', borderBottom: '1px solid var(--line)', background: 'var(--card-2)' }}>
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 6 }}>
            tagged via {tagModeLabel(t.tag_mode)} · {t.replied ? 'replied' : 'awaiting reply'}
          </div>
          {/* quick-reply template snippets (UI only) */}
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', fontWeight: 600, marginBottom: 5 }}>QUICK REPLY SNIPPETS (UI only — no live send)</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
            {QUICK_REPLIES.map((q, i) => (
              <span key={i} style={{ fontSize: 10, color: 'var(--ink-2)', padding: '4px 8px', border: '1px solid var(--line)', background: 'var(--card)', maxWidth: 280 }}>{q}</span>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            {canEdit ? (
              <>
                <button onClick={flagHot} disabled={busy} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--gold)', background: 'var(--gold-soft)', color: 'var(--gold)', padding: '6px 12px', opacity: busy ? 0.6 : 1 }}>⚑ FLAG → HOT FAMILY</button>
                <button onClick={objectionBrief} disabled={busy} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: busy ? 'default' : 'pointer', border: '1px solid var(--signal)', background: 'var(--signal-soft)', color: 'var(--signal)', padding: '6px 12px', opacity: busy ? 0.6 : 1 }}>✎ OBJECTION → CONTENT BRIEF</button>
                <Link href="/admissions" style={{ fontFamily: MONO, fontSize: 9, color: 'var(--brand)', textDecoration: 'none' }}>Admissions →</Link>
              </>
            ) : (
              <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ FLAG / BRIEF — OWNER-GATED</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// =============================== 5f · SLA TRACKER ==========================
function SlaTab({ sla, isLeader, notify }: { sla: SlaResponse } & Ctx) {
  const badge = sourceBadge(sla.source);
  const lateOnly = sla.late.filter((l) => !l.contacted);
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="APPLICANTS TODAY" value={String(sla.applicants_today)} sub="entered the queue today" />
        <div style={{ border: `1px solid ${sla.compliance_pct >= 90 ? 'var(--ok)' : 'var(--warn)'}`, background: sla.compliance_pct >= 90 ? 'var(--ok-soft)' : 'var(--warn-soft)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: sla.compliance_pct >= 90 ? 'var(--ok)' : 'var(--warn)', fontWeight: 600 }}>{sla.window_hours}-HR CONTACT SLA</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 28, lineHeight: 1.05, marginTop: 7, color: 'var(--ink)' }}>{sla.compliance_pct}%</div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>target 90% · {lateOnly.length} uncontacted past window</div>
        </div>
        <StatTile label="PENDING CONTACT" value={String(sla.pending)} sub="not yet contacted" tone={sla.pending > 0 ? 'warn' : 'ok'} />
        <StatTile label="30-DAY VOLUME" value={sla.history_30d_count.toLocaleString()} sub="applicants in the trailing 30 days" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 14, marginBottom: 14 }}>
        {/* late list — red */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Late list</div>
            <SourceBadge info={badge} />
          </div>
          {sla.late.map((l) => (
            <div key={l.applicant_label} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: l.contacted ? 'var(--warn)' : 'var(--signal)', flexShrink: 0 }} />
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink)' }}>{l.applicant_label}</span>
              <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>{l.owner}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 600, color: l.contacted ? 'var(--warn)' : 'var(--signal)' }}>{l.hours_waiting}h</span>
              {isLeader && <button onClick={() => notify(`Flagged ${l.applicant_label} for personal response (leader · demo affordance).`, 'ok')} style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink-2)', padding: '3px 7px' }}>flag</button>}
            </div>
          ))}
          {sla.late.length === 0 && <Empty>No late applicants — every contact is within the window.</Empty>}
        </div>

        {/* per-owner */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Per-owner attribution</div>
          {sla.per_owner.map((o) => (
            <div key={o.owner} style={{ padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <span style={{ fontSize: 11.5, color: 'var(--ink)', fontFamily: MONO }}>{o.owner}</span>
                <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: o.compliance_pct >= 90 ? 'var(--ok)' : 'var(--warn)' }}>{o.compliance_pct}%</span>
              </div>
              <div style={{ height: 5, background: 'var(--accent-soft)', marginTop: 5, position: 'relative' }}>
                <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, o.compliance_pct)}%`, background: o.compliance_pct >= 90 ? 'var(--ok)' : 'var(--warn)', opacity: 0.85 }} />
              </div>
              <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 3 }}>{o.in_window} of {o.total} in window</div>
            </div>
          ))}
        </div>
      </div>

      {/* 30-day history bar */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 8 }}>30-day applicant volume</div>
        <HistoryChart total={sla.history_30d_count} compliance={sla.compliance_pct} />
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        ⌖ The applicant denominator is the Supabase app_form source of truth; the SLA timer log is a Supabase mirror. Compliance is owner-attributable. {isLeader ? 'Leaders may flag a late applicant for a personal response.' : 'Flag-for-personal-response is leader-only.'}
      </div>
    </>
  );
}

// A lightweight 30-day synthetic distribution chart (volume context only).
function HistoryChart({ total, compliance }: { total: number; compliance: number }) {
  const bars = useMemo(() => {
    const out: number[] = [];
    const avg = Math.max(1, Math.round(total / 30));
    for (let i = 0; i < 30; i++) out.push(Math.max(0, Math.round(avg * (0.6 + ((i * 7) % 11) / 11))));
    return out;
  }, [total]);
  const max = Math.max(...bars, 1);
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 64 }}>
        {bars.map((b, i) => (
          <div key={i} title={`day ${i + 1}: ~${b}`} style={{ flex: 1, height: `${(100 * b) / max}%`, background: compliance >= 90 ? 'var(--ok)' : 'var(--gold)', opacity: 0.7 }} />
        ))}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 6 }}>~{Math.round(total / 30)}/day avg over {total} applicants · trailing 30 days</div>
    </>
  );
}

// ============================ shared bits ====================================
function Heatmap({ cells, dimension }: { cells: HeatmapCell[]; dimension: string }) {
  // pivot: rows = engagement tiers (fixed order), cols = distinct attribute values.
  const cols = useMemo(() => {
    const seen: string[] = [];
    for (const c of cells) if (!seen.includes(c.attribute_value)) seen.push(c.attribute_value);
    return seen;
  }, [cells]);
  const maxPct = useMemo(() => Math.max(1, ...cells.map((c) => c.conversion_pct)), [cells]);
  const lookup = (tier: string, col: string) => cells.find((c) => c.engagement_tier === tier && c.attribute_value === col);

  if (cells.length === 0) return <Empty>No heatmap data for this dimension.</Empty>;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `74px repeat(${cols.length}, 1fr)`, gap: 3 }}>
      <div />
      {cols.map((c) => (
        <div key={c} style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', textAlign: 'center', fontWeight: 600 }}>{attrValueLabel(dimension, c)}</div>
      ))}
      {ENGAGEMENT_TIER_ORDER.map((tier) => {
        const ts = engagementTierStyle(tier);
        return (
          <Fragment key={tier}>
            <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink)', display: 'flex', alignItems: 'center', fontWeight: 600 }}>{ts.label}</div>
            {cols.map((col) => {
              const cell = lookup(tier, col);
              const pct = cell?.conversion_pct ?? 0;
              const sty = heatCellStyle(pct, maxPct);
              return (
                <div key={`${tier}-${col}`} title={cell ? `${ts.label} × ${attrValueLabel(dimension, col)}: ${cell.converted}/${cell.total} = ${pct}%` : 'no data'} style={{ background: sty.bg, opacity: sty.opacity, color: sty.color, fontFamily: MONO, fontSize: 11, fontWeight: 600, textAlign: 'center', padding: '11px 0' }}>
                  {cell ? `${pct}%` : '—'}
                </div>
              );
            })}
          </Fragment>
        );
      })}
    </div>
  );
}

// minimal Fragment shim (avoids importing React.Fragment by name elsewhere).
function Fragment({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

function StageBar({ s, showStuck }: { s: { stage: string; count: number; pct: number; stuck: number }; showStuck?: boolean }) {
  const isHandoff = HANDOFF_STAGES.includes(s.stage);
  return (
    <div style={{ marginBottom: 9 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
        <span style={{ fontSize: 11, color: 'var(--ink)', fontWeight: isHandoff ? 600 : 500 }}>
          {stageLabel(s.stage)}{isHandoff && <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '1px 5px', marginLeft: 6, background: 'var(--ok-soft)', color: 'var(--ok)' }}>HANDOFF</span>}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>
          {s.count.toLocaleString()} · {s.pct}%{showStuck && s.stuck > 0 && <span style={{ color: 'var(--warn)' }}> · {s.stuck} stuck</span>}
        </span>
      </div>
      <div style={{ height: 8, background: 'var(--accent-soft)', position: 'relative' }}>
        <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, s.pct)}%`, background: isHandoff ? 'var(--ok)' : 'var(--gold)', opacity: 0.8 }} />
        {showStuck && s.stuck > 0 && s.count > 0 && (
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: 0, width: `${Math.min(100, (100 * s.stuck) / s.count)}%`, background: 'var(--warn)', opacity: 0.55 }} />
        )}
      </div>
    </div>
  );
}

function ReachBar({ pct }: { pct: number }) {
  return (
    <div style={{ height: 5, background: 'var(--accent-soft)', marginTop: 6, position: 'relative' }}>
      <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, pct)}%`, background: pct >= 60 ? 'var(--ok)' : pct >= 30 ? 'var(--gold)' : 'var(--ink-3)', opacity: 0.8 }} />
    </div>
  );
}

function CrossLinks() {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.7px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>CROSS-MODULE LINKS</div>
      <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 7 }}>
        {[
          <>Hot families (SMS) escalate to the <Link href="/decision" style={LINK}>Decision Queue</Link> + <Link href="/admissions" style={LINK}>Admissions</Link>.</>,
          <>Recurring objections draft a brief into <Link href="/content" style={LINK}>Content</Link> (objection → content loop).</>,
          <>Conversion attribution feeds <Link href="/content" style={LINK}>Content Performance</Link>.</>,
          <>Pipeline + handoff feed the <Link href="/dashboard" style={LINK}>KPI Scorecard</Link>.</>,
        ].map((l, i) => (
          <li key={i} style={{ fontSize: 11.5, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
            <span style={{ color: 'var(--gold)' }}>→</span> <span>{l}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatTile({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: 'ok' | 'warn' }) {
  const color = tone === 'ok' ? 'var(--ok)' : tone === 'warn' ? 'var(--warn)' : 'var(--ink)';
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>{label}</div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 28, lineHeight: 1.05, marginTop: 7, color }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{sub}</div>
    </div>
  );
}

function MiniStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--card-2)', padding: 11 }}>
      <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 22, color: 'var(--ink)', marginTop: 4, lineHeight: 1 }}>{value}</div>
      {sub && <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

function SourceBadge({ info }: { info: { label: string; tone: 'live' | 'synthetic' | 'truth' | 'neutral' } }) {
  const s = badgeStyle(info.tone);
  return <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, letterSpacing: '.3px', padding: '2px 8px', background: s.bg, color: s.color }}>⌖ {info.label}</span>;
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', color: live ? 'var(--ok)' : 'var(--ink-3)', background: live ? 'var(--ok-soft)' : 'var(--accent-soft)' }}>
      {live ? '● LIVE' : '○ SAMPLE · offline seed'}
    </span>
  );
}

function FilterChip({ on, onClick, label, color, bg }: { on: boolean; onClick: () => void; label: string; color: string; bg: string }) {
  return (
    <button onClick={onClick} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', padding: '4px 10px', border: `1px solid ${on ? 'var(--ink)' : 'var(--line-2)'}`, background: on ? bg : 'var(--card)', color: on ? color : 'var(--ink-3)' }}>{label}</button>
  );
}

function ToggleChip({ on, onClick, color, bg, children }: { on: boolean; onClick: () => void; color: string; bg: string; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 600, cursor: 'pointer', padding: '5px 11px', border: `1px solid ${on ? color : 'var(--line-2)'}`, background: on ? bg : 'var(--card)', color: on ? color : 'var(--ink-3)' }}>{on ? '✓ ' : ''}{children}</button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: '28px 16px', textAlign: 'center', fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>{children}</div>;
}

function FormCard({ title, tag, children }: { title: string; tag?: string; children: React.ReactNode }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, letterSpacing: '.3px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <span>{title}</span>
        {tag && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 400, opacity: 0.85, whiteSpace: 'nowrap' }}>{tag}</span>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '14px 16px' }}>{children}</div>
    </div>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>{children}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '12px 22px 0', padding: '10px 14px', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}` }}>
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: ok ? 'var(--ok)' : 'var(--signal)' }}>{ok ? '✓ DONE' : '⚠ ERROR'}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{toast.msg}</span>
      {ok && toast.href && <Link href={toast.href} style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color: 'var(--ok)' }}>open →</Link>}
      <button onClick={onClose} aria-label="Dismiss" style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>✕</button>
    </div>
  );
}

const LINK: React.CSSProperties = { color: 'var(--ink)', fontWeight: 600 };
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
