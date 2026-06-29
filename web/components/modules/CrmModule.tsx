'use client';

// CRM / Marketing Operations (Module 7) — the data backbone surfaced as product,
// wired end-to-end to the FastAPI backbone. Five controlled sub-views (TabBar):
//   7a Overview        — GET /crm/ops/overview: sync-parity score, the PERMANENT-RED
//                        UTM attribution flag, the LIVE HubSpot lead-score mini-histogram,
//                        open data-quality count, per-connector last-sync, field flags.
//   7b Source tracking — GET /crm/ops/source-tracking: per-UTM-param resolved % bars, the
//                        attribution chain (form → Supabase → HubSpot), the broken-UTM
//                        drill-in, and the UTM fix log.
//   7c Lead scoring    — GET /crm/ops/lead-scoring: READ-ONLY LIVE HubSpot histogram +
//                        cold/warm/hot tiers, the DERIVED (honestly labeled, NOT live)
//                        score→conversion table, the scoring model + threshold, change log.
//   7d Sync parity     — GET /crm/ops/sync-parity: overall + field-level parity, the
//                        known-unreliable field flags, drift alerts, and the ALWAYS-ON
//                        rule-of-truth reminder from the API.
//   7e Data quality    — GET /crm/ops/data-quality + scan/file/triage: open issues, the
//                        resolution log, a "run auto-detect scan" action, an owner-gated
//                        file-issue form, and leader-only acknowledge/prioritize/resolve.
// Every read falls back to a per-resource seed (lib/crm-api) so the screen never blanks;
// the LIVE/SAMPLE pill is honest, and every surface renders its provenance badge from the
// backend `source` string — LIVE HUBSPOT · AGGREGATE / DERIVED / SUPABASE ⇄ HUBSPOT /
// SYNTHETIC. UTM attribution is shown as a permanent red flag: broken until rebuilt.

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { canEditWorkstream, moduleById, type Role } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet, apiPost, apiPatch } from '@/lib/api';
import {
  type OverviewResponse,
  type SourceTrackingResponse,
  type LeadScoringResponse,
  type SyncParityResponse,
  type DataQualityResponse,
  type LeadScoreDistribution,
  type FieldFlag,
  type FixLogEntry,
  type Issue,
  type ScanResult,
  type FileIssueRequest,
  type UtmEntity,
  type UtmRepairManual,
  type SourceBadgeInfo,
  type BadgeTone,
  repairUtm,
  SEED_OVERVIEW,
  SEED_SOURCE_TRACKING,
  SEED_LEAD_SCORING,
  SEED_SYNC_PARITY,
  SEED_DATA_QUALITY,
  sourceBadge,
  badgeStyle,
  severityStyle,
  statusStyle,
  priorityStyle,
  categoryLabel,
  fieldLabel,
  connectorLabel,
  fmtPct,
  fmtDate,
  fmtAge,
  CATEGORIES,
  SEVERITIES,
  PRIORITIES,
  FILE_ISSUE_KINDS,
} from '@/lib/crm-api';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

interface Toast { msg: string; kind: 'ok' | 'err'; href?: string; }
type Notify = (m: string, k: 'ok' | 'err', href?: string) => void;
type Ctx = { role: Role; canEdit: boolean; isLeader: boolean; refetch: () => void; notify: Notify };

