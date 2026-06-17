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
 * The seeded synthetic anon-session tokens for ONE demo family — the restore
 * credentials for `setSession`. SYNTHETIC ONLY (INV-1): these are the anon-session
 * tokens the director's seed minted per family; they are NOT a service_role key
 * and carry no privilege beyond that family's own RLS owner-scope (INV-5).
 */
export interface DemoSessionTokens {
  access_token: string;
  refresh_token: string;
}

/** uid → that family's seeded anon-session tokens. Demo/seed-time, synthetic-only. */
export type DemoSessions = Record<string, DemoSessionTokens>;

/**
 * The slice of the REAL anon Supabase client the demo swap depends on: just
 * `auth.setSession`. Typed structurally so `asDemoSupabase` can wrap a bare
 * `@supabase/supabase-js` client (whose `auth` IS a superset of this) WITHOUT
 * pulling the full client type into this module — and WITHOUT any service_role
 * surface (INV-5). The real client's `setSession` returns `{ error }`; we keep
 * only that field in the structural type.
 */
export interface DemoSessionClient extends MinimalSupabase {
  auth: MinimalSupabase['auth'] & {
    setSession: (tokens: DemoSessionTokens) => Promise<{
      error: { message: string } | null;
    }>;
  };
}

/**
 * Build a production `DemoSupabase` from the REAL anon client + the seeded
 * per-family session map. The returned object IS the client, extended with a
 * `signInAsUid` that restores the family's seeded anon session via
 * `auth.setSession` — after which `getSession()` reports that family's uid and
 * every owner-scoped read is RLS-scoped to it (no cross-family leak). Anon-only:
 * no `service_role`, no privilege beyond the synthetic tokens (INV-5/INV-1).
 *
 * A missing uid or a `setSession` error THROWS (never silently no-ops) — an
 * inconsistent seed/env must surface, not degrade quietly.
 */
export function asDemoSupabase(
  client: DemoSessionClient,
  sessions: DemoSessions,
): DemoSupabase {
  const demo: DemoSupabase = Object.assign(client, {
    async signInAsUid(uid: string): Promise<void> {
      const tokens = sessions[uid];
      if (!tokens) {
        throw new Error(
          `no seeded demo session for uid ${uid} — VITE_DEMO_SESSIONS is inconsistent with VITE_DEMO_FAMILIES`,
        );
      }
      const { error } = await client.auth.setSession({
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token,
      });
      if (error) {
        throw new Error(`demo session restore failed for uid ${uid}: ${error.message}`);
      }
    },
  });
  return demo;
}

/**
 * The seeded per-family anon-session tokens the production switcher restores from.
 * Mirrors `loadDemoFamilies`: the director's live-seed step mints one anon session
 * per demo family and writes a JSON OBJECT (uid → {access_token, refresh_token}) to
 * `VITE_DEMO_SESSIONS`. Consumed ONLY by `asDemoSupabase` (never shown in the UI),
 * synthetic-only (INV-1). When unset or malformed the map is empty (fail-safe) — the
 * switcher then has no DemoSupabase and renders its honest "no seeded families" state.
 */
export function loadDemoSessions(): DemoSessions {
  const raw = import.meta.env.VITE_DEMO_SESSIONS as string | undefined;
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return {};
    }
    const out: DemoSessions = {};
    for (const [uid, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (
        typeof value === 'object' &&
        value !== null &&
        typeof (value as DemoSessionTokens).access_token === 'string' &&
        typeof (value as DemoSessionTokens).refresh_token === 'string'
      ) {
        out[uid] = {
          access_token: (value as DemoSessionTokens).access_token,
          refresh_token: (value as DemoSessionTokens).refresh_token,
        };
      }
    }
    return out;
  } catch {
    return {};
  }
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
