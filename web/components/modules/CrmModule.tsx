'use client';

// CRM / Marketing Operations (Module 7) — the data backbone surfaced as product.
// This is where the Phase-1 sync backbone (Supabase ⇄ HubSpot parity) becomes a
// screen: sync parity score, the KNOWN-BROKEN UTM attribution flag, per-connector
// last-sync, field-reliability flags, and a data-quality queue (auto-detected +
// filed). The rule of truth — app_form owns funnel/TEFA/income/grade, never the
// HubSpot field values — is pinned and always on. We are honest about what is
// broken: UTM is a permanent red until rebuilt; we never fake green.

import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';

const MONO = 'JetBrains Mono';

// ---- Seed data (typed; inline) ---------------------------------------------

type SyncKind = 'ok' | 'warn' | 'bad';

interface SystemRow {
  name: string;
  role: string;
  last: string;
  status: string;
  kind: SyncKind;
}

interface FieldFlag {
  field: string;
  flag: string;
  why: string;
}

type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM';
type QueueOrigin = 'AUTO-DETECTED' | 'FILED';
type QueueStatus = 'OPEN' | 'IN PROGRESS' | 'BLOCKED';

interface QueueItem {
  id: string;
  title: string;
  origin: QueueOrigin;
  severity: Severity;
  owner: string;
  status: QueueStatus;
  age: string;
}

// Last-sync + health per connector (markup: crmSystems / mk()).
const SYSTEMS: SystemRow[] = [
  { name: 'Supabase app_form', role: 'SOURCE OF TRUTH · funnel/TEFA/income/grade', last: '3m ago', status: 'HEALTHY', kind: 'ok' },
  { name: 'HubSpot', role: 'CRM · pipeline · engagement · sequences', last: '6m ago', status: 'FIELD DRIFT', kind: 'bad' },
  { name: 'community.gt.school', role: 'Ambassadors (dual-source w/ HubSpot)', last: '22m ago', status: 'RECONCILE', kind: 'warn' },
  { name: 'summer.gt.school', role: 'Camp registrations (dual-source w/ form)', last: '14m ago', status: 'HEALTHY', kind: 'ok' },
  { name: 'Meta Business Suite', role: 'FB + IG engagement (stood-in / seeded)', last: '1h ago', status: 'STOOD-IN', kind: 'warn' },
  { name: 'X / Twitter', role: 'X engagement (stood-in / seeded)', last: '1h ago', status: 'STOOD-IN', kind: 'warn' },
  { name: 'GA4', role: 'Web analytics · UTM origin', last: '11m ago', status: 'UTM BROKEN', kind: 'bad' },
  { name: 'Google Sheet', role: 'Content production (synced r+w)', last: '5m ago', status: 'HEALTHY', kind: 'ok' },
];

// The unreliable fields — flagged below the parity threshold (markup: fieldFlags).
const FIELD_FLAGS: FieldFlag[] = [
  { field: 'income', flag: 'UNRELIABLE', why: 'HubSpot copy drifts from app_form — use app_form.' },
  { field: 'source', flag: 'UNRELIABLE', why: 'UTM origin broken upstream (GA4) — channel attribution suspect.' },
  { field: 'TEFA status', flag: 'UNRELIABLE', why: 'Funding signal lives in app_form, not the HubSpot field.' },
];

// Auto-detected + filed data-quality items, each with severity / owner / status.
const QUEUE: QueueItem[] = [
  { id: 'DQ-118', title: 'UTM attribution broken end-to-end — channel ROI not reportable', origin: 'AUTO-DETECTED', severity: 'CRITICAL', owner: 'the Marketing Lead', status: 'IN PROGRESS', age: '6d' },
  { id: 'DQ-131', title: 'HubSpot income field drifting from app_form (sync parity < 97%)', origin: 'AUTO-DETECTED', severity: 'HIGH', owner: 'the Marketing Lead', status: 'OPEN', age: '2d' },
  { id: 'DQ-134', title: 'TEFA status field stale in HubSpot — app_form is canonical', origin: 'AUTO-DETECTED', severity: 'HIGH', owner: 'the Marketing Lead', status: 'OPEN', age: '1d' },
  { id: 'DQ-137', title: 'community.gt.school ambassador rows need dual-source reconcile', origin: 'AUTO-DETECTED', severity: 'MEDIUM', owner: 'the Grassroots Owner', status: 'OPEN', age: '9h' },
  { id: 'DQ-126', title: 'Duplicate family records on phone-only match (filed by Admissions)', origin: 'FILED', severity: 'MEDIUM', owner: 'the Admissions Owner', status: 'BLOCKED', age: '4d' },
];

const SEV_TOKENS: Record<Severity, { color: string; bg: string }> = {
  CRITICAL: { color: 'var(--signal)', bg: 'var(--signal-soft)' },
  HIGH: { color: 'var(--warn)', bg: 'var(--warn-soft)' },
  MEDIUM: { color: 'var(--ink-2)', bg: 'var(--accent-soft)' },
};

const STATUS_TOKENS: Record<QueueStatus, { color: string; bg: string }> = {
  OPEN: { color: 'var(--signal)', bg: 'var(--signal-soft)' },
  'IN PROGRESS': { color: 'var(--warn)', bg: 'var(--warn-soft)' },
  BLOCKED: { color: 'var(--ink-3)', bg: 'var(--accent-soft)' },
};

function kindTokens(kind: SyncKind): { color: string; bg: string } {
  if (kind === 'ok') return { color: 'var(--ok)', bg: 'var(--ok-soft)' };
  if (kind === 'warn') return { color: 'var(--warn)', bg: 'var(--warn-soft)' };
  return { color: 'var(--signal)', bg: 'var(--signal-soft)' };
}

