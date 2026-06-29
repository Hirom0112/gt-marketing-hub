'use client';

// Field Marketing & Events (Module 8) — the Field & Events Owner's surface, wired
// end-to-end to the FastAPI backbone (Phase 2). Four controlled sub-views (TabBar):
//   8a Overview        — stat grid from GET /field/events/overview (upcoming /
//                        completed-this-month / RSVPs vs attendance + the rate /
//                        event→consult conversion with an HONEST "manual ·
//                        uninstrumented" label / top event type by attendance) +
//                        the cross-module note (ambassador events live in Grassroots,
//                        shown read-only on the calendar; proposals → Decision Queue).
//   8b Event tracker   — the field-event list from GET /field/events with a filter bar
//                        (type / status / owner / date range), an expandable detail row
//                        (notes / materials / budget / follow-up) and the OWNER-gated
//                        "Log event" (POST) + inline "Edit" (PATCH attendance / consults /
//                        status). Gated by canEditWorkstream('events') — admin (or an
//                        operator who owns 'events'); the demo operator owns grassroots,
//                        so only ADMIN writes here.
//   8c Calendar        — a real MONTH GRID from GET /field/events/calendar, color-coded
//                        by event_type, rendering field events AND ambassador events
//                        (Module 2) on the same grid; ambassador items are VISUALLY
//                        DISTINCT (dashed + a "Grassroots · read-only" badge) so the
//                        overlay is obvious. A legend maps each type to its color.
//   8d Priority recs   — a propose-event form (→ POST /field/events/proposal, open to any
//                        seat) + the list of raised field-event proposals read from GET
//                        /decisions (workstream "field_events") with their decision status.
// Every read falls back to a per-resource seed (lib/events-api) so the screen never
// blanks; the LIVE/SAMPLE pill is derived honestly from whether the fetch landed.

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { canEditWorkstream, moduleById, type Role } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';
import { apiGet, apiPost, apiPatch } from '@/lib/api';
import {
  type ApiDecision,
  outcomeOf,
  outcomeMeta,
  relAge,
  fmtBudget,
} from '@/lib/decisions';
import {
  type FieldOverview,
  type FieldEventRow,
  type CalendarItem,
  type FieldEventCreateRequest,
  type FieldEventUpdateRequest,
  type EventProposalRequest,
  type DecisionResponse,
  type MonthKey,
  SEED_OVERVIEW,
  SEED_EVENTS,
  SEED_CALENDAR,
  FIELD_EVENT_TYPE_OPTIONS,
  STATUS_OPTIONS,
  eventTypeColor,
  eventTypeLabel,
  statusStyle,
  fmtShortDate,
  fmtLongDate,
  monthOf,
  monthLabel,
  sameMonth,
  addMonth,
  dayOf,
  monthMatrix,
} from '@/lib/events-api';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

interface Toast { msg: string; kind: 'ok' | 'err'; }
type Notify = (m: string, k: 'ok' | 'err') => void;
type Ctx = { role: Role; canEdit: boolean; refetch: () => void; notify: Notify; live: boolean };

