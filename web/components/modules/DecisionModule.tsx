'use client';

// Decision Queue (Module 11) — wired to the FastAPI backbone (Phase 3).
//   • Operator → locked: a hatched banner + ONLY their own submissions (GET
//     /decisions/mine, with the leader's resolution feedback) + the Raise form.
//   • Admin    → reads the full queue + history, but no decide buttons (documented).
//   • Leader   → the only role that can approve / reject / need-info (POST
//     /decisions/{id}/action). Sees Active, History, and the Raise form.
// Every fetch fails soft (apiGet/apiPost → null) so the screen still renders when
// the backbone is down. A lightweight toast surfaces raise success + resolutions.

import { useCallback, useEffect, useRef, useState } from 'react';
import { canDecide, canViewFullQueue, moduleById } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { apiGet, apiPost } from '@/lib/api';
import {
  type ApiDecision,
  type MyApiDecision,
  type RaiseBody,
  type ApiWorkstream,
  type ApiPriority,
  type Outcome,
  WORKSTREAM_OPTIONS,
  workstreamLabel,
  outcomeOf,
  outcomeMeta,
  relAge,
  fmtBudget,
} from '@/lib/decisions';
import { TabBar } from '@/components/TabBar';

const MONO = 'JetBrains Mono';
const DISPLAY = 'Fraunces';

type Toast = { kind: 'ok' | 'info'; text: string } | null;

export function DecisionModule() {
  const { session } = useSession();
  const def = moduleById('decision')!;
  const fullQueue = canViewFullQueue(session); // admin + leader
  const canAct = canDecide(session); // leader only

  const [tab, setTab] = useState(0);
  const [toast, setToast] = useState<Toast>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((t: Exclude<Toast, null>) => {
    setToast(t);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4200);
  }, []);
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  // Tabs differ by role: leadership gets the spec's 3 sub-views; an operator gets
  // only their own submissions + the raise form (no full queue / history).
  const tabs = fullQueue ? def.tabs : ['My submissions', 'Raise a decision'];
  // Clamp the active tab when the role (and thus tab set) changes via the switcher.
  useEffect(() => { setTab((t) => (t < tabs.length ? t : 0)); }, [tabs.length]);

  return (
    <>
      <TabBar tabs={tabs} active={tab} onChange={setTab} />
      <section className="scr" style={{ padding: '20px 22px 40px', position: 'relative' }}>
        {fullQueue ? (
          <>
            {tab === 0 && <ActiveTab role={session.role} canAct={canAct} isAdmin={session.role === 'admin'} onToast={showToast} />}
            {tab === 1 && <HistoryTab role={session.role} />}
            {tab === 2 && <RaiseFlow role={session.role} onSubmitted={() => setTab(0)} onToast={showToast} />}
          </>
        ) : (
          <>
            {tab === 0 && <OperatorSubmissions session={session} onToast={showToast} onRaise={() => setTab(1)} />}
            {tab === 1 && <RaiseFlow role={session.role} onSubmitted={() => setTab(0)} onToast={showToast} />}
          </>
        )}
      </section>
      {toast && <ToastBanner toast={toast} onClose={() => setToast(null)} />}
    </>
  );
}

// Tell the Sidebar badge (and any other listener) to refetch the open count.
function pingChanged() {
  try { window.dispatchEvent(new Event('decisions:changed')); } catch { /* SSR / no window */ }
}

