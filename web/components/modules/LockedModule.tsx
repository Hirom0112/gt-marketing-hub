'use client';

// Shown when a role can't view a module (operator → Decision Queue). Fail-closed
// as a *state*, not an error: explain the rule and the allowed path.

import type { ModuleDef } from '@/lib/registry';

export function LockedModule({ def }: { def: ModuleDef }) {
  return (
    <section className="scr" style={{ padding: '48px 22px', maxWidth: 560 }}>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: 9, letterSpacing: '1px', color: 'var(--ink-3)', marginBottom: 12 }}>
        {def.idx} · {def.label.toUpperCase()}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: 9, fontWeight: 600, letterSpacing: '.5px', padding: '3px 8px', background: 'var(--accent-soft)', color: 'var(--ink-2)' }}>🔒 LEADERSHIP ONLY</span>
      </div>
      <h2 style={{ fontFamily: 'Fraunces', fontWeight: 700, fontSize: 22, letterSpacing: '-.4px', color: 'var(--ink)', margin: '0 0 10px' }}>
        The {def.title} is restricted
      </h2>
      <p style={{ color: 'var(--ink-2)', fontSize: 13, lineHeight: 1.65 }}>
        Only leadership can view and act on the full Decision Queue. As an operator you can still
        <b> submit</b> a decision, proposal, or budget ask <b>from your own module</b>. It lands here and
        you can track its status, but the queue itself stays leadership-only.
      </p>
    </section>
  );
}
