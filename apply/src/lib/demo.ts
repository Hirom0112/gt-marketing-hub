// Demo-only family-switcher + pages-dropbox config (MULTI_AGENT_COCKPIT §10.2).
//
// This is the FAMILY FACE of the on-camera demo: a dropdown of the seeded
// synthetic families so the founder can "log in as" one and show their four-lane
// status, paired with a small index of the deployed pages (apply flow · four-lane
// status · cockpit).
//
// HONESTY (CLAUDE INV-1 / THREAT_MODEL): this is NOT real auth. It EXTENDS the
// existing anon-resume (`resume_banner`) — each demo family is its OWN seeded
// anon-session user (its own auth.uid()), and selecting one swaps the active anon
// session to THAT family's uid. RLS (deny-by-default, owner-scoped, null-guarded)
// is still the only boundary: signed in as family A you can read ONLY family A's
// rows — there is no cross-family leak, exactly as in production. Every family is
// synthetic, `@example.invalid` (INV-1); no real principal, no privileged key.
//
// The SPA uses ONLY the anon client (INV-5) — `service_role` is server-only and
// never referenced here.

import type { MinimalSupabase } from './apply';

/**
 * One seeded synthetic demo family the founder can sign in as. The `uid` is the
 * family's OWN seeded anon-session auth.uid() (its RLS owner key); `label` is the
 * synthetic household name shown in the dropdown. Synthetic-only (INV-1).
 */
export interface DemoFamily {
  /** The family's own seeded anon-session uid (its RLS owner-scope key). */
  uid: string;
  /** The owning family_record id (so the status page can locate the household). */
  familyId: string;
  /** Synthetic household label shown in the dropdown (e.g. "Maple Household"). */
  label: string;
  /** A short hint of where this family is in the funnel, for the demo operator. */
  hint?: string;
}

/**
 * The demo-only session surface: the anon Supabase client PLUS a demo-only
 * "sign in as this seeded family" operation. The real implementation swaps the
 * active anon session to the family's seeded credentials; the mock swaps the
 * active uid so RLS-scoped reads return only that family's rows. It is a SUPERSET
 * of the anon `MinimalSupabase` — still no `service_role`, still anon-only.
 */
export interface DemoSupabase extends MinimalSupabase {
  /**
   * Swap the active anon session to the seeded family identified by `uid`. After
   * this resolves, `getSession()` reports `uid` and every owner-scoped read is
   * RLS-scoped to it — so the status page shows ONLY that family (no leak).
   */
  signInAsUid: (uid: string) => Promise<void>;
}

/** Narrow a `MinimalSupabase` to a `DemoSupabase` (the demo client adds the swap). */
export function isDemoSupabase(sb: MinimalSupabase): sb is DemoSupabase {
  return typeof (sb as Partial<DemoSupabase>).signInAsUid === 'function';
}

/**
 * The cockpit (admin/closer) URL for the "pages dropbox" quick-jump. INV-11: the
 * one canonical home is the `VITE_COCKPIT_URL` env var (TECH_STACK §5); this falls
 * back to a relative `/` only so the link renders in dev/test without the env set.
 * It is a link target, not a tunable threshold — no magic number.
 */
export const COCKPIT_URL: string =
  (import.meta.env.VITE_COCKPIT_URL as string | undefined) ?? '#cockpit';

/**
 * The seeded demo cohort the switcher lists. The director's live-seed step writes
 * the curated cohort to Supabase (one anon-session user per family) and supplies
 * the matching `VITE_DEMO_FAMILIES` (a JSON array of {uid, familyId, label,hint}).
 * Demo-only, synthetic-only (INV-1). When unset (dev/test without a seed) the list
 * is empty and the switcher renders its honest "no seeded families" state.
 */
export function loadDemoFamilies(): DemoFamily[] {
  const raw = import.meta.env.VITE_DEMO_FAMILIES as string | undefined;
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (f): f is DemoFamily =>
          typeof f === 'object' &&
          f !== null &&
          typeof (f as DemoFamily).uid === 'string' &&
          typeof (f as DemoFamily).familyId === 'string' &&
          typeof (f as DemoFamily).label === 'string',
      )
      .map((f) => ({
        uid: f.uid,
        familyId: f.familyId,
        label: f.label,
        hint: typeof f.hint === 'string' ? f.hint : undefined,
      }));
  } catch {
    return [];
  }
}