// =========================== 11a · ACTIVE DECISIONS ==========================
function ActiveTab({
  role,
  canAct,
  isAdmin,
  onToast,
}: {
  role: 'admin' | 'leader' | 'operator';
  canAct: boolean;
  isAdmin: boolean;
  onToast: (t: Exclude<Toast, null>) => void;
}) {
  const [rows, setRows] = useState<ApiDecision[] | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    apiGet<ApiDecision[]>('/decisions?view=active', role).then((data) => {
      if (Array.isArray(data)) setRows(data);
      setLoaded(true);
    });
  }, [role]);
  useEffect(() => { load(); }, [load]);

  // Filters
  const [fWork, setFWork] = useState('');
  const [fPriority, setFPriority] = useState('');
  const [fOwner, setFOwner] = useState('');
  const [fDue, setFDue] = useState('');

  const list = rows ?? [];
  const owners = Array.from(new Set(list.map((d) => d.raised_by).filter(Boolean)));
  const now = Date.now();
  const filtered = list.filter((d) => {
    if (fWork && d.workstream !== fWork) return false;
    if (fPriority && d.priority !== fPriority) return false;
    if (fOwner && d.raised_by !== fOwner) return false;
    if (fDue) {
      const due = d.due_date ? new Date(d.due_date).getTime() : null;
      if (fDue === 'has' && due === null) return false;
      if (fDue === 'overdue' && (due === null || due >= now)) return false;
      if (fDue === 'soon' && (due === null || due < now || due > now + 7 * 864e5)) return false;
    }
    return true;
  });

  const counts = {
    open: list.length,
    urgent: list.filter((d) => d.priority === 'urgent').length,
    auto: list.filter((d) => d.source !== 'manual_raise').length,
  };

  const act = async (id: string, action: 'approve' | 'reject' | 'need_info', comment: string) => {
    const res = await apiPost<ApiDecision>(`/decisions/${id}/action`, role, { action, comment });
    if (res) {
      onToast({ kind: 'ok', text: action === 'approve' ? 'Decision approved.' : action === 'reject' ? 'Decision rejected.' : 'More info requested.' });
      load();
      pingChanged();
      return true;
    }
    onToast({ kind: 'info', text: 'Action failed — check you are signed in as a leader.' });
    return false;
  };

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
        <Count label="◷ OPEN" value={counts.open} color="var(--signal)" sub="awaiting a decision" border="var(--signal)" bg="var(--signal-soft)" />
        <Count label="! URGENT" value={counts.urgent} color="var(--warn)" sub="flagged urgent" />
        <Count label="⚑ AUTO-FLAGGED" value={counts.auto} color="var(--ink)" sub="system escalations" />
      </div>

      {isAdmin && (
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', padding: '7px 11px', border: '1px dashed var(--line-2)', marginBottom: 12 }}>
          ADMIN VIEW (the Marketing Lead) · you can submit and read the queue, but approve / reject / need-info are reserved for Leadership. <span style={{ color: 'var(--ink-2)' }}>— documented assumption.</span>
        </div>
      )}

      {/* Filter bar */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', padding: '11px 13px', border: '1px solid var(--line-2)', background: 'var(--card-2)', marginBottom: 14 }}>
        <FilterLabel>FILTER</FilterLabel>
        <Select value={fWork} onChange={setFWork}>
          <option value="">All workstreams</option>
          {WORKSTREAM_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
        <Select value={fPriority} onChange={setFPriority}>
          <option value="">Any priority</option>
          <option value="urgent">Urgent</option>
          <option value="normal">Normal</option>
        </Select>
        <Select value={fOwner} onChange={setFOwner}>
          <option value="">Any owner</option>
          {owners.map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
        <Select value={fDue} onChange={setFDue}>
          <option value="">Any due date</option>
          <option value="has">Has a due date</option>
          <option value="overdue">Overdue</option>
          <option value="soon">Due within 7 days</option>
        </Select>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          {filtered.length} of {list.length} shown
        </span>
      </div>

      {!loaded ? (
        <Empty>Loading the queue…</Empty>
      ) : filtered.length === 0 ? (
        <Empty>{list.length === 0 ? 'No open decisions — the queue is clear.' : 'No decisions match these filters.'}</Empty>
      ) : (
        filtered.map((d) => <ActiveCard key={d.id} d={d} canAct={canAct} onAct={act} />)
      )}

      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        Async approve / reject / need-info. Proposals arrive from operators; budget variance &gt;10%, sync-parity drops, and hot-family flags auto-escalate here. {rows === null && '· Backbone unreachable — showing an empty queue.'}
      </div>
    </>
  );
}

function ActiveCard({
  d,
  canAct,
  onAct,
}: {
  d: ApiDecision;
  canAct: boolean;
  onAct: (id: string, action: 'approve' | 'reject' | 'need_info', comment: string) => Promise<boolean>;
}) {
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const auto = d.source !== 'manual_raise';
  const typeLabel = auto ? `AUTO · ${d.source.toUpperCase()}` : 'PROPOSAL';
  const typeCol = auto ? 'var(--signal)' : 'var(--ink-2)';
  const budget = fmtBudget(d.budget_ask);

  const run = async (action: 'approve' | 'reject' | 'need_info') => {
    if (action === 'need_info' && !comment.trim()) return; // backend requires a comment
    setBusy(true);
    const ok = await onAct(d.id, action, comment.trim());
    setBusy(false);
    if (ok) setComment('');
  };

  return (
    <div style={{ border: '1px solid var(--line)', background: 'var(--card)', marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 14, padding: '14px 16px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9, minWidth: 56 }}>
          <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--signal)', marginTop: 4, flexShrink: 0 }} />
          <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 2 }}>{relAge(d.created_at) || '—'}</div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '2px 7px', borderRadius: 2, border: `1px solid ${typeCol}`, color: typeCol }}>{typeLabel}</span>
            {d.priority === 'urgent' && (
              <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '2px 7px', borderRadius: 2, background: 'var(--warn-soft)', color: 'var(--warn)' }}>! URGENT</span>
            )}
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{d.question || '(untitled decision)'}</span>
          </div>
          {d.recommendation && (
            <div style={{ fontSize: 11.5, color: 'var(--ink-2)', marginTop: 5, lineHeight: 1.5, maxWidth: 680 }}>
              <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginRight: 6 }}>REC</span>{d.recommendation}
            </div>
          )}
          <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
            <span>⌖ {workstreamLabel(d.workstream)}</span>
            <span>raised by {d.raised_by || '—'}</span>
            {budget && <span style={{ color: 'var(--ink-2)' }}>budget {budget}</span>}
            {d.due_date && <span>due {d.due_date}</span>}
          </div>
        </div>
        <div style={{ minWidth: 80, display: 'flex', justifyContent: 'flex-end' }}>
          <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--signal-soft)', color: 'var(--signal)', whiteSpace: 'nowrap', height: 'fit-content' }}>OPEN</span>
        </div>
      </div>

      {/* Leadership response row (leader only) */}
      {canAct && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '11px 16px', borderTop: '1px solid var(--line)', background: 'var(--card-2)', flexWrap: 'wrap' }}>
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Comment (required for need-info)…"
            style={{ flex: 1, minWidth: 180, fontFamily: 'Geist', fontSize: 12, padding: '7px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2 }}
          />
          <ActBtn border="var(--ok)" bg="var(--ok-soft)" color="var(--ok)" disabled={busy} onClick={() => run('approve')}>✓ APPROVE</ActBtn>
          <ActBtn border="var(--warn)" bg="var(--warn-soft)" color="var(--warn)" disabled={busy || !comment.trim()} onClick={() => run('need_info')}>↩ NEED INFO</ActBtn>
          <ActBtn border="var(--line-2)" bg="var(--card)" color="var(--ink-2)" disabled={busy} onClick={() => run('reject')}>✕ REJECT</ActBtn>
        </div>
      )}
    </div>
  );
}

