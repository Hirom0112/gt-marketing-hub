// Runtime config for the front end.
//
// `apiBaseUrl` is the base URL the React app calls (TECH_STACK §5.1
// GT_API_BASE_URL, injected at build as VITE_GT_API_BASE_URL). No real
// API calls are made yet — this is the seam later slices read from.
export const DEFAULT_API_BASE_URL = 'http://localhost:8000';

export const apiBaseUrl: string =
  import.meta.env.VITE_GT_API_BASE_URL ?? DEFAULT_API_BASE_URL;

// ---------------------------------------------------------------------------
// Verified-principal auth header (B1). The login gate trades a chosen SEAT for a
// REAL signed JWT minted by the backend's `POST /auth/demo-token`, and every
// cockpit API call carries it as `Authorization: Bearer <token>`. The backend's
// get_principal verifies the signature and trusts ONLY `app_metadata.role` — it
// no longer reads any client-spelled header. This REPLACES the old spoofable
// client-spelled role/agent principal (it could be forged by hand); the signed
// token cannot be tampered with. The token is synthetic-data-scoped and
// carries NO service_role secret (INV-5).
import { loadSession } from './LoginPage';

/** Build the bearer auth header from the currently stored seat's token. Returns
 *  an empty object when no seat (and so no token) is stored — the login gate is
 *  the only surface that renders without a seat. An expired token is NOT scrubbed
 *  here on purpose: it simply 401s server-side and the user re-enters via the
 *  login gate (see B1 expiry note in LoginPage). */
export function authHeaders(): Record<string, string> {
  const session = loadSession();
  if (session === null || !session.token) return {};
  return { Authorization: `Bearer ${session.token}` };
}

/** A thin fetch wrapper that prefixes `apiBaseUrl` and merges the bearer auth
 *  header onto every cockpit API call. Call as `apiFetch('/work-queue')` or
 *  `apiFetch('/seam/x/reconcile', { method: 'POST', ... })`. The resolved
 *  `(url, init)` shape matches a plain `fetch` so existing call-site behavior
 *  and tests are unchanged — this only adds the `Authorization: Bearer` header. */
export function apiFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const merged: RequestInit = {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  };
  return fetch(`${apiBaseUrl}${path}`, merged);
}

// HubSpot portal deep-link base (S10 W3 capture panel). The cockpit surfaces the
// live Deal / Contact / Note ids returned by the seed + approve routes as
// click-through deep links into the real portal — "✓ captured in HubSpot." The
// portal id is the live demo portal (246504420); overridable at build via
// VITE_GT_HUBSPOT_PORTAL_ID so it is portable to GT's real portal.
const DEFAULT_HUBSPOT_PORTAL_ID = '246504420';

export const hubspotPortalId: string =
  import.meta.env.VITE_GT_HUBSPOT_PORTAL_ID ?? DEFAULT_HUBSPOT_PORTAL_ID;

const HUBSPOT_RECORD_BASE = `https://app-na2.hubspot.com/contacts/${hubspotPortalId}/record`;

// Deep links to a live HubSpot record by object type id (0-1 contact, 0-3 deal,
// 0-46 note) — the proof-of-capture links the capture panel renders.
export function hubspotDealUrl(dealId: string): string {
  return `${HUBSPOT_RECORD_BASE}/0-3/${dealId}`;
}

export function hubspotContactUrl(contactId: string): string {
  return `${HUBSPOT_RECORD_BASE}/0-1/${contactId}`;
}

export function hubspotNoteUrl(noteId: string): string {
  return `${HUBSPOT_RECORD_BASE}/0-46/${noteId}`;
}