// ============================ the module =====================================
export function CrmModule() {
  const { session } = useSession();
  const def = moduleById('crm')!;
  const canEdit = canEditWorkstream(session, 'crm'); // file a manual issue — owner/admin
  const isLeader = session.role === 'leader' || session.role === 'admin'; // triage / scoring
  const role = session.role;

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [tracking, setTracking] = useState<SourceTrackingResponse | null>(null);
  const [scoring, setScoring] = useState<LeadScoringResponse | null>(null);
  const [parity, setParity] = useState<SyncParityResponse | null>(null);
  const [dq, setDq] = useState<DataQualityResponse | null>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(() => {
    apiGet<OverviewResponse>('/crm/ops/overview', role).then((d) => {
      if (d && d.lead_score_distribution) { setOverview(d); setLive(true); }
      else { setOverview(SEED_OVERVIEW); setLive(false); }
    });
    apiGet<SourceTrackingResponse>('/crm/ops/source-tracking', role).then((d) => setTracking(d && Array.isArray(d.params) ? d : SEED_SOURCE_TRACKING));
    apiGet<LeadScoringResponse>('/crm/ops/lead-scoring', role).then((d) => setScoring(d && d.distribution ? d : SEED_LEAD_SCORING));
    apiGet<SyncParityResponse>('/crm/ops/sync-parity', role).then((d) => setParity(d && d.parity_by_field ? d : SEED_SYNC_PARITY));
    apiGet<DataQualityResponse>('/crm/ops/data-quality', role).then((d) => setDq(d && Array.isArray(d.open_issues) ? d : SEED_DATA_QUALITY));
  }, [role]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback<Notify>((msg, kind, href) => setToast({ msg, kind, href }), []);

  const ov = overview ?? SEED_OVERVIEW;
  const trk = tracking ?? SEED_SOURCE_TRACKING;
  const sco = scoring ?? SEED_LEAD_SCORING;
  const par = parity ?? SEED_SYNC_PARITY;
  const dqData = dq ?? SEED_DATA_QUALITY;
  const ctx: Ctx = { role, canEdit, isLeader, refetch: load, notify };

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <Header idx={def.idx} title={def.title} owner={def.owner} canEdit={canEdit} live={live} />

        {tab === 0 && <OverviewTab ov={ov} />}
        {tab === 1 && <SourceTrackingTab trk={trk} ctx={ctx} />}
        {tab === 2 && <LeadScoringTab sco={sco} ctx={ctx} />}
        {tab === 3 && <SyncParityTab par={par} />}
        {tab === 4 && <DataQualityTab dq={dqData} {...ctx} />}

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          ⌖ {def.source} · read-only window onto the Phase-1 sync backbone — the Hub owns the parity dials + the data-quality queue, never the field values. UTM attribution was broken end-to-end; the flag is now driven by the live broken count + an owner-triggered repair — we never fake green.
        </div>
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

// =============================== 7a · OVERVIEW ===============================
function OverviewTab({ ov }: { ov: OverviewResponse }) {
  const parityPct = fmtPct(ov.parity_overall);
  const parityTone = ov.data_confidence_banner ? 'var(--signal)' : 'var(--warn)';
  const distBadge = sourceBadge(ov.lead_score_distribution.source);
  const dist = ov.lead_score_distribution;
  // Data-driven UTM health — RED only while the live broken count is non-zero.
  const utmBroken = ov.utm_status === 'broken';
  const utmColor = utmBroken ? 'var(--signal)' : 'var(--ok)';
  const utmTotal = ov.utm_ok + ov.utm_broken;

  return (
    <>
      {/* top stat row — parity, data-driven UTM health, open DQ, lead total */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>SYNC PARITY · SUPABASE ⇄ HUBSPOT</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 28, lineHeight: 1.05, marginTop: 7, color: parityTone }}>{parityPct}</div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{ov.data_confidence_banner ? 'below floor · confidence banner active' : 'above floor · field drift tracked'}</div>
        </div>

        {/* UTM — data-driven: red while any UTM is broken, green once resolved */}
        <div style={{ border: `2px solid ${utmColor}`, background: utmBroken ? 'var(--signal-soft)' : 'var(--ok-soft)', padding: 14 }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: utmColor, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: utmColor, animation: utmBroken ? 'blink 1.6s infinite' : 'none' }} />
            UTM ATTRIBUTION HEALTH
          </div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 28, lineHeight: 1.05, marginTop: 7, color: utmColor }}>{utmBroken ? 'BROKEN' : 'HEALTHY'}</div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>
            {utmBroken
              ? `${ov.utm_broken} of ${utmTotal} leads broken · fixable ones repair losslessly, the rest need a manual decision`
              : `all ${utmTotal} leads resolved`}
          </div>
        </div>

        <StatTile label="OPEN DATA-QUALITY ISSUES" value={String(ov.open_dq_count)} sub="auto-detected + filed · see queue" tone={ov.open_dq_count > 0 ? 'warn' : 'ok'} />
        <StatTile label="LEAD-SCORE POPULATION" value={dist.total.toLocaleString()} sub={`mean ${dist.mean} · qualifies ≥ ${dist.threshold}`} />
      </div>

      {/* lead-score mini-histogram (LIVE HubSpot) + field flags */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 14, marginBottom: 14 }}>
        <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Lead-score distribution</div>
            <SourceBadge info={distBadge} />
          </div>
          <Histogram dist={dist} />
        </div>

        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
            ⚑ FIELD RELIABILITY FLAGS
          </div>
          <FieldFlagList flags={ov.field_flags} />
        </div>
      </div>

      {/* per-connector last sync (live per-source) */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>CONNECTORS · LAST SYNC</span>
          <SourceBadge info={sourceBadge(ov.last_sync[0]?.source)} />
        </div>
        {ov.last_sync.map((c) => (
          <div key={c.connector} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
            <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--ok)', flexShrink: 0 }} />
            <span style={{ flex: 1, fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>{connectorLabel(c.connector)}</span>
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)' }}>{fmtDate(c.last_sync)} · {fmtAge(c.last_sync)} ago</span>
          </div>
        ))}
        {ov.last_sync.length === 0 && <Empty>No connector watermarks.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Last-sync stamps are REAL per source: the HubSpot connectors read the newest <code>lastmodifieddate</code>/<code>hs_lastmodifieddate</code> off the live portal; Supabase reads the latest <code>crm_synced_at</code> (each row carries its own source badge, synthetic only if a live read returns nothing). The lead-score histogram is a LIVE HubSpot aggregate (one CRM-Search COUNT per band) — never a per-contact read.
      </div>
    </>
  );
}