// =============================== 11b · HISTORY ===============================
function HistoryTab({ role }: { role: 'admin' | 'leader' | 'operator' }) {
  const [rows, setRows] = useState<ApiDecision[] | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    apiGet<ApiDecision[]>('/decisions?view=history', role).then((data) => {
      if (Array.isArray(data)) setRows(data);
      setLoaded(true);
    });
  }, [role]);

  const [q, setQ] = useState('');
  const [fWork, setFWork] = useState('');
  const [fOutcome, setFOutcome] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');

  const list = rows ?? [];
  const filtered = list.filter((d) => {
    if (q && !d.question.toLowerCase().includes(q.toLowerCase())) return false;
    if (fWork && d.workstream !== fWork) return false;
    const oc = outcomeOf(d);
    if (fOutcome && oc !== fOutcome) return false;
    if (from || to) {
      const r = d.resolution_date ? new Date(d.resolution_date).getTime() : null;
      if (r === null) return false;
      if (from && r < new Date(from).getTime()) return false;
      if (to && r > new Date(to).getTime() + 864e5) return false;
    }
    return true;
  });

  return (
    <>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', padding: '11px 13px', border: '1px solid var(--line-2)', background: 'var(--card-2)', marginBottom: 14 }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search decisions…"
          style={{ flex: 1, minWidth: 160, fontFamily: 'Geist', fontSize: 12, padding: '6px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)', color: 'var(--ink)', borderRadius: 2 }}
        />
        <Select value={fWork} onChange={setFWork}>
          <option value="">All workstreams</option>
          {WORKSTREAM_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </Select>
        <Select value={fOutcome} onChange={setFOutcome}>
          <option value="">Any outcome</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="needinfo">Need-info</option>
          <option value="inflight">In flight</option>
        </Select>
        <DateInput value={from} onChange={setFrom} title="Resolved from" />
        <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>→</span>
        <DateInput value={to} onChange={setTo} title="Resolved to" />
      </div>

      <div style={{ border: '1px solid var(--ink)', background: 'var(--card)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '2.4fr 1fr 1fr 0.9fr 0.9fr', fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', padding: '8px 14px', borderBottom: '1px solid var(--line-2)', background: 'var(--card-2)', fontWeight: 600 }}>
          <div>DECISION</div>
          <div>WORKSTREAM</div>
          <div>RAISED BY</div>
          <div style={{ textAlign: 'right' }}>RESOLVED</div>
          <div style={{ textAlign: 'center' }}>OUTCOME</div>
        </div>
        {!loaded ? (
          <Empty>Loading history…</Empty>
        ) : filtered.length === 0 ? (
          <Empty>{list.length === 0 ? 'No decided items yet.' : 'No decisions match this search.'}</Empty>
        ) : (
          filtered.map((d, i) => {
            const m = outcomeMeta(outcomeOf(d));
            const resolved = d.resolution_date ? new Date(d.resolution_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—';
            return (
              <div key={d.id} style={{ display: 'grid', gridTemplateColumns: '2.4fr 1fr 1fr 0.9fr 0.9fr', alignItems: 'center', padding: '11px 14px', borderBottom: '1px solid var(--line)', background: i % 2 ? 'var(--card-2)' : 'transparent' }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, color: 'var(--ink)', fontWeight: 500 }}>{d.question || '(untitled)'}</div>
                  {d.recommendation && <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.recommendation}</div>}
                </div>
                <div style={{ fontSize: 11, color: 'var(--ink-2)' }}>{workstreamLabel(d.workstream)}</div>
                <div style={{ fontFamily: MONO, fontSize: 10, color: 'var(--ink-2)' }}>{d.raised_by || '—'}</div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 10, color: 'var(--ink-3)' }}>{resolved}</div>
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: m.bg, color: m.color, whiteSpace: 'nowrap' }}>{m.label}</span>
                </div>
              </div>
            );
          })
        )}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 8 }}>
        The decided / in-flight archive — full audit trail. Outcome is the leadership verdict (approved / rejected / need-info) from the decision&apos;s action log.
      </div>
    </>
  );
}

