// Runtime config for the front end.
//
// `apiBaseUrl` is the base URL the React app calls (TECH_STACK §5.1
// GT_API_BASE_URL, injected at build as VITE_GT_API_BASE_URL). No real
// API calls are made yet — this is the seam later slices read from.
export const DEFAULT_API_BASE_URL = 'http://localhost:8000';

export const apiBaseUrl: string =
  import.meta.env.VITE_GT_API_BASE_URL ?? DEFAULT_API_BASE_URL;

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