// =============================== 7b · SOURCE TRACKING =======================
function SourceTrackingTab({ trk, ctx }: { trk: SourceTrackingResponse; ctx: Ctx }) {
  const badge = sourceBadge(trk.source);
  const [openId, setOpenId] = useState<string | null>(null);
  const [repairing, setRepairing] = useState(false);
  // Manual list from the LAST repair (the unfixable ones a human must decide on).
  const [manual, setManual] = useState<UtmRepairManual[] | null>(null);

  const canRepair = ctx.canEdit || ctx.isLeader; // mirrors the other CRM-Ops write gates
  const utmBroken = trk.utm_status === 'broken';
  const utmColor = utmBroken ? 'var(--signal)' : 'var(--ok)';
  const total = trk.params[0]?.total ?? trk.broken_utm.length;

  const runRepair = async () => {
    setRepairing(true);
    const res = await repairUtm(ctx.role);
    setRepairing(false);
    if (!res) { ctx.notify('Could not repair — owner (leader/admin or the crm owner) access is required and the backbone must be up.', 'err'); return; }
    setManual(res.manual);
    ctx.notify(`Repaired ${res.repaired_count} · ${res.manual.length} need a manual decision`, 'ok');
    ctx.refetch();
  };

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <SourceBadge info={badge} />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{trk.broken_utm.length} broken · {trk.fix_log.length} fixes logged</span>
          {canRepair ? (
            <button onClick={runRepair} disabled={repairing} style={{ ...PRIMARY_BTN, opacity: repairing ? 0.6 : 1, cursor: repairing ? 'default' : 'pointer' }}>{repairing ? 'REPAIRING…' : '↻ REPAIR UTMs'}</button>
          ) : (
            <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '6px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ REPAIR UTMs — OWNER-GATED</span>
          )}
        </div>
      </div>

      {/* data-driven UTM banner — red while broken, green once resolved */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', background: utmBroken ? 'var(--signal-soft)' : 'var(--ok-soft)', border: `2px solid ${utmColor}`, marginBottom: 14 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.5px', padding: '2px 7px', background: utmColor, color: 'var(--on-signal)', whiteSpace: 'nowrap' }}>{utmBroken ? '⚠ UTM BROKEN' : '✓ UTM HEALTHY'}</span>
        <span style={{ fontSize: 12, color: 'var(--ink)', flex: 1 }}>
          {utmBroken ? (
            <>
              UTM attribution was broken end-to-end upstream — <b>{trk.broken_utm.length} of {total} leads broken</b>. The fixable ones repair losslessly via the action above; the rest (e.g. a missing utm_campaign) need a manual decision. The resolution % below is honest about what currently resolves.
            </>
          ) : (
            <>UTM attribution is <b>resolved</b> — all {total} leads resolved. The flag is driven by the live broken count, not a hard-coded state.</>
          )}
        </span>
      </div>

      {/* needs manual decision — the unfixable ones from the last repair */}
      {manual && manual.length > 0 && (
        <div style={{ border: '1px solid var(--warn)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--warn)', fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--warn)', fontWeight: 600 }}>
            ⚑ NEEDS MANUAL DECISION · {manual.length} unrepairable
          </div>
          {manual.map((m) => (
            <div key={m.entity_ref} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink)', fontWeight: 600, whiteSpace: 'nowrap' }}>{m.entity_ref}</span>
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }}>
                {m.reasons.map((r, i) => (
                  <li key={i} style={{ fontSize: 11, color: 'var(--ink-2)', display: 'flex', gap: 7 }}><span style={{ color: 'var(--warn)' }}>•</span> {r}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {manual && manual.length === 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', background: 'var(--ok-soft)', border: '1px solid var(--ok)', marginBottom: 14 }}>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: 'var(--ok)' }}>✓ NO MANUAL DECISIONS</span>
          <span style={{ fontSize: 12, color: 'var(--ink)', flex: 1 }}>Every broken UTM repaired losslessly — nothing left to decide by hand.</span>
        </div>
      )}

      {/* per-param resolution bars */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 10 }}>Per-UTM-param resolution</div>
        {trk.params.map((p) => (
          <div key={p.param} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink)', fontWeight: 600 }}>{p.param}</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{p.resolved}/{p.total} · {p.resolved_pct}%</span>
            </div>
            <div style={{ height: 8, background: 'var(--accent-soft)', position: 'relative' }}>
              <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, p.resolved_pct)}%`, background: p.resolved_pct >= 80 ? 'var(--ok)' : p.resolved_pct >= 50 ? 'var(--gold)' : 'var(--signal)', opacity: 0.85 }} />
            </div>
          </div>
        ))}
      </div>

      {/* attribution chain */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)', marginBottom: 4 }}>Attribution chain</div>
        <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginBottom: 12 }}>Form → Supabase (source of truth) → HubSpot (mirror). Per-step status — the chain is intact even while the UTM payload it carries is broken.</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap' }}>
          {trk.attribution_chain.map((s, i) => {
            const ok = s.status === 'ok';
            return (
              <div key={s.step} style={{ display: 'flex', alignItems: 'center' }}>
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, minWidth: 130 }}>
                  <span style={{ width: 28, height: 28, borderRadius: '50%', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', color: ok ? 'var(--ok)' : 'var(--signal)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 11, fontWeight: 600, border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}` }}>{ok ? '✓' : '!'}</span>
                  <span style={{ fontSize: 11, color: 'var(--ink)', fontWeight: 600, textAlign: 'center' }}>{fieldLabel(s.label)}</span>
                  <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '1px 6px', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', color: ok ? 'var(--ok)' : 'var(--signal)' }}>{s.status.toUpperCase()}</span>
                </div>
                {i < trk.attribution_chain.length - 1 && <span style={{ color: 'var(--ink-3)', fontSize: 16, margin: '0 2px', alignSelf: 'flex-start', marginTop: 6 }}>→</span>}
              </div>
            );
          })}
        </div>
      </div>

      {/* broken-UTM drill-in */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Broken-UTM drill-in</div>
        {trk.broken_utm.map((e) => {
          const open = openId === e.entity_id;
          return (
            <div key={e.entity_id}>
              <div onClick={() => setOpenId(open ? null : e.entity_id)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px', borderBottom: '1px solid var(--line)', cursor: 'pointer', background: open ? 'var(--card-2)' : 'transparent' }}>
                <span style={{ color: 'var(--ink-3)', fontSize: 9, width: 8 }}>{open ? '▾' : '▸'}</span>
                <span style={{ flex: 1, fontFamily: MONO, fontSize: 11, color: 'var(--ink)', fontWeight: 600 }}>{e.entity_id}</span>
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  {e.offending_keys.map((k) => (
                    <span key={k} style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: 'var(--signal-soft)', color: 'var(--signal)' }}>{k}</span>
                  ))}
                </div>
              </div>
              {open && (
                <div style={{ padding: '10px 16px 12px 34px', borderBottom: '1px solid var(--line)', background: 'var(--card-2)' }}>
                  <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {e.reasons.map((r, i) => (
                      <li key={i} style={{ fontSize: 11, color: 'var(--ink-2)', display: 'flex', gap: 7 }}><span style={{ color: 'var(--signal)' }}>•</span> {r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          );
        })}
        {trk.broken_utm.length === 0 && <Empty>No broken UTM in the cohort.</Empty>}
      </div>

      {/* UTM fix log */}
      <FixLog title="UTM fix log" entries={trk.fix_log} emptyMsg="No UTM fixes logged yet." />
    </>
  );
}

// =============================== 7c · LEAD SCORING ==========================
function LeadScoringTab({ sco, ctx }: { sco: LeadScoringResponse; ctx: Ctx }) {
  const distBadge = sourceBadge(sco.distribution.source);
  const corrBadge = sourceBadge(sco.correlation_source);
  const dist = sco.distribution;
  const maxCorr = Math.max(1, ...sco.correlation.map((c) => c.conversion_pct));

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <SourceBadge info={distBadge} />
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>read-only · the cockpit never edits HubSpot gt_lead_score</span>
        </div>
      </div>

      {/* tier breakdown */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="COLD" value={dist.tiers.cold.toLocaleString()} sub={`score < ${40}`} />
        <StatTile label="WARM" value={dist.tiers.warm.toLocaleString()} sub={`40–79`} tone="warn" />
        <StatTile label="HOT" value={dist.tiers.hot.toLocaleString()} sub={`≥ 80`} tone="ok" />
        <StatTile label="MEAN SCORE" value={String(dist.mean)} sub={`qualifies ≥ ${dist.threshold} · ${dist.total.toLocaleString()} leads`} />
      </div>

      {/* histogram */}
      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Score distribution histogram</div>
          <SourceBadge info={distBadge} />
        </div>
        <Histogram dist={dist} tall />
      </div>

      {/* score → conversion correlation — DERIVED, honestly labeled */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Score → conversion correlation</div>
          <SourceBadge info={corrBadge} />
        </div>
        <div style={{ fontSize: 9.5, color: 'var(--ink-3)', padding: '8px 16px 0' }}>
          NOT a live join. This table is <b>derived</b> deterministically from the band edges (a true per-contact → deal-stage join is not an aggregate read) — it is labeled honestly and never claimed live.
        </div>
        <div style={{ padding: '10px 16px 14px' }}>
          {sco.correlation.map((c) => (
            <div key={c.band} style={{ display: 'grid', gridTemplateColumns: '90px 1fr 56px', alignItems: 'center', gap: 10, padding: '5px 0' }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--ink)' }}>{c.band}</span>
              <div style={{ height: 8, background: 'var(--accent-soft)', position: 'relative' }}>
                <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, (100 * c.conversion_pct) / maxCorr)}%`, background: 'var(--ink-3)', opacity: 0.55 }} />
              </div>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: 'var(--ink-2)', textAlign: 'right' }}>{c.conversion_pct}%</span>
            </div>
          ))}
        </div>
      </div>

      {/* model + threshold */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Scoring model</div>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, padding: '3px 9px', background: 'var(--gold-soft)', color: 'var(--gold)' }}>THRESHOLD · {sco.threshold}</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-2)', lineHeight: 1.5 }}>{sco.model_description}</div>
      </div>

      {/* change log */}
      <FixLog title="Scoring-model change log" entries={sco.change_log} emptyMsg="No scoring-model changes logged." />

      {/* leadership input — approve/propose a scoring-model change (spec 7c/leadership) */}
      {ctx.isLeader ? (
        <ScoringChangeForm role={ctx.role} notify={ctx.notify} refetch={ctx.refetch} />
      ) : (
        <div style={{ marginTop: 12, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          ◌ Proposing a scoring-model change is leader-only (it opens a Decision-Queue item + a change-log entry).
        </div>
      )}
    </>
  );
}

// =============================== 7d · SYNC PARITY ==========================
function SyncParityTab({ par }: { par: SyncParityResponse }) {
  const badge = sourceBadge(par.source);
  const fields = Object.entries(par.parity_by_field);
  const unreliableNames = new Set(par.field_flags.filter((f) => f.status === 'unreliable').map((f) => f.field));
  const driftFields = new Set(par.drift_alerts.map((d) => d.field));
  const floor = par.drift_alerts[0]?.floor ?? 0.9;

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <SourceBadge info={badge} />
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{par.drift_alerts.length} drift alert{par.drift_alerts.length === 1 ? '' : 's'} · drift floor {fmtPct(floor)}</span>
      </div>

      {/* ALWAYS-ON rule of truth */}
      <div style={{ border: '1px solid var(--gold)', background: 'var(--gold-soft)', padding: 14, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
          <span style={{ fontSize: 12 }}>📌</span>
          <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--gold)', fontWeight: 600 }}>RULE OF TRUTH · ALWAYS ON</span>
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink)', lineHeight: 1.5, fontWeight: 500 }}>{par.rule_of_truth}</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 14 }}>
        <StatTile label="OVERALL PARITY" value={fmtPct(par.parity_overall)} sub="row-level synced / total" tone={par.parity_overall >= 0.95 ? 'ok' : 'warn'} />
        <StatTile label="FIELDS TRACKED" value={String(fields.length)} sub="DB ⇄ mirror per-field agreement" />
        <StatTile label="DRIFT ALERTS" value={String(par.drift_alerts.length)} sub={`fields below ${fmtPct(floor)} floor`} tone={par.drift_alerts.length > 0 ? 'warn' : 'ok'} />
      </div>

      {/* field-level parity table */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1.2fr', fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>FIELD</div>
          <div style={{ textAlign: 'right' }}>PARITY</div>
          <div style={{ textAlign: 'right' }}>FLAGS</div>
        </div>
        {fields.map(([name, value]) => {
          const drift = driftFields.has(name);
          const unreliable = unreliableNames.has(name);
          return (
            <div key={name} style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 1.2fr', alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500 }}>{fieldLabel(name)}</span>
              <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, textAlign: 'right', color: drift ? 'var(--signal)' : value >= 0.95 ? 'var(--ok)' : 'var(--warn)' }}>{fmtPct(value)}</span>
              <div style={{ display: 'flex', gap: 4, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                {drift && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: 'var(--signal-soft)', color: 'var(--signal)' }}>DRIFT</span>}
                {unreliable && <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', background: 'var(--warn-soft)', color: 'var(--warn)' }}>UNRELIABLE</span>}
                {!drift && !unreliable && <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>—</span>}
              </div>
            </div>
          );
        })}
        {fields.length === 0 && <Empty>No tracked fields.</Empty>}
      </div>

      {/* unreliable-field flags + drift alerts */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>⚑ KNOWN-UNRELIABLE FIELDS</div>
          <FieldFlagList flags={par.field_flags} />
        </div>
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>⚠ DRIFT ALERTS</div>
          {par.drift_alerts.map((d) => (
            <div key={d.field} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 14px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontSize: 11.5, color: 'var(--ink)' }}>{fieldLabel(d.field)}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, fontWeight: 600, color: 'var(--signal)' }}>{fmtPct(d.parity)} <span style={{ color: 'var(--ink-3)', fontWeight: 400 }}>&lt; {fmtPct(d.floor)}</span></span>
            </div>
          ))}
          {par.drift_alerts.length === 0 && <Empty>No fields below the drift floor.</Empty>}
        </div>
      </div>
    </>
  );
}

// =============================== 7e · DATA QUALITY QUEUE ====================
function DataQualityTab({ dq, role, canEdit, isLeader, notify, refetch }: { dq: DataQualityResponse } & Ctx) {
  const [scanning, setScanning] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [catFilter, setCatFilter] = useState('');

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const i of dq.open_issues) c[i.category] = (c[i.category] ?? 0) + 1;
    return c;
  }, [dq]);

  const shown = dq.open_issues.filter((i) => !catFilter || i.category === catFilter);

  const runScan = async () => {
    setScanning(true);
    const res = await apiPost<ScanResult>('/crm/ops/scan', role, {});
    setScanning(false);
    if (!res) { notify('Could not run the scan — the backbone must be up.', 'err'); return; }
    notify(`Auto-detect scan complete — scanned ${res.scanned}, detected ${res.detected}, ${res.open_dq_count} open.`, 'ok');
    refetch();
  };

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{dq.open_issues.length} open · {dq.resolution_log.length} resolved</span>
          {!isLeader && <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ TRIAGE — LEADER-ONLY</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button onClick={runScan} disabled={scanning} style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 14px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', cursor: scanning ? 'default' : 'pointer', opacity: scanning ? 0.6 : 1, borderRadius: 2 }}>{scanning ? 'SCANNING…' : '↻ RUN AUTO-DETECT SCAN'}</button>
          {canEdit ? (
            <button onClick={() => setShowForm((s) => !s)} style={{ ...PRIMARY_BTN, cursor: 'pointer' }}>{showForm ? '✕ CLOSE' : '+ FILE ISSUE'}</button>
          ) : (
            <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '6px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ FILE ISSUE — OWNER-GATED</span>
          )}
        </div>
      </div>

      {canEdit && showForm && (
        <div style={{ marginBottom: 14 }}>
          <FileIssueForm role={role} notify={notify} refetch={() => { refetch(); setShowForm(false); }} />
        </div>
      )}

      {/* category filter chips */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
        <FilterChip on={catFilter === ''} onClick={() => setCatFilter('')} label={`All ${dq.open_issues.length}`} color="var(--ink-2)" bg="var(--accent-soft)" />
        {CATEGORIES.map((c) => (
          <FilterChip key={c} on={catFilter === c} onClick={() => setCatFilter(c)} label={`${categoryLabel(c)} ${counts[c] ?? 0}`} color="var(--ink-2)" bg="var(--accent-soft)" />
        ))}
      </div>

      {/* open issues */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>OPEN ISSUES</div>
        {shown.map((i) => <IssueRow key={i.issue_id} issue={i} role={role} isLeader={isLeader} notify={notify} refetch={refetch} />)}
        {shown.length === 0 && <Empty>No open issues in this category.</Empty>}
      </div>

      {/* resolution log */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>RESOLUTION LOG · CLOSED ISSUES</div>
        {dq.resolution_log.map((i) => (
          <div key={i.issue_id} style={{ padding: '11px 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '2px 7px', background: 'var(--ok-soft)', color: 'var(--ok)' }}>RESOLVED</span>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{categoryLabel(i.category)}</span>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink)' }}>{i.description}</span>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>by {i.resolved_by || '—'} · {fmtDate(i.resolved_at)}</span>
            </div>
            {i.resolution && <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 5, paddingLeft: 2, lineHeight: 1.4 }}>↳ {i.resolution}</div>}
          </div>
        ))}
        {dq.resolution_log.length === 0 && <Empty>No resolved issues yet.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        The scan auto-detects sync-drift + UTM breakage and UPSERTS queue items (dedups on a deterministic signature — never duplicates). {canEdit ? 'You may file a manual issue (owner stamped server-side).' : 'Filing a manual issue is owner-gated.'} {isLeader ? 'Acknowledge / prioritize / resolve are yours.' : 'Triage (acknowledge / prioritize / resolve) is leader-only.'}
      </div>
    </>
  );
}

function IssueRow({ issue, role, isLeader, notify, refetch }: { issue: Issue; role: Role; isLeader: boolean; notify: Notify; refetch: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const sev = severityStyle(issue.severity);
  const st = statusStyle(issue.status);
  const pr = priorityStyle(issue.priority);
  const srcBadge = sourceBadge(issue.source);

  const patch = async (body: Record<string, string>, okMsg: string) => {
    setBusy(true);
    const res = await apiPatch<Issue>(`/crm/ops/data-quality/${issue.issue_id}`, role, body);
    setBusy(false);
    if (!res || !res.issue_id) { notify('Could not update — leader/admin access required and the backbone must be up.', 'err'); return; }
    notify(okMsg, 'ok');
    refetch();
  };

  return (
    <div>
      <div onClick={() => setOpen((o) => !o)} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 12, alignItems: 'center', padding: '11px 16px', borderBottom: '1px solid var(--line)', cursor: 'pointer', background: open ? 'var(--card-2)' : 'transparent' }}>
        <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: sev.color, flexShrink: 0 }} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, color: 'var(--ink)', lineHeight: 1.4 }}>{issue.description}</div>
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>
            {srcBadge.label} · {categoryLabel(issue.category)} · ⌖ {issue.owner}{issue.entity_ref ? ` · ${issue.entity_ref}` : ''} · {fmtAge(issue.created_at)} old
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {issue.priority === 'urgent' && <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 8px', background: pr.bg, color: pr.color }}>URGENT</span>}
          <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 8px', background: sev.bg, color: sev.color, whiteSpace: 'nowrap' }}>{sev.label}</span>
          <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 8px', background: st.bg, color: st.color, whiteSpace: 'nowrap' }}>{st.label}</span>
        </div>
      </div>
      {open && (
        <div style={{ padding: '12px 16px 14px', borderBottom: '1px solid var(--line)', background: 'var(--card-2)' }}>
          <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 10 }}>
            kind: {issue.kind} · signature: {issue.signature} · filed {fmtDate(issue.created_at)}
          </div>
          {isLeader ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <button onClick={() => patch({ status: 'acknowledged' }, `Acknowledged "${issue.description.slice(0, 40)}…"`)} disabled={busy || issue.status === 'acknowledged'} style={{ ...TRIAGE_BTN('var(--warn)'), opacity: busy || issue.status === 'acknowledged' ? 0.5 : 1 }}>◐ ACKNOWLEDGE</button>
              <button onClick={() => patch({ priority: issue.priority === 'urgent' ? 'normal' : 'urgent' }, `Priority set to ${issue.priority === 'urgent' ? 'normal' : 'urgent'}.`)} disabled={busy} style={{ ...TRIAGE_BTN('var(--gold)'), opacity: busy ? 0.5 : 1 }}>⚑ {issue.priority === 'urgent' ? 'DOWNGRADE' : 'PRIORITIZE'}</button>
              <button onClick={() => patch({ status: 'resolved', resolution: 'Resolved from the CRM-Ops data-quality queue.' }, `Resolved "${issue.description.slice(0, 40)}…"`)} disabled={busy || issue.status === 'resolved'} style={{ ...TRIAGE_BTN('var(--ok)'), opacity: busy || issue.status === 'resolved' ? 0.5 : 1 }}>✓ RESOLVE</button>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>leader/admin · resolved_by stamped server-side</span>
            </div>
          ) : (
            <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ ACKNOWLEDGE / PRIORITIZE / RESOLVE — LEADER-ONLY</span>
          )}
        </div>
      )}
    </div>
  );
}

function FileIssueForm({ role, notify, refetch }: { role: Role; notify: Notify; refetch: () => void }) {
  const [category, setCategory] = useState<string>(CATEGORIES[0]);
  const [kind, setKind] = useState<string>(FILE_ISSUE_KINDS[0]);
  const [severity, setSeverity] = useState<string>('medium');
  const [priority, setPriority] = useState<string>('normal');
  const [entityRef, setEntityRef] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!description.trim()) { notify('Add a short description before filing.', 'err'); return; }
    setSaving(true);
    const body: FileIssueRequest = { category, kind, severity, priority, entity_ref: entityRef.trim(), description: description.trim() };
    const res = await apiPost<Issue>('/crm/ops/data-quality', role, body);
    setSaving(false);
    if (!res || !res.issue_id) { notify('Could not file the issue — owner (or admin) access is required and the backbone must be up.', 'err'); return; }
    notify(`Filed "${res.description.slice(0, 40)}…" → data-quality queue.`, 'ok');
    refetch();
  };

  return (
    <FormCard title="FILE DATA-QUALITY ISSUE" tag="OWNER · POST /crm/ops/data-quality">
      <Row>
        <Field label="CATEGORY"><select value={category} onChange={(e) => setCategory(e.target.value)} style={SELECT}>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(c)}</option>)}</select></Field>
        <Field label="KIND"><select value={kind} onChange={(e) => setKind(e.target.value)} style={SELECT}>{FILE_ISSUE_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}</select></Field>
      </Row>
      <Row>
        <Field label="SEVERITY"><select value={severity} onChange={(e) => setSeverity(e.target.value)} style={SELECT}>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}</select></Field>
        <Field label="PRIORITY"><select value={priority} onChange={(e) => setPriority(e.target.value)} style={SELECT}>{PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}</select></Field>
      </Row>
      <Field label="ENTITY REF (optional · synthetic id, never PII)"><input value={entityRef} onChange={(e) => setEntityRef(e.target.value)} placeholder="e.g. Family-0042" style={INPUT} /></Field>
      <Field label="DESCRIPTION"><textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} placeholder="What's wrong, and where…" style={{ ...INPUT, resize: 'vertical' }} /></Field>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>owner stamped server-side · opens OPEN</span>
        <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'FILING…' : 'FILE ISSUE'}</button>
      </div>
    </FormCard>
  );
}

function ScoringChangeForm({ role, notify, refetch }: { role: Role; notify: Notify; refetch: () => void }) {
  const [summary, setSummary] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!summary.trim()) { notify('Describe the scoring-model change before proposing it.', 'err'); return; }
    setSaving(true);
    const res = await apiPost<{ decision?: { id?: string }; fix?: { fix_id?: string } }>(
      '/crm/ops/scoring-change', role, { summary: summary.trim() },
    );
    setSaving(false);
    if (!res || !res.decision) { notify('Could not propose the change — leader (or admin) access is required and the backbone must be up.', 'err'); return; }
    notify('Scoring-model change proposed → Decision Queue + change log.', 'ok', '/decision');
    setSummary('');
    refetch();
  };

  return (
    <div style={{ marginTop: 12 }}>
      <FormCard title="PROPOSE SCORING-MODEL CHANGE" tag="LEADER · POST /crm/ops/scoring-change">
        <Field label="CHANGE SUMMARY"><textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={2} placeholder="e.g. Raise the qualification threshold 60 → 65 for the fall cohort…" style={{ ...INPUT, resize: 'vertical' }} /></Field>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
          <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>opens a Decision-Queue item (workstream crm) + appends the change log · scoring stays read-only in HubSpot</span>
          <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'PROPOSING…' : 'PROPOSE CHANGE'}</button>
        </div>
      </FormCard>
    </div>
  );
}

// ============================ shared bits ====================================
function Histogram({ dist, tall }: { dist: LeadScoreDistribution; tall?: boolean }) {
  const max = Math.max(1, ...dist.bands.map((b) => b.count));
  const h = tall ? 120 : 76;
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, height: h }}>
        {dist.bands.map((b) => {
          // tier color: cold < warm_min(40), warm < hot_min(80), else hot.
          const color = b.low < 40 ? 'var(--ink-3)' : b.low < 80 ? 'var(--gold)' : 'var(--ok)';
          return (
            <div key={b.label} title={`${b.label}: ${b.count.toLocaleString()}`} style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', gap: 4, height: '100%' }}>
              <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>{b.count.toLocaleString()}</span>
              <div style={{ width: '100%', height: `${(100 * b.count) / max}%`, background: color, opacity: 0.82, minHeight: 2 }} />
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', gap: 8, marginTop: 5 }}>
        {dist.bands.map((b) => (
          <div key={b.label} style={{ flex: 1, textAlign: 'center', fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{b.label}</div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 14, marginTop: 8, fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
        <span><span style={{ display: 'inline-block', width: 9, height: 9, background: 'var(--ink-3)', opacity: 0.82, marginRight: 4 }} />cold {dist.tiers.cold.toLocaleString()}</span>
        <span><span style={{ display: 'inline-block', width: 9, height: 9, background: 'var(--gold)', opacity: 0.82, marginRight: 4 }} />warm {dist.tiers.warm.toLocaleString()}</span>
        <span><span style={{ display: 'inline-block', width: 9, height: 9, background: 'var(--ok)', opacity: 0.82, marginRight: 4 }} />hot {dist.tiers.hot.toLocaleString()}</span>
      </div>
    </>
  );
}

function FieldFlagList({ flags }: { flags: FieldFlag[] }) {
  if (flags.length === 0) return <Empty>No field flags.</Empty>;
  return (
    <>
      {flags.map((f) => {
        const bad = f.status === 'unreliable';
        return (
          <div key={f.field} style={{ padding: '9px 14px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 11.5, color: 'var(--ink)' }}>{fieldLabel(f.field)}</span>
              <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', background: bad ? 'var(--signal-soft)' : 'var(--ok-soft)', color: bad ? 'var(--signal)' : 'var(--ok)', whiteSpace: 'nowrap' }}>{f.status.toUpperCase()}</span>
            </div>
            {f.reason && <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.4 }}>{f.reason}</div>}
          </div>
        );
      })}
    </>
  );
}

function FixLog({ title, entries, emptyMsg }: { title: string; entries: FixLogEntry[]; emptyMsg: string }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
      <div style={{ padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>{title}</div>
      {entries.map((f) => (
        <div key={f.fix_id} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--line)' }}>
          <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: 'var(--accent-soft)', color: 'var(--ink-2)', whiteSpace: 'nowrap', marginTop: 1 }}>{f.kind.replace(/_/g, ' ').toUpperCase()}</span>
          <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink)', lineHeight: 1.4 }}>{f.summary}</span>
          <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', whiteSpace: 'nowrap' }}>{f.actor} · {fmtDate(f.applied_at)}</span>
        </div>
      ))}
      {entries.length === 0 && <Empty>{emptyMsg}</Empty>}
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

function SourceBadge({ info }: { info: SourceBadgeInfo }) {
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

const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
function TRIAGE_BTN(color: string): React.CSSProperties {
  return { fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', border: `1px solid ${color}`, background: 'var(--card)', color, padding: '6px 12px', borderRadius: 2 };
}