// =============================== 11c · RAISE FLOW ============================
function RaiseFlow({
  role,
  onSubmitted,
  onToast,
}: {
  role: 'admin' | 'leader' | 'operator';
  onSubmitted: () => void;
  onToast: (t: Exclude<Toast, null>) => void;
}) {
  const blank = { question: '', recommendation: '', workstream: '' as '' | ApiWorkstream, budget_ask: '', due_date: '', priority: 'normal' as ApiPriority };
  const [f, setF] = useState(blank);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));
  const valid = f.question.trim() && f.recommendation.trim() && f.workstream;

  const submit = async () => {
    if (!valid) { setErr('Question, recommendation, and workstream are required.'); return; }
    setErr(null);
    setBusy(true);
    const body: RaiseBody = {
      question: f.question.trim(),
      recommendation: f.recommendation.trim(),
      workstream: f.workstream as ApiWorkstream,
      budget_ask: f.budget_ask.trim() ? Number(f.budget_ask) : null,
      due_date: f.due_date || null,
      priority: f.priority,
    };
    const res = await apiPost<ApiDecision>('/decisions', role, body);
    setBusy(false);
    if (res) {
      setF(blank);
      onToast({ kind: 'ok', text: 'Submitted — leadership will review.' });
      pingChanged();
      onSubmitted();
    } else {
      setErr('Submit failed — the backbone may be unreachable.');
    }
  };

  return (
    <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', maxWidth: 720 }}>
      <div style={{ padding: '11px 16px', borderBottom: '2px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', fontFamily: DISPLAY, fontWeight: 700, fontSize: 14, letterSpacing: '.3px' }}>
        RAISE A DECISION
      </div>
      <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Field label="QUESTION" hint="What needs a decision?">
          <input value={f.question} onChange={(e) => set('question', e.target.value)} placeholder="e.g. Fund a parent-panel pop-up for the August push?" style={inputStyle} />
        </Field>
        <Field label="RECOMMENDATION" hint="Your proposed call + the why">
          <textarea value={f.recommendation} onChange={(e) => set('recommendation', e.target.value)} rows={3} placeholder="Recommend approving $8K for two pop-ups in high-income TX zips…" style={{ ...inputStyle, resize: 'vertical', fontFamily: 'Geist' }} />
        </Field>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          <Field label="WORKSTREAM">
            <select value={f.workstream} onChange={(e) => set('workstream', e.target.value as ApiWorkstream)} style={inputStyle}>
              <option value="">Select a workstream…</option>
              {WORKSTREAM_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>
          <Field label="PRIORITY">
            <select value={f.priority} onChange={(e) => set('priority', e.target.value as ApiPriority)} style={inputStyle}>
              <option value="normal">Normal</option>
              <option value="urgent">Urgent</option>
            </select>
          </Field>
          <Field label="BUDGET ASK" hint="Optional · USD">
            <input type="number" value={f.budget_ask} onChange={(e) => set('budget_ask', e.target.value)} placeholder="8000" style={inputStyle} />
          </Field>
          <Field label="DUE DATE" hint="Optional">
            <input type="date" value={f.due_date} onChange={(e) => set('due_date', e.target.value)} style={inputStyle} />
          </Field>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          {err && <span style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--signal)' }}>{err}</span>}
          <button
            onClick={submit}
            disabled={busy}
            style={{ marginLeft: 'auto', cursor: busy ? 'default' : 'pointer', fontFamily: MONO, fontSize: 10, fontWeight: 600, letterSpacing: '.4px', padding: '8px 16px', border: '1px solid var(--ink)', background: 'var(--ink)', color: 'var(--paper)', borderRadius: 2, opacity: busy ? 0.6 : 1 }}
          >
            {busy ? 'SUBMITTING…' : '+ SUBMIT TO QUEUE'}
          </button>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>
          You are submitting as <b style={{ color: 'var(--ink-2)' }}>{role}</b> — your identity is stamped server-side. Anyone may raise a decision; only leadership decides.
        </div>
      </div>
    </div>
  );
}

