// Demo family-switcher + pages dropbox (MULTI_AGENT_COCKPIT §10.2) — the FAMILY
// FACE of the on-camera demo.
//
//   * A dropdown of the seeded synthetic families. Picking one signs into THAT
//     family's anon session (a session SWAP to the family's own auth.uid()) and
//     loads their four-lane status page. It EXTENDS the existing `resume_banner`
//     anon-resume — same anon-session mechanism, NOT real auth.
//   * A "pages dropbox" — quick links to apply flow · four-lane status · cockpit —
//     so the founder can jump on camera: "this parent made it this far" → "here's
//     what our dashboard shows".
//
// HONESTY + INVARIANTS: demo-only, synthetic-only (INV-1). Each family is its own
// seeded anon uid; RLS (deny-by-default, owner-scoped, null-guarded) is the only
// boundary, so signed in as family A you read ONLY family A's rows (no cross-family
// leak). The SPA uses ONLY the anon client (INV-5) — no service_role anywhere.

import { useState } from 'react';
import { COCKPIT_URL, type DemoFamily } from './lib/demo';

export function DemoSwitcher({
  families,
  onSelectFamily,
  onApplyFlow,
  onStatusPage,
  cockpitUrl = COCKPIT_URL,
  busy = false,
}: {
  /** The seeded synthetic demo cohort (loadDemoFamilies()). */
  families: DemoFamily[];
  /** Sign into the chosen family's anon session, then load its status page. */
  onSelectFamily: (family: DemoFamily) => void | Promise<void>;
  /** Pages-dropbox quick link: jump to the apply flow (a fresh application). */
  onApplyFlow: () => void;
  /** Pages-dropbox quick link: jump to the current session's four-lane status. */
  onStatusPage: () => void;
  /** The cockpit (admin/closer) URL; defaults to the configured COCKPIT_URL. */
  cockpitUrl?: string;
  /** Disable the controls while a session swap is in flight. */
  busy?: boolean;
}) {
  const [selectedUid, setSelectedUid] = useState('');

  function selectFamily() {
    const family = families.find((f) => f.uid === selectedUid);
    if (!family) return;
    void onSelectFamily(family);
  }

  return (
    <section className="demo-switcher" aria-label="demo_switcher">
      <div className="demo-switcher-head">
        <span className="demo-pill">Demo</span>
        <h3 className="demo-switcher-title">Demo control — sign in as a family</h3>
      </div>
      <p className="demo-switcher-note">
        Synthetic families only — this is a demo session swap (anon-resume), not
        real login. Each family sees only its own data.
      </p>

      <div className="demo-switcher-row">
        <label className="demo-switcher-field">
          <span className="demo-switcher-cap">Sign in as</span>
          <select
            aria-label="demo_family_select"
            value={selectedUid}
            onChange={(e) => setSelectedUid(e.target.value)}
            disabled={busy || families.length === 0}
          >
            <option value="">
              {families.length === 0
                ? 'No seeded families'
                : 'Choose a synthetic family…'}
            </option>
            {families.map((f) => (
              <option key={f.uid} value={f.uid}>
                {f.label}
                {f.hint ? ` — ${f.hint}` : ''}
              </option>
            ))}
          </select>
        </label>
        <button
          className="primary"
          aria-label="demo_sign_in"
          onClick={selectFamily}
          disabled={busy || selectedUid === ''}
        >
          {busy ? 'Signing in…' : 'View their status'}
        </button>
      </div>

      <nav className="pages-dropbox" aria-label="pages_dropbox">
        <span className="pages-dropbox-cap">Pages</span>
        <button
          className="pages-link"
          aria-label="page_apply_flow"
          onClick={onApplyFlow}
        >
          Apply flow
        </button>
        <button
          className="pages-link"
          aria-label="page_status"
          onClick={onStatusPage}
        >
          Four-lane status
        </button>
        <a
          className="pages-link"
          aria-label="page_cockpit"
          href={cockpitUrl}
          target="_blank"
          rel="noreferrer"
        >
          Cockpit
        </a>
      </nav>
    </section>
  );
}
