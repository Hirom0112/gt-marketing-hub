'use client';

// Decision Queue (Module 11) — the clearest role-gating + cross-module test.
//   • Operator → locked: a hatched banner + ONLY their own submissions + Raise.
//   • Admin    → reads the full queue, but no decide buttons (documented call).
//   • Leader   → the only role that can approve / reject / need-info on pending.
// Status transitions are local-state for the mock build; the same actions map
// 1:1 to backbone writes when wired.

import { useState } from 'react';
import { canDecide, canViewFullQueue, moduleById } from '@/lib/registry';
import { useSession } from '@/lib/session';
import { SEED_DECISIONS, statusMeta, typeColor, type Decision, type DqStatus } from '@/lib/decisions';
import { TabBar } from '@/components/TabBar';

const MONO = 'JetBrains Mono';

export function DecisionModule() {
  const { session } = useSession();
  const [rows, setRows] = useState<Decision[]>(SEED_DECISIONS);
  const def = moduleById('decision')!;

  const act = (id: string, status: DqStatus) =>
    setRows((rs) => rs.map((d) => (d.id === id ? { ...d, status } : d)));

  const fullQueue = canViewFullQueue(session);
  const canAct = canDecide(session); // leader only
  const isAdmin = session.role === 'admin';

  const counts = {
    pending: rows.filter((d) => d.status === 'pending').length,
    escalated: rows.filter((d) => d.type.indexOf('AUTO') === 0 && d.status === 'pending').length,
    needinfo: rows.filter((d) => d.status === 'needinfo').length,
  };

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* OPERATOR — locked surface + own submissions */}
        {!fullQueue && (
          <>
            <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 16, overflow: 'hidden' }}>
              <div style={{ padding: '26px 22px', background: 'repeating-linear-gradient(45deg,var(--hatch) 0 1px,transparent 1px 9px)', borderBottom: '1px solid var(--line-2)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 11 }}>
                  <span style={{ fontSize: 20 }}>🔒</span>
                  <div>
                    <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 18, color: 'var(--ink)' }}>The Decision Queue is leadership-only</div>
                    <div style={{ fontSize: 12.5, color: 'var(--ink-2)', marginTop: 3, maxWidth: 580, lineHeight: 1.5 }}>
                      As the <b>{session.userRole.replace('Operator · ', '')}</b> you can <b>submit</b> proposals into the queue from your module, but you cannot view or act on the full queue. Viewing and deciding are reserved for leadership.
                    </div>
                  </div>
                </div>
              </div>
              <div style={{ padding: '13px 22px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)' }}>YOUR ACCESS</span>
                <Pill bg="var(--ok-soft)" color="var(--ok)">✓ SUBMIT FROM MY MODULE</Pill>
                <Pill bg="var(--accent-soft)" color="var(--ink-3)">✕ VIEW QUEUE</Pill>
                <Pill bg="var(--accent-soft)" color="var(--ink-3)">✕ APPROVE / REJECT</Pill>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--line)', paddingBottom: 9, marginBottom: 12 }}>
              <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 14, color: 'var(--ink)' }}>Your submissions</div>
              <span
                role="button"
                tabIndex={0}
                style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: '.4px', padding: '5px 12px', borderRadius: 2, background: 'var(--ink)', color: 'var(--paper)', cursor: 'pointer', transition: 'opacity .15s var(--ease)' }}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.85')}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
              >+ RAISE A DECISION</span>
            </div>
            {rows.filter((d) => d.mine).map((d) => {
              const sm = statusMeta(d.status);
              return (
                <div key={d.id} style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: '13px 15px', marginBottom: 9, display: 'flex', alignItems: 'center', gap: 15 }}>
                  <span style={{ fontFamily: MONO, fontSize: 10, fontWeight: 600, color: 'var(--ink-3)' }}>{d.id}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 500, color: 'var(--ink)' }}>{d.title}</div>
                    <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 2 }}>{d.from} · {d.amount} · {d.age} ago</div>
                  </div>
                  <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: sm.bg, color: sm.color, whiteSpace: 'nowrap' }}>{sm.label}</span>
                </div>
              );
            })}
            <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 6 }}>
              You see only the decisions you submitted, and their status. Other proposals and the full queue stay hidden.
            </div>
          </>
        )}

        {/* ADMIN + LEADER — full queue */}
        {fullQueue && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
              <Count label="⌛ PENDING" value={counts.pending} color="var(--signal)" sub="awaiting your decision" border="var(--signal)" bg="var(--signal-soft)" />
              <Count label="⚑ AUTO-FLAGGED" value={counts.escalated} color="var(--ink)" sub="budget / sync breaches" />
              <Count label="↩ NEED-INFO" value={counts.needinfo} color="var(--warn)" sub="awaiting submitter" />
            </div>

            {isAdmin && (
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: 'var(--ink-3)', padding: '7px 11px', border: '1px dashed var(--line-2)', marginBottom: 12 }}>
                ADMIN VIEW (the Marketing Lead) · you can submit and read the queue, but approve / reject / need-info are reserved for Leadership. <span style={{ color: 'var(--ink-2)' }}>— documented assumption.</span>
              </div>
            )}

            {rows.map((d) => {
              const sm = statusMeta(d.status);
              const showActions = d.status === 'pending' && canAct;
              return (
                <div key={d.id} style={{ border: '1px solid var(--line)', background: 'var(--card)', marginBottom: 10 }}>
                  <div style={{ display: 'flex', gap: 14, padding: '14px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9, minWidth: 74 }}>
                      <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: sm.color, marginTop: 3, flexShrink: 0 }} />
                      <div>
                        <div style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>{d.id}</div>
                        <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginTop: 3 }}>{d.age} ago</div>
                      </div>
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '2px 7px', borderRadius: 2, border: `1px solid ${typeColor(d.type)}`, color: typeColor(d.type) }}>{d.type}</span>
                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--ink)' }}>{d.title}</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: 'var(--ink-2)', marginTop: 5, lineHeight: 1.5, maxWidth: 660 }}>{d.detail}</div>
                      <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 7 }}>⌖ {d.from} · raised by {d.by} · {d.amount}</div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 8, minWidth: 172 }}>
                      <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: sm.bg, color: sm.color, whiteSpace: 'nowrap' }}>{sm.label}</span>
                      {showActions && (
                        <div style={{ display: 'flex', gap: 5 }}>
                          <ActBtn border="var(--ok)" bg="var(--ok-soft)" color="var(--ok)" onClick={() => act(d.id, 'approved')}>✓ APPROVE</ActBtn>
                          <ActBtn border="var(--warn)" bg="var(--warn-soft)" color="var(--warn)" onClick={() => act(d.id, 'needinfo')}>↩ INFO</ActBtn>
                          <ActBtn border="var(--line-2)" bg="var(--card-2)" color="var(--ink-2)" onClick={() => act(d.id, 'rejected')}>✕ REJECT</ActBtn>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
            <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)', marginTop: 6 }}>
              Async approve / reject / need-info. Proposals arrive from operators; budget variance &gt;10%, sync-parity drops, and hot-family flags auto-escalate here. Decisions write back to their source module.
            </div>
          </>
        )}
      </section>
    </>
  );
}

function Pill({ bg, color, children }: { bg: string; color: string; children: React.ReactNode }) {
  return <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: bg, color, whiteSpace: 'nowrap' }}>{children}</span>;
}
function Count({ label, value, color, sub, border, bg }: { label: string; value: number; color: string; sub: string; border?: string; bg?: string }) {
  return (
    <div style={{ border: `1px solid ${border ?? 'var(--line-2)'}`, background: bg ?? 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: border ? color : 'var(--ink-3)', fontWeight: 600 }}>{label}</div>
      <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 30, lineHeight: 1.05, color, marginTop: 6 }}>{value}</div>
      <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 3 }}>{sub}</div>
    </div>
  );
}
function ActBtn({ border, bg, color, onClick, children }: { border: string; bg: string; color: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{ cursor: 'pointer', fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '5px 9px', borderRadius: 2, border: `1px solid ${border}`, background: bg, color, transition: 'transform .15s var(--ease), background .15s var(--ease)' }}
      onMouseEnter={(e) => (e.currentTarget.style.transform = 'translateY(-1px)')}
      onMouseLeave={(e) => (e.currentTarget.style.transform = 'translateY(0)')}
    >
      {children}
    </button>
  );
}