// ===================== OPERATOR · MY SUBMISSIONS =============================
function OperatorSubmissions({
  session,
  onToast,
  onRaise,
}: {
  session: { role: 'admin' | 'leader' | 'operator'; userRole: string };
  onToast: (t: Exclude<Toast, null>) => void;
  onRaise: () => void;
}) {
  const [rows, setRows] = useState<MyApiDecision[] | null>(null);
  const [loaded, setLoaded] = useState(false);
  const prev = useRef<Record<string, Outcome>>({});

  const load = useCallback(() => {
    apiGet<MyApiDecision[]>('/decisions/mine', session.role).then((data) => {
      if (Array.isArray(data)) {
        // Resolution toast: an item that was open (pending/needinfo) and is now decided.
        const _decided = (o: Outcome) => o === 'approved' || o === 'rejected' || o === 'resolved' || o === 'inflight';
        for (const d of data) {
          const before = prev.current[d.id];
          const after = outcomeOf(d);
          if (before && (before === 'pending' || before === 'needinfo') && _decided(after)) {
            const verb = after === 'approved' ? 'approved' : after === 'rejected' ? 'rejected' : 'resolved';
            onToast({ kind: 'info', text: `Your proposal was ${verb} by leadership.` });
          }
        }
        const next: Record<string, Outcome> = {};
        for (const d of data) next[d.id] = outcomeOf(d);
        prev.current = next;
        setRows(data);
      }
      setLoaded(true);
    });
  }, [session.role, onToast]);

  useEffect(() => {
    load();
    const onChanged = () => load();
    window.addEventListener('decisions:changed', onChanged);
    return () => window.removeEventListener('decisions:changed', onChanged);
  }, [load]);

  const list = rows ?? [];

  return (
    <>
      {/* Locked, hatched banner — the queue stays leadership-only */}
      <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 16, overflow: 'hidden' }}>
        <div style={{ padding: '26px 22px', background: 'repeating-linear-gradient(45deg,var(--hatch) 0 1px,transparent 1px 9px)', borderBottom: '1px solid var(--line-2)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
            <span style={{ fontSize: 20 }}>🔒</span>
            <div>
              <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 18, color: 'var(--ink)' }}>The Decision Queue is leadership-only</div>
              <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 3, maxWidth: 580, lineHeight: 1.5 }}>
                As the <b>{session.userRole.replace('Operator · ', '')}</b> you can <b>submit</b> proposals into the queue, and track what becomes of them below — but viewing and deciding the full queue are reserved for leadership.
              </div>
            </div>
          </div>
        </div>
        <div style={{ padding: '13px 22px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>YOUR ACCESS</span>
          <Pill bg="var(--ok-soft)" color="var(--ok)">✓ SUBMIT + TRACK MINE</Pill>
          <Pill bg="var(--accent-soft)" color="var(--ink-3)">✕ VIEW QUEUE</Pill>
          <Pill bg="var(--accent-soft)" color="var(--ink-3)">✕ APPROVE / REJECT</Pill>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--line)', paddingBottom: 9, marginBottom: 12 }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 14, color: 'var(--ink)' }}>Your submissions</div>
        <button
          onClick={onRaise}
          style={{ border: 'none', fontFamily: MONO, fontSize: 9.5, letterSpacing: '.4px', padding: '6px 12px', borderRadius: 2, background: 'var(--ink)', color: 'var(--paper)', cursor: 'pointer' }}
        >
          + RAISE A DECISION
        </button>
      </div>

      {!loaded ? (
        <Empty>Loading your submissions…</Empty>
      ) : list.length === 0 ? (
        <Empty>You haven&apos;t raised any decisions yet. Use <b>+ Raise a decision</b> to submit one.</Empty>
      ) : (
        list.map((d) => {
          const oc = outcomeOf(d);
          const m = outcomeMeta(oc);
          const decided = oc === 'approved' || oc === 'rejected' || oc === 'resolved' || oc === 'inflight';
          const borderColor = oc === 'rejected' ? 'var(--signal)' : decided ? 'var(--ok)' : 'var(--line-2)';
          const budget = fmtBudget(d.budget_ask);
          return (
            <div key={d.id} style={{ border: `1px solid ${borderColor}`, background: 'var(--card)', padding: '13px 15px', marginBottom: 9 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--ink)' }}>{d.question || '(untitled)'}</div>
                  <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 3 }}>
                    {workstreamLabel(d.workstream)}{budget ? ` · ${budget}` : ''}{d.created_at ? ` · ${relAge(d.created_at)} ago` : ''}
                  </div>
                </div>
                <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: m.bg, color: m.color, whiteSpace: 'nowrap' }}>
                  {oc === 'approved' ? `✓ ${m.label}` : oc === 'rejected' ? `✕ ${m.label}` : decided ? `● ${m.label}` : m.label}
                </span>
              </div>
              {/* Resolution feedback — the leader's latest comment */}
              {d.latest_comment && (
                <div style={{ marginTop: 10, padding: '9px 11px', background: 'var(--card-2)', borderLeft: '2px solid var(--brand)', fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
                  <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginRight: 6 }}>LEADERSHIP</span>
                  {d.latest_comment}
                </div>
              )}
            </div>
          );
        })
      )}
      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 6 }}>
        You see only the decisions you submitted, and their resolution. The full queue stays hidden. {rows === null && '· Backbone unreachable.'}
      </div>
    </>
  );
}