// ============================ the module =====================================
export function EventsModule() {
  const { session } = useSession();
  const def = moduleById('events')!;
  const canEdit = canEditWorkstream(session, 'events'); // admin always; operator only if owns 'events' (demo: admin only)
  const role = session.role;

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast | null>(null);

  const [overview, setOverview] = useState<FieldOverview | null>(null);
  const [events, setEvents] = useState<FieldEventRow[] | null>(null);
  const [calendar, setCalendar] = useState<CalendarItem[] | null>(null);
  const [proposals, setProposals] = useState<ApiDecision[] | null>(null);
  const [live, setLive] = useState(false);

  const load = useCallback(() => {
    apiGet<FieldOverview>('/field/events/overview', role).then((d) => {
      if (d && typeof d.total_rsvps === 'number') { setOverview(d); setLive(true); }
      else { setOverview(SEED_OVERVIEW); setLive(false); }
    });
    apiGet<FieldEventRow[]>('/field/events', role).then((d) =>
      setEvents(Array.isArray(d) && d.length > 0 ? d : SEED_EVENTS),
    );
    apiGet<CalendarItem[]>('/field/events/calendar', role).then((d) =>
      setCalendar(Array.isArray(d) && d.length > 0 ? d : SEED_CALENDAR),
    );
    apiGet<ApiDecision[]>('/decisions?view=all', role).then((d) =>
      setProposals(Array.isArray(d) ? d.filter((x) => x.workstream === 'field_events') : []),
    );
  }, [role]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  const notify = useCallback<Notify>((msg, kind) => setToast({ msg, kind }), []);

  const ov = overview ?? SEED_OVERVIEW;
  const ev = events ?? SEED_EVENTS;
  const cal = calendar ?? SEED_CALENDAR;
  const props = proposals ?? [];
  const ctx: Ctx = { role, canEdit, refetch: load, notify, live };

  return (
    <>
      <TabBar tabs={def.tabs} active={tab} onChange={setTab} />
      {toast && <ToastBar toast={toast} onClose={() => setToast(null)} />}
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        <Header idx={def.idx} title={def.title} owner={def.owner} canEdit={canEdit} live={live} />

        {tab === 0 && <OverviewTab ov={ov} />}
        {tab === 1 && <TrackerTab ev={ev} {...ctx} />}
        {tab === 2 && <CalendarTab cal={cal} />}
        {tab === 3 && <ProposalsTab props={props} {...ctx} />}

        <div style={{ marginTop: 18, fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>⌖ {def.source} · manual entry</div>
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

// =============================== 8a · OVERVIEW ===============================
function OverviewTab({ ov }: { ov: FieldOverview }) {
  const top = ov.top_event_type_by_attendance;
  return (
    <>
      {/* headline stat grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 14 }}>
        <StatTile label="UPCOMING EVENTS" value={String(ov.upcoming_count)} sub="confirmed / planning · next 30 days" />
        <StatTile label="COMPLETED THIS MONTH" value={String(ov.completed_this_month)} sub="field events run this month" />
        <StatTile label="CONSULTS BOOKED" value={String(ov.consults_booked_total)} sub="manually logged on-site / post-event" />

        {/* RSVP vs attendance — hero tile with the rate */}
        <div style={{ border: '1px solid var(--ink)', background: 'var(--card-2)', padding: 14, gridColumn: 'span 2' }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink)', fontWeight: 600 }}>RSVPS → ATTENDANCE</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, marginTop: 6, flexWrap: 'wrap' }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 30, color: 'var(--ink)', lineHeight: 1 }}>
              {ov.total_attendance}
              <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 400, color: 'var(--ink-3)' }}> / {ov.total_rsvps}</span>
            </div>
            <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, padding: '4px 10px', background: 'var(--ok-soft)', color: 'var(--ok)' }}>
              {ov.rsvp_to_attendance_pct}% SHOW RATE
            </span>
          </div>
          <div style={{ height: 6, background: 'var(--card)', border: '1px solid var(--line)', position: 'relative', marginTop: 9 }}>
            <div style={{ position: 'absolute', inset: 0, width: `${Math.min(100, ov.rsvp_to_attendance_pct)}%`, background: 'var(--ok)', opacity: 0.85 }} />
          </div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 6 }}>{ov.total_attendance} attended of {ov.total_rsvps} RSVPs across all field events with recorded attendance.</div>
        </div>

        {/* event→consult conversion — with the HONEST manual label */}
        <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card)', padding: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>EVENT → CONSULT</span>
            {ov.event_to_consult_manual && (
              <span style={{ fontFamily: MONO, fontSize: 7.5, fontWeight: 600, padding: '1px 5px', background: 'var(--warn-soft)', color: 'var(--warn)' }}>MANUAL v1 · UNINSTRUMENTED</span>
            )}
          </div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 28, color: 'var(--ink)', marginTop: 6, lineHeight: 1 }}>{ov.event_to_consult_pct}%</div>
          <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 5 }}>
            {ov.consults_booked_total} consults from {ov.total_attendance} attendees. Computed from a <b>hand-entered</b> consults field — not click/CRM instrumented yet.
          </div>
        </div>
      </div>

      {/* top event type by attendance */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>TOP EVENT TYPE BY ATTENDANCE</span>
        {top ? (
          <>
            <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, padding: '4px 11px', ...chip(eventTypeColor(top.event_type)) }}>{eventTypeLabel(top.event_type)}</span>
            <span style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 20, color: 'var(--ink)' }}>{top.attendance}</span>
            <span style={{ fontSize: 11, color: 'var(--ink-2)' }}>attendees — the highest-attendance format this period.</span>
          </>
        ) : (
          <span style={{ fontSize: 11, color: 'var(--ink-3)' }}>No attendance recorded yet.</span>
        )}
      </div>

      {/* cross-module note */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.7px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>CROSS-MODULE LINKS</div>
        <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {[
            <>Ambassador-hosted events live in <Link href="/grassroots" style={LINK}>Grassroots</Link> — they overlay this module&apos;s <b>Calendar</b> read-only (Module 8 never writes them).</>,
            <>Priority recommendations submit to the <Link href="/decision" style={LINK}>Decision Queue</Link> as open <code style={{ fontFamily: MONO, fontSize: 10 }}>field_events</code> proposals for leadership.</>,
            <>Event→consult is a <b>manual v1</b> signal — it is surfaced as uninstrumented rather than implying CRM tracking.</>,
          ].map((l, i) => (
            <li key={i} style={{ fontSize: 12, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
              <span style={{ color: 'var(--gold)' }}>→</span> <span>{l}</span>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

// ============================ 8b · EVENT TRACKER =============================
function TrackerTab({ ev, role, canEdit, refetch, notify }: { ev: FieldEventRow[] } & Ctx) {
  const [fType, setFType] = useState('');
  const [fStatus, setFStatus] = useState('');
  const [fOwner, setFOwner] = useState('');
  const [fFrom, setFFrom] = useState('');
  const [fTo, setFTo] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  const types = useMemo(() => Array.from(new Set(ev.map((e) => e.event_type))).sort(), [ev]);
  const statuses = useMemo(() => Array.from(new Set(ev.map((e) => e.status))).sort(), [ev]);
  const owners = useMemo(() => Array.from(new Set(ev.map((e) => e.owner))).sort(), [ev]);

  const filtered = ev.filter((e) =>
    (!fType || e.event_type === fType) &&
    (!fStatus || e.status === fStatus) &&
    (!fOwner || e.owner === fOwner) &&
    (!fFrom || e.event_date >= fFrom) &&
    (!fTo || e.event_date <= fTo),
  );
  const sorted = [...filtered].sort((a, b) => a.event_date.localeCompare(b.event_date));
  const sliced = !!(fType || fStatus || fOwner || fFrom || fTo);
  const clear = () => { setFType(''); setFStatus(''); setFOwner(''); setFFrom(''); setFTo(''); };

  return (
    <>
      {/* owner-gated Log event */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>FIELD-EVENT TRACKER · MANUAL ENTRY</span>
        {canEdit ? (
          <button onClick={() => setShowLog((s) => !s)} style={{ ...PRIMARY_BTN, cursor: 'pointer' }}>{showLog ? '✕ CLOSE FORM' : '+ LOG EVENT'}</button>
        ) : (
          <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, padding: '3px 9px', background: 'var(--accent-soft)', color: 'var(--ink-3)' }}>◌ LOG / EDIT — OWNER-GATED</span>
        )}
      </div>

      {canEdit && showLog && (
        <div style={{ marginBottom: 14 }}>
          <LogEventForm role={role} notify={notify} refetch={() => { refetch(); setShowLog(false); }} />
        </div>
      )}

      {/* filter bar */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', padding: '11px 13px', border: '1px solid var(--line-2)', background: 'var(--card-2)', marginBottom: 14 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, alignSelf: 'center' }}>FILTER</span>
        <FilterSelect label="TYPE" value={fType} onChange={setFType} all="All types" options={types} labeler={eventTypeLabel} />
        <FilterSelect label="STATUS" value={fStatus} onChange={setFStatus} all="Any status" options={statuses} labeler={(s) => statusStyle(s).label} />
        <FilterSelect label="OWNER" value={fOwner} onChange={setFOwner} all="Any owner" options={owners} />
        <DateField label="FROM" value={fFrom} onChange={setFFrom} />
        <DateField label="TO" value={fTo} onChange={setFTo} />
        {sliced && <button onClick={clear} style={{ fontFamily: MONO, fontSize: 9.5, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink-2)', padding: '7px 12px' }}>✕ CLEAR</button>}
        <span style={{ marginLeft: 'auto', alignSelf: 'center', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{sorted.length} of {ev.length} shown</span>
      </div>

      {/* table */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: GRID, fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '8px 16px', borderBottom: '2px solid var(--ink)', fontWeight: 600 }}>
          <div>EVENT</div>
          <div>TYPE</div>
          <div>DATE</div>
          <div>VENUE</div>
          <div style={{ textAlign: 'right' }}>RSVP</div>
          <div style={{ textAlign: 'right' }}>ATTEND</div>
          <div style={{ textAlign: 'right' }}>CONSULTS</div>
          <div>STATUS</div>
        </div>
        {sorted.map((e) => {
          const st = statusStyle(e.status);
          const tc = eventTypeColor(e.event_type);
          const open = openId === e.event_id;
          return (
            <div key={e.event_id}>
              <div
                onClick={() => { setOpenId(open ? null : e.event_id); if (open) setEditId(null); }}
                style={{ display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '9px 16px', borderBottom: '1px solid var(--line)', cursor: 'pointer', background: open ? 'var(--card-2)' : 'transparent' }}
              >
                <div style={{ fontSize: 11.5, color: 'var(--ink)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: 'var(--ink-3)', fontSize: 9, width: 8 }}>{open ? '▾' : '▸'}</span>{e.event_name}
                </div>
                <div><span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 6px', ...chip(tc) }}>{eventTypeLabel(e.event_type)}</span></div>
                <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{fmtShortDate(e.event_date)}</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{e.venue || '—'}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{e.rsvp_count}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: 'var(--ink)' }}>{e.attendance_count || '—'}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10.5, color: e.consults_booked > 0 ? 'var(--ok)' : 'var(--ink-3)' }}>{e.consults_booked || '—'}</div>
                <div><span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: st.bg, color: st.color }}>{st.label}</span></div>
              </div>
              {open && (
                <div style={{ padding: '12px 16px 16px 32px', borderBottom: '1px solid var(--line)', background: 'var(--card-2)' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 14 }}>
                    <Detail label="NOTES" value={e.notes || '—'} />
                    <Detail label="MATERIALS" value={e.materials || '—'} />
                    <Detail label="BUDGET" value={e.budget_usd > 0 ? `$${e.budget_usd.toLocaleString()}` : '—'} />
                    <Detail label="FOLLOW-UP" value={e.status === 'completed' ? `${e.consults_booked} consults booked · log outcomes` : e.status === 'cancelled' ? 'Cancelled — no follow-up' : 'Confirm attendance after the event'} />
                  </div>
                  {canEdit && (
                    <div style={{ marginTop: 12 }}>
                      {editId === e.event_id ? (
                        <EditEventForm e={e} role={role} notify={notify} refetch={() => { refetch(); setEditId(null); }} onCancel={() => setEditId(null)} />
                      ) : (
                        <button onClick={() => setEditId(e.event_id)} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--ink)', background: 'var(--card)', color: 'var(--ink)', padding: '6px 12px' }}>✎ EDIT — log attendance / consults / status</button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {sorted.length === 0 && <Empty>No field events match these filters.</Empty>}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Click a row to expand notes / materials / budget. {canEdit ? 'Log a new event or edit attendance, consults & status inline — writes hit POST/PATCH /field/events and refetch.' : 'Logging & editing are owner-gated (the Field & Events Owner / admin).'}
      </div>
    </>
  );
}
const GRID = '2.2fr 1.1fr .7fr 1fr .55fr .6fr .7fr .95fr';

// ---- 8b write forms --------------------------------------------------------
function LogEventForm({ role, refetch, notify }: { role: Role; refetch: () => void; notify: Notify }) {
  const [name, setName] = useState('');
  const [type, setType] = useState<string>(FIELD_EVENT_TYPE_OPTIONS[0]);
  const [date, setDate] = useState('');
  const [venue, setVenue] = useState('');
  const [status, setStatus] = useState<string>('planning');
  const [rsvp, setRsvp] = useState('');
  const [attend, setAttend] = useState('');
  const [consults, setConsults] = useState('');
  const [budget, setBudget] = useState('');
  const [notes, setNotes] = useState('');
  const [materials, setMaterials] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!name.trim() || !date) { notify('Event name and date are required.', 'err'); return; }
    setSaving(true);
    const body: FieldEventCreateRequest = {
      event_name: name.trim(), event_type: type, event_date: date,
      venue: venue.trim() || undefined, status,
      rsvp_count: num(rsvp), attendance_count: num(attend), consults_booked: num(consults), budget_usd: num(budget),
      notes: notes.trim() || undefined, materials: materials.trim() || undefined,
    };
    const res = await apiPost<FieldEventRow>('/field/events', role, body);
    setSaving(false);
    if (!res) { notify('Could not log the event — Field & Events owner (admin) access is required and the backbone must be up.', 'err'); return; }
    notify(`Logged field event "${name.trim()}".`, 'ok');
    refetch();
  };

  return (
    <FormCard title="LOG FIELD EVENT" tag="OWNER · POST /field/events">
      <Row>
        <Field label="EVENT NAME"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Fall STEM open house" style={INPUT} /></Field>
        <Field label="TYPE"><select value={type} onChange={(e) => setType(e.target.value)} style={SELECT}>{FIELD_EVENT_TYPE_OPTIONS.map((t) => <option key={t} value={t}>{eventTypeLabel(t)}</option>)}</select></Field>
      </Row>
      <Row>
        <Field label="DATE"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={INPUT} /></Field>
        <Field label="VENUE"><input value={venue} onChange={(e) => setVenue(e.target.value)} placeholder="e.g. Austin metro" style={INPUT} /></Field>
      </Row>
      <Row>
        <Field label="STATUS"><select value={status} onChange={(e) => setStatus(e.target.value)} style={SELECT}>{STATUS_OPTIONS.map((s) => <option key={s} value={s}>{statusStyle(s).label}</option>)}</select></Field>
        <Field label="BUDGET (USD)"><input type="number" min={0} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="0" style={INPUT} /></Field>
      </Row>
      <Row>
        <Field label="RSVP"><input type="number" min={0} value={rsvp} onChange={(e) => setRsvp(e.target.value)} placeholder="0" style={INPUT} /></Field>
        <Field label="ATTENDANCE"><input type="number" min={0} value={attend} onChange={(e) => setAttend(e.target.value)} placeholder="0" style={INPUT} /></Field>
      </Row>
      <Row>
        <Field label="CONSULTS BOOKED"><input type="number" min={0} value={consults} onChange={(e) => setConsults(e.target.value)} placeholder="0" style={INPUT} /></Field>
        <Field label="MATERIALS"><input value={materials} onChange={(e) => setMaterials(e.target.value)} placeholder="e.g. Tour decks, banner" style={INPUT} /></Field>
      </Row>
      <Field label="NOTES"><textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} placeholder="Context, outcomes, follow-up…" style={{ ...INPUT, resize: 'vertical' }} /></Field>
      <SubmitRow saving={saving} onClick={submit} label="LOG EVENT" />
    </FormCard>
  );
}

function EditEventForm({ e, role, refetch, notify, onCancel }: { e: FieldEventRow; role: Role; refetch: () => void; notify: Notify; onCancel: () => void }) {
  const [attend, setAttend] = useState(String(e.attendance_count));
  const [consults, setConsults] = useState(String(e.consults_booked));
  const [status, setStatus] = useState(e.status);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    const body: FieldEventUpdateRequest = {
      attendance_count: num(attend), consults_booked: num(consults), status,
    };
    const res = await apiPatch<FieldEventRow>(`/field/events/${e.event_id}`, role, body);
    setSaving(false);
    if (!res) { notify('Could not update the event — owner (admin) access is required.', 'err'); return; }
    notify(`Updated "${e.event_name}" — ${num(attend)} attended · ${num(consults)} consults · ${statusStyle(status).label.toLowerCase()}.`, 'ok');
    refetch();
  };

  return (
    <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', padding: '12px 14px', maxWidth: 520 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', color: 'var(--ink-3)', marginBottom: 10 }}>EDIT · PATCH /field/events/{e.event_id.slice(0, 8)}…</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
        <Field label="ATTENDANCE"><input type="number" min={0} value={attend} onChange={(ev) => setAttend(ev.target.value)} style={INPUT} /></Field>
        <Field label="CONSULTS"><input type="number" min={0} value={consults} onChange={(ev) => setConsults(ev.target.value)} style={INPUT} /></Field>
        <Field label="STATUS"><select value={status} onChange={(ev) => setStatus(ev.target.value)} style={SELECT}>{STATUS_OPTIONS.map((s) => <option key={s} value={s}>{statusStyle(s).label}</option>)}</select></Field>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, alignItems: 'center', marginTop: 10 }}>
        <button onClick={onCancel} style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink-2)', padding: '7px 12px' }}>CANCEL</button>
        <button onClick={submit} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'SAVING…' : 'SAVE CHANGES'}</button>
      </div>
    </div>
  );
}

// ============================ 8c · CALENDAR =================================
function CalendarTab({ cal }: { cal: CalendarItem[] }) {
  const fieldCount = cal.filter((c) => c.source === 'field').length;
  const ambCount = cal.filter((c) => c.source === 'ambassador').length;

  // Months that carry events; default to the busiest (most items) month.
  const months = useMemo(() => {
    const map = new Map<string, MonthKey & { n: number }>();
    for (const c of cal) {
      const m = monthOf(c.event_date);
      const k = `${m.year}-${m.month}`;
      const cur = map.get(k);
      if (cur) cur.n += 1; else map.set(k, { ...m, n: 1 });
    }
    return Array.from(map.values()).sort((a, b) => a.year - b.year || a.month - b.month);
  }, [cal]);
  const busiest = months.length ? months.reduce((a, b) => (b.n > a.n ? b : a)) : monthOf(new Date().toISOString().slice(0, 10));
  const [cur, setCur] = useState<MonthKey>({ year: busiest.year, month: busiest.month });

  const itemsByDay = useMemo(() => {
    const m: Record<number, CalendarItem[]> = {};
    for (const c of cal) {
      const mk = monthOf(c.event_date);
      if (sameMonth(mk, cur)) (m[dayOf(c.event_date)] ??= []).push(c);
    }
    return m;
  }, [cal, cur]);

  const matrix = monthMatrix(cur);
  const usedTypes = Array.from(new Set(cal.filter((c) => sameMonth(monthOf(c.event_date), cur)).map((c) => c.event_type)));

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 10 }}>
        <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>
          BLENDED CALENDAR · <b style={{ color: 'var(--ink)' }}>{fieldCount}</b> FIELD + <b style={{ color: 'var(--ink)' }}>{ambCount}</b> AMBASSADOR (READ-ONLY)
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <NavBtn onClick={() => setCur((m) => addMonth(m, -1))}>‹</NavBtn>
          <span style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 14, color: 'var(--ink)', minWidth: 150, textAlign: 'center' }}>{monthLabel(cur)}</span>
          <NavBtn onClick={() => setCur((m) => addMonth(m, 1))}>›</NavBtn>
        </div>
      </div>

      {/* overlay note */}
      <div style={{ border: '1px dashed var(--line-2)', background: 'var(--card)', padding: '9px 14px', marginBottom: 12, fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.5 }}>
        Field events are written here; <b>ambassador-hosted events</b> (Module 2 · Grassroots) overlay <b>read-only</b> — shown dashed with a <i>Grassroots · read-only</i> badge. Color encodes the event type (legend below).
      </div>

      {/* month grid */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', borderBottom: '2px solid var(--ink)' }}>
          {['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'].map((d) => (
            <div key={d} style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.3px', color: 'var(--ink-3)', padding: '7px 8px', textAlign: 'center' }}>{d}</div>
          ))}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)' }}>
          {matrix.flat().map((day, i) => {
            const items = day ? (itemsByDay[day] ?? []) : [];
            return (
              <div key={i} style={{ minHeight: 92, borderRight: (i % 7 !== 6) ? '1px solid var(--line)' : 'none', borderBottom: '1px solid var(--line)', padding: '5px 5px 6px', background: day ? 'transparent' : 'var(--card-2)' }}>
                {day > 0 && <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginBottom: 3 }}>{day}</div>}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {items.map((it) => <CalChip key={it.event_id} it={it} />)}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* legend */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '11px 14px' }}>
        <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>LEGEND</div>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
          {usedTypes.map((t) => {
            const c = eventTypeColor(t);
            return (
              <span key={t} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 11, height: 11, background: c.bg, border: `1.5px solid ${c.fg}`, display: 'inline-block' }} />
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)' }}>{eventTypeLabel(t)}</span>
              </span>
            );
          })}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginLeft: 6, paddingLeft: 14, borderLeft: '1px solid var(--line)' }}>
            <span style={{ width: 11, height: 11, background: 'transparent', border: '1.5px dashed var(--ink-3)', display: 'inline-block' }} />
            <span style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-2)' }}>Grassroots · read-only overlay</span>
          </span>
        </div>
      </div>
    </>
  );
}