// ---- Component -------------------------------------------------------------

export function CrmModule() {
  const def = moduleById('crm')!;
  const openCount = QUEUE.filter((q) => q.status !== 'BLOCKED').length;

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* TOP ROW — parity score, BROKEN UTM, pinned rule of truth */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1.4fr', gap: 12, marginBottom: 16 }}>
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>SYNC PARITY · SUPABASE ⇄ HUBSPOT</div>
            <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 34, lineHeight: 1.05, color: 'var(--warn)', marginTop: 7 }}>96.2%</div>
            <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>below 97% threshold · 3 fields drifting</div>
          </div>

          <div style={{ border: '1px solid var(--signal)', background: 'var(--signal-soft)', padding: 14 }}>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--signal)', fontWeight: 600 }}>UTM ATTRIBUTION HEALTH</div>
            <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 34, lineHeight: 1.05, color: 'var(--signal)', marginTop: 7 }}>BROKEN</div>
            <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>permanent red until rebuilt · tracked here</div>
          </div>

          {/* Pinned rule of truth — always on, anchored with a full hairline + pin */}
          <div style={{ border: '1px solid var(--gold)', background: 'var(--gold-soft)', padding: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <span style={{ fontSize: 12 }}>📌</span>
              <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--gold)', fontWeight: 600 }}>RULE OF TRUTH · ALWAYS ON</span>
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink)', marginTop: 8, lineHeight: 1.5, fontWeight: 500 }}>
              Supabase <b>app_form</b> is the source of truth for <b>funnel / TEFA / income / grade</b> — never HubSpot field values for these.
            </div>
          </div>
        </div>

        {/* MIDDLE — source systems / last sync (left), field flags + queue summary (right) */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr', gap: 14, marginBottom: 16 }}>
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', padding: '10px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
              SOURCE SYSTEMS · LAST SYNC
            </div>
            {SYSTEMS.map((c) => {
              const t = kindTokens(c.kind);
              return (
                <div key={c.name} style={{ display: 'grid', gridTemplateColumns: '1.3fr 1.5fr .8fr 1fr', alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: t.color }} />
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>{c.name}</span>
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--ink-2)' }}>{c.role}</div>
                  <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)' }}>{c.last}</div>
                  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                    <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: t.bg, color: t.color, whiteSpace: 'nowrap' }}>{c.status}</span>
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
              <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', padding: '10px 14px', borderBottom: '1px solid var(--line-2)', fontWeight: 600 }}>
                ⚑ FIELD RELIABILITY FLAGS
              </div>
              {FIELD_FLAGS.map((f) => (
                <div key={f.field} style={{ padding: '9px 14px', borderBottom: '1px solid var(--line)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ fontSize: 11.5, color: 'var(--ink)' }}>{f.field}</span>
                    <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--signal-soft)', color: 'var(--signal)', whiteSpace: 'nowrap' }}>{f.flag}</span>
                  </div>
                  <div style={{ fontSize: 9.5, color: 'var(--ink-3)', marginTop: 4, lineHeight: 1.4 }}>{f.why}</div>
                </div>
              ))}
            </div>

            <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '13px 14px' }}>
              <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>⚙ DATA-QUALITY QUEUE · AUTO-DETECTED</div>
              <div style={{ display: 'flex', gap: 14, marginTop: 9 }}>
                <div>
                  <div style={{ fontFamily: 'Fraunces', fontSize: 24, fontWeight: 600, lineHeight: 1.05, color: 'var(--signal)' }}>{openCount}</div>
                  <div style={{ fontSize: 9.5, color: 'var(--ink-2)', marginTop: 2 }}>open</div>
                </div>
                <div>
                  <div style={{ fontFamily: 'Fraunces', fontSize: 24, fontWeight: 600, lineHeight: 1.05, color: 'var(--ink)' }}>11</div>
                  <div style={{ fontSize: 9.5, color: 'var(--ink-2)', marginTop: 2 }}>resolved 30d</div>
                </div>
              </div>
              <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 8, lineHeight: 1.45 }}>
                System auto-detects sync drift + UTM breakage and opens queue items — not manual-only.
              </div>
            </div>
          </div>
        </div>

        {/* DATA-QUALITY QUEUE — the items themselves: severity / owner / status */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>DATA-QUALITY QUEUE · OPEN ITEMS</span>
            <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>auto-detected + filed · severity · owner · status</span>
          </div>
          {QUEUE.map((q) => {
            const sev = SEV_TOKENS[q.severity];
            const st = STATUS_TOKENS[q.status];
            return (
              <div key={q.id} style={{ display: 'grid', gridTemplateColumns: 'auto 62px 1fr auto', gap: 12, alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: sev.color, flexShrink: 0 }} />
                <div style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color: 'var(--ink)' }}>{q.id}</div>
                <div>
                  <div style={{ fontSize: 12, color: 'var(--ink)', lineHeight: 1.4 }}>{q.title}</div>
                  <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>
                    {q.origin} · ⌖ {q.owner} · {q.age} old
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: sev.bg, color: sev.color, whiteSpace: 'nowrap' }}>{q.severity}</span>
                  <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: st.bg, color: st.color, whiteSpace: 'nowrap' }}>{q.status}</span>
                </div>
              </div>
            );
          })}
        </div>

        <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', lineHeight: 1.6 }}>
          ⌖ This module is where the Phase-1 sync backbone surfaces as product. Every figure elsewhere cites one of these systems. When parity drops below threshold, the <b>data-confidence banner</b> follows the affected numbers across every module — the Hub never silently picks a winner.
        </div>
      </section>
    </>
  );
}