// ============================== shared bits =================================
const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', fontFamily: 'Geist', fontSize: 12.5,
  padding: '8px 10px', border: '1px solid var(--line-2)', background: 'var(--paper)',
  color: 'var(--ink)', borderRadius: 2,
};

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--ink-3)', fontWeight: 600 }}>
        {label}{hint && <span style={{ marginLeft: 7, fontWeight: 400, color: 'var(--ink-3)', opacity: 0.8 }}>{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function FilterLabel({ children }: { children: React.ReactNode }) {
  return <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>{children}</span>;
}

function Select({ value, onChange, children }: { value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ fontFamily: 'Geist', fontSize: 11.5, padding: '5px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 }}
    >
      {children}
    </select>
  );
}

function DateInput({ value, onChange, title }: { value: string; onChange: (v: string) => void; title: string }) {
  return (
    <input
      type="date"
      title={title}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ fontFamily: MONO, fontSize: 11, padding: '5px 8px', border: '1px solid var(--line-2)', background: 'var(--card)', color: 'var(--ink)', borderRadius: 2 }}
    />
  );
}

function Pill({ bg, color, children }: { bg: string; color: string; children: React.ReactNode }) {
  return <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: bg, color, whiteSpace: 'nowrap' }}>{children}</span>;
}

function Count({ label, value, color, sub, border, bg }: { label: string; value: number; color: string; sub: string; border?: string; bg?: string }) {
  return (
    <div style={{ border: `1px solid ${border ?? 'var(--line-2)'}`, background: bg ?? 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: border ? color : 'var(--ink-3)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 30, lineHeight: 1.05, color, marginTop: 6 }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 3 }}>{sub}</div>
    </div>
  );
}

function ActBtn({ border, bg, color, disabled, onClick, children }: { border: string; bg: string; color: string; disabled?: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{ cursor: disabled ? 'not-allowed' : 'pointer', fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '7px 11px', borderRadius: 2, border: `1px solid ${border}`, background: bg, color, opacity: disabled ? 0.45 : 1, transition: 'opacity .15s var(--ease)' }}
    >
      {children}
    </button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ padding: '34px 16px', textAlign: 'center', fontFamily: MONO, fontSize: 11, color: 'var(--ink-3)' }}>
      {children}
    </div>
  );
}

function ToastBanner({ toast, onClose }: { toast: Exclude<Toast, null>; onClose: () => void }) {
  const ok = toast.kind === 'ok';
  return (
    <div
      role="status"
      onClick={onClose}
      style={{
        position: 'fixed', bottom: 22, right: 22, zIndex: 50, cursor: 'pointer',
        display: 'flex', alignItems: 'center', gap: 10, padding: '11px 15px',
        border: `1px solid ${ok ? 'var(--ok)' : 'var(--brand)'}`,
        background: 'var(--card)', boxShadow: '0 6px 24px rgba(0,0,0,.18)', borderRadius: 3,
        maxWidth: 340,
      }}
    >
      <span style={{ fontSize: 14, color: ok ? 'var(--ok)' : 'var(--brand)' }}>{ok ? '✓' : 'ℹ'}</span>
      <span style={{ fontSize: 12.5, color: 'var(--ink)' }}>{toast.text}</span>
    </div>
  );
}