function CalChip({ it }: { it: CalendarItem }) {
  const c = eventTypeColor(it.event_type);
  if (it.read_only) {
    // ambassador overlay — dashed outline, distinct
    return (
      <div title={`${it.event_name} · Grassroots · read-only`} style={{ border: `1.5px dashed ${c.fg}`, background: 'transparent', padding: '2px 5px', borderRadius: 2 }}>
        <div style={{ fontSize: 8.5, color: 'var(--ink)', lineHeight: 1.2, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.event_name}</div>
        <div style={{ fontFamily: MONO, fontSize: 6.5, fontWeight: 600, letterSpacing: '.2px', color: c.fg, marginTop: 1 }}>◌ GR · READ-ONLY</div>
      </div>
    );
  }
  // field event — solid color chip
  return (
    <div title={`${it.event_name} · ${eventTypeLabel(it.event_type)}${it.status ? ' · ' + it.status : ''}`} style={{ background: c.bg, borderLeft: `3px solid ${c.fg}`, padding: '2px 5px', borderRadius: 2 }}>
      <div style={{ fontSize: 8.5, color: c.fg, lineHeight: 1.2, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.event_name}</div>
    </div>
  );
}

// ============================ 8d · PRIORITY RECS ============================
function ProposalsTab({ props, role, notify, refetch }: { props: ApiDecision[] } & Ctx) {
  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1fr', gap: 14, alignItems: 'start' }}>
        <ProposeForm role={role} notify={notify} refetch={refetch} />

        {/* raised proposals + decision status */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Raised proposals</div>
            <Link href="/decision" style={{ fontFamily: MONO, fontSize: 9, color: 'var(--brand)', textDecoration: 'none' }}>Decision Queue →</Link>
          </div>
          {props.length === 0 && <Empty>No field-event proposals raised yet. Submit one to send it to leadership.</Empty>}
          {props.map((d) => {
            const o = outcomeOf(d);
            const m = outcomeMeta(o);
            const budget = fmtBudget(d.budget_ask);
            return (
              <div key={d.id} style={{ padding: '11px 16px', borderBottom: '1px solid var(--line)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                  <div style={{ fontSize: 12, color: 'var(--ink)', fontWeight: 500, lineHeight: 1.35 }}>{d.question.replace(/^Approve event proposal:\s*/, '')}</div>
                  <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, padding: '2px 7px', background: m.bg, color: m.color, whiteSpace: 'nowrap' }}>{m.label}</span>
                </div>
                {d.recommendation && <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 4 }}>{d.recommendation}</div>}
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 6, fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>
                  {budget && <span>ask {budget}</span>}
                  <span>priority {d.priority}</span>
                  {d.due_date && <span>due {fmtShortDate(d.due_date)}</span>}
                  {d.created_at && <span>{relAge(d.created_at)} ago</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 10 }}>
        ⌖ Proposing is open to any seat (the form lives in the owner&apos;s module). Each submission lands as an OPEN <code style={{ fontFamily: MONO }}>field_events</code> decision; leadership&apos;s verdict (APPROVED / REJECTED / NEED-INFO) reflects back here from GET /decisions.
      </div>
    </>
  );
}

function ProposeForm({ role, notify, refetch }: { role: Role; notify: Notify; refetch: () => void }) {
  const [name, setName] = useState('');
  const [type, setType] = useState<string>(FIELD_EVENT_TYPE_OPTIONS[0]);
  const [date, setDate] = useState('');
  const [persona, setPersona] = useState('');
  const [attendance, setAttendance] = useState('');
  const [rationale, setRationale] = useState('');
  const [budget, setBudget] = useState('');
  const [priority, setPriority] = useState('normal');
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    if (!name.trim()) { notify('An event name is required.', 'err'); return; }
    setSaving(true);
    // Fold the structured context (type / date / persona / expected attendance) into the
    // recommendation text — the proposal feeder carries name + recommendation + budget + priority.
    const ctxBits = [
      `Type: ${eventTypeLabel(type)}`,
      date && `Target date: ${fmtLongDate(date)}`,
      persona.trim() && `Persona: ${persona.trim()}`,
      attendance.trim() && `Expected attendance: ${num(attendance)}`,
    ].filter(Boolean).join(' · ');
    const recommendation = [rationale.trim(), ctxBits].filter(Boolean).join(rationale.trim() ? '\n' : '');
    const body: EventProposalRequest = {
      name: name.trim(),
      recommendation,
      budget_ask: budget.trim() ? Math.max(0, Number(budget)) : null,
      due_date: date || null,
      priority,
    };
    const res = await apiPost<DecisionResponse>('/field/events/proposal', role, body);
    setSaving(false);
    if (!res || !res.id) { notify('Could not submit the proposal — the backbone must be up.', 'err'); return; }
    notify(`Proposed "${name.trim()}" → open in the Decision Queue.`, 'ok');
    setName(''); setDate(''); setPersona(''); setAttendance(''); setRationale(''); setBudget(''); setPriority('normal');
    refetch();
  };

  return (
    <FormCard title="PROPOSE AN EVENT" tag="ANY SEAT · → DECISION QUEUE">
      <Field label="EVENT NAME"><input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Fall STEM open house — North Austin" style={INPUT} /></Field>
      <Row>
        <Field label="EVENT TYPE"><select value={type} onChange={(e) => setType(e.target.value)} style={SELECT}>{FIELD_EVENT_TYPE_OPTIONS.map((t) => <option key={t} value={t}>{eventTypeLabel(t)}</option>)}</select></Field>
        <Field label="TARGET DATE"><input type="date" value={date} onChange={(e) => setDate(e.target.value)} style={INPUT} /></Field>
      </Row>
      <Row>
        <Field label="TARGET PERSONA"><input value={persona} onChange={(e) => setPersona(e.target.value)} placeholder="e.g. K-2 robotics families" style={INPUT} /></Field>
        <Field label="EXPECTED ATTENDANCE"><input type="number" min={0} value={attendance} onChange={(e) => setAttendance(e.target.value)} placeholder="0" style={INPUT} /></Field>
      </Row>
      <Field label="RATIONALE / RECOMMENDATION"><textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} placeholder="Why this event, and the expected impact…" style={{ ...INPUT, resize: 'vertical' }} /></Field>
      <Row>
        <Field label="BUDGET ASK (USD)"><input type="number" min={0} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="0" style={INPUT} /></Field>
        <Field label="PRIORITY"><select value={priority} onChange={(e) => setPriority(e.target.value)} style={SELECT}><option value="normal">Normal</option><option value="urgent">Urgent</option></select></Field>
      </Row>
      <SubmitRow saving={saving} onClick={submit} label="SUBMIT PROPOSAL" footer={<Link href="/decision" style={{ fontFamily: MONO, fontSize: 9, color: 'var(--brand)', textDecoration: 'none' }}>open the Decision Queue →</Link>} />
    </FormCard>
  );
}

// ============================ shared bits ====================================
function num(s: string): number { return s.trim() ? Math.max(0, Math.round(Number(s))) : 0; }
function chip(c: { bg: string; fg: string }) { return { background: c.bg, color: c.fg }; }
const LINK: React.CSSProperties = { color: 'var(--ink)', fontWeight: 600 };

function StatTile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>{label}</div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 28, lineHeight: 1.05, marginTop: 7, color: 'var(--ink)' }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 4 }}>{sub}</div>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.3px', color: 'var(--ink-3)', fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 11, color: 'var(--ink-2)', lineHeight: 1.45 }}>{value}</div>
    </div>
  );
}

