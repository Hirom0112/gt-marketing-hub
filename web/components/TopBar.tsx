'use client';

// Top bar: active module title + data source, the live Fall-cutoff countdown
// (Aug 17 2026), the week-of / sprint marker, and the current user. Below it,
// the app-wide data-confidence banner (Supabase ⇄ HubSpot parity) that links to
// CRM Ops — shown on every module that consumes HubSpot data.

import Link from 'next/link';
import { useEffect, useState } from 'react';
import type { ModuleDef } from '@/lib/registry';
import { useSession } from '@/lib/session';

const MONO = 'JetBrains Mono';
const CUTOFF = Date.UTC(2026, 7, 17, 4, 0, 0); // Aug 17 2026

function useCountdown() {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (now === null) return null;
  let s = Math.max(0, Math.floor((CUTOFF - now) / 1000));
  const dd = Math.floor(s / 86400); s -= dd * 86400;
  const hh = Math.floor(s / 3600); s -= hh * 3600;
  const mm = Math.floor(s / 60); s -= mm * 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return { dd, hh: pad(hh), mm: pad(mm), ss: pad(s) };
}

export function TopBar({ active }: { active?: ModuleDef }) {
  const { session } = useSession();
  const cd = useCountdown();
  const initials = session.userName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
  const isOperatorReadonly = session.role === 'operator' && active && !session.ownedModules.includes(active.id) && active.id !== 'home';

  return (
    <>
      <header style={{ display: 'flex', alignItems: 'stretch', borderBottom: '1px solid var(--line-2)', background: 'var(--card)', minHeight: 58 }}>
        <div style={{ flex: 1, padding: '9px 22px', display: 'flex', flexDirection: 'column', justifyContent: 'center', borderRight: '1px solid var(--line)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 style={{ margin: 0, fontFamily: 'Fraunces', fontWeight: 700, fontSize: 18, letterSpacing: '-.3px', color: 'var(--ink)' }}>
              {active?.title ?? 'Executive Command Center'}
            </h1>
            {isOperatorReadonly && (
              <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.6px', padding: '2px 6px', border: '1px solid var(--line-2)', color: 'var(--ink-3)' }}>
                READ-ONLY
              </span>
            )}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: '.4px', color: 'var(--ink-3)', marginTop: 4 }}>
            ⌖ {active?.source ?? ''}
          </div>
        </div>

        <div style={{ padding: '8px 20px', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'flex-end', borderRight: '1px solid var(--line)', minWidth: 236 }}>
          <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.8px', color: 'var(--signal)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--signal)', animation: 'blink 1.6s infinite' }} />
            FALL ENROLLMENT CUTOFF · AUG 17
          </div>
          <div style={{ fontFamily: MONO, fontWeight: 600, fontSize: 20, color: 'var(--ink)', letterSpacing: '.5px', marginTop: 3, fontVariantNumeric: 'tabular-nums' }}>
            {cd ? (
              <>
                {cd.dd}<span style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 1 }}>d</span> {cd.hh}<span style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 1 }}>h</span> {cd.mm}<span style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 1 }}>m</span> {cd.ss}<span style={{ fontSize: 10, color: 'var(--ink-3)', marginLeft: 1 }}>s</span>
              </>
            ) : (
              <span style={{ color: 'var(--ink-3)' }}>··</span>
            )}
          </div>
        </div>

        <div style={{ padding: '8px 18px', display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 172 }}>
          <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: '.6px', color: 'var(--ink-3)' }}>WEEK OF JUN 22 · SPRINT P2/5</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
            <span style={{ width: 24, height: 24, borderRadius: '50%', background: 'var(--gold)', color: 'var(--on-brand)', fontFamily: 'Fraunces', fontWeight: 700, fontSize: 10, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{initials}</span>
            <div style={{ lineHeight: 1.2 }}>
              <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink)' }}>{session.userName}</div>
              <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>{session.userRole}</div>
            </div>
          </div>
        </div>
      </header>

      {/* App-wide data-confidence banner */}
      <DataConfidenceBanner />
    </>
  );
}

function DataConfidenceBanner() {
  const [hover, setHover] = useState(false);
  return (
    <Link href="/crm" style={{ display: 'block' }}>
      <div
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '7px 22px',
          background: 'var(--signal-soft)',
          borderBottom: '1px solid var(--signal)',
          boxShadow: hover ? 'inset 0 0 0 1px var(--signal)' : 'none',
          transition: 'box-shadow .15s var(--ease)',
        }}
      >
        <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.5px', padding: '2px 7px', background: 'var(--signal)', color: 'var(--on-signal)', whiteSpace: 'nowrap' }}>⚠ DATA CONFIDENCE</span>
        <span style={{ fontSize: 12, color: 'var(--ink)', flex: 1 }}>
          Supabase ⇄ HubSpot sync parity <b style={{ fontFamily: MONO }}>96.2%</b>, with <b>income</b>, <b>source</b> &amp; <b>TEFA</b> fields below threshold. Funnel &amp; income read from <b>Supabase app_form</b> (source of truth). UTM attribution remains broken.
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10, color: 'var(--signal)', fontWeight: 600, whiteSpace: 'nowrap', textDecoration: hover ? 'underline' : 'none' }}>Inspect in CRM Ops →</span>
      </div>
    </Link>
  );
}
