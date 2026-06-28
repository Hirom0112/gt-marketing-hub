'use client';

// The spec-faithful "module brief" screen used for every module that hasn't yet
// been built into its full sub-views: owner + edit-state, summary, three headline
// stats, cross-module links, and an honest "what's broken / manual v1" note.
// Honest about state per the product principle (fail-closed reads as a state).

import { canEditWorkstream, type ModuleDef } from '@/lib/registry';
import { MODULE_BRIEFS, type StatColor } from '@/lib/moduleData';
import { useSession } from '@/lib/session';
import { TabBar } from '@/components/TabBar';

const COLOR: Record<StatColor, string> = {
  ink: 'var(--ink)', ok: 'var(--ok)', signal: 'var(--signal)',
  broken: 'var(--broken)', gold: 'var(--gold)', warn: 'var(--warn)',
};

export function GenericModule({ def }: { def: ModuleDef }) {
  const { session } = useSession();
  const brief = MODULE_BRIEFS[def.id];
  const canEdit = canEditWorkstream(session, def.id);

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Header band */}
        <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 16, borderBottom: '1px solid var(--line)', paddingBottom: 12 }}>
          <div>
            <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '1px', color: 'var(--ink-3)', marginBottom: 5 }}>
              MODULE {def.idx} · OWNER: {def.owner.toUpperCase()}
            </div>
            <div style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 16, color: 'var(--ink)' }}>{def.title}</div>
          </div>
          <span
            style={{
              fontFamily: 'JetBrains Mono', fontSize: 9, fontWeight: 600, padding: '3px 9px',
              background: canEdit ? 'var(--gold-soft)' : 'var(--accent-soft)',
              color: canEdit ? 'var(--gold)' : 'var(--ink-3)',
            }}
          >
            {canEdit ? '✎ EDITABLE' : '◌ READ-ONLY'}
          </span>
        </div>

        {/* Summary */}
        {brief?.summary && (
          <p style={{ fontSize: 13.5, lineHeight: 1.65, color: 'var(--ink-2)', maxWidth: 760, margin: '0 0 18px' }}>{brief.summary}</p>
        )}

        {/* Headline stats */}
        {brief?.stats?.length ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 18 }}>
            {brief.stats.map((s) => (
              <div key={s.label} style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14 }}>
                <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '.5px', color: 'var(--ink-3)', fontWeight: 600 }}>{s.label}</div>
                <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 30, lineHeight: 1.05, letterSpacing: '-.5px', color: COLOR[s.color ?? 'ink'], marginTop: 7 }}>{s.value}</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginTop: 4 }}>{s.note}</div>
              </div>
            ))}
          </div>
        ) : null}

        {/* Cross-links + what's broken */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {brief?.links?.length ? (
            <Panel title="CROSS-MODULE LINKS">
              <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {brief.links.map((l) => (
                  <li key={l} style={{ fontSize: 12, color: 'var(--ink-2)', display: 'flex', gap: 7 }}>
                    <span style={{ color: 'var(--gold)' }}>→</span> {l}
                  </li>
                ))}
              </ul>
            </Panel>
          ) : null}

          {brief?.broken ? (
            <Panel title="WHAT'S BROKEN / MANUAL v1" tone="signal">
              <p style={{ margin: 0, fontSize: 12, lineHeight: 1.6, color: 'var(--ink-2)' }}>{brief.broken}</p>
            </Panel>
          ) : (
            <Panel title="STATE">
              <p style={{ margin: 0, fontSize: 12, lineHeight: 1.6, color: 'var(--ink-2)' }}>
                No known data gaps. Sub-views: {def.tabs.join(' · ')}.
              </p>
            </Panel>
          )}
        </div>

        <div style={{ marginTop: 18, fontFamily: 'JetBrains Mono', fontSize: 9, color: 'var(--ink-3)' }}>
          ⌖ {def.source}
        </div>
      </section>
    </>
  );
}

function Panel({ title, tone, children }: { title: string; tone?: 'signal'; children: React.ReactNode }) {
  return (
    <div style={{ border: `1px solid ${tone === 'signal' ? 'var(--signal)' : 'var(--line-2)'}`, background: tone === 'signal' ? 'var(--signal-soft)' : 'var(--card)', padding: 14 }}>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '.7px', color: tone === 'signal' ? 'var(--signal)' : 'var(--ink-3)', fontWeight: 600, marginBottom: 9 }}>{title}</div>
      {children}
    </div>
  );
}