function NavBtn({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} style={{ fontFamily: MONO, fontSize: 13, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', padding: '2px 11px', lineHeight: 1.4 }}>{children}</button>;
}

function StatusPill({ live }: { live: boolean }) {
  return (
    <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', color: live ? 'var(--ok)' : 'var(--ink-3)', background: live ? 'var(--ok-soft)' : 'var(--accent-soft)' }}>
      {live ? '● LIVE' : '○ SAMPLE'}
    </span>
  );
}

function FilterSelect({ label, value, onChange, all, options, labeler }: { label: string; value: string; onChange: (v: string) => void; all: string; options: string[]; labeler?: (s: string) => string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} style={{ fontFamily: 'Geist', fontSize: 11.5, padding: '6px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, minWidth: 130 }}>
        <option value="">{all}</option>
        {options.map((o) => <option key={o} value={o}>{labeler ? labeler(o) : o}</option>)}
      </select>
    </label>
  );
}

function DateField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontFamily: MONO, fontSize: 8.5, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
      {label}
      <input type="date" value={value} onChange={(e) => onChange(e.target.value)} style={{ fontFamily: 'Geist', fontSize: 11.5, padding: '5px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 }} />
    </label>
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

function SubmitRow({ saving, onClick, label, footer }: { saving: boolean; onClick: () => void; label: string; footer?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: footer ? 'space-between' : 'flex-end', alignItems: 'center', gap: 12 }}>
      {footer}
      <button onClick={onClick} disabled={saving} style={{ ...PRIMARY_BTN, opacity: saving ? 0.6 : 1, cursor: saving ? 'default' : 'pointer' }}>{saving ? 'SAVING…' : label}</button>
    </div>
  );
}

function ToastBar({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '12px 22px 0', padding: '10px 14px', background: ok ? 'var(--ok-soft)' : 'var(--signal-soft)', border: `1px solid ${ok ? 'var(--ok)' : 'var(--signal)'}` }}>
      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, color: ok ? 'var(--ok)' : 'var(--signal)' }}>{ok ? '✓ DONE' : '⚠ ERROR'}</span>
      <span style={{ flex: 1, fontSize: 12, color: 'var(--ink)' }}>{toast.msg}</span>
      {ok && <Link href="/decision" style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color: 'var(--ok)' }}>open →</Link>}
      <button onClick={onClose} aria-label="Dismiss" style={{ border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: MONO, fontSize: 12, color: 'var(--ink-3)' }}>✕</button>
    </div>
  );
}

// ---- shared style objects ---------------------------------------------------
const INPUT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const SELECT: React.CSSProperties = { fontFamily: 'Geist', fontSize: 12.5, padding: '7px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2, width: '100%', boxSizing: 'border-box' };
const PRIMARY_BTN: React.CSSProperties = { fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2 };
