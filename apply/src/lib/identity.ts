// Synthetic-identity generator (INV-1 / INV-6 — non-negotiable).
//
// The form NEVER collects a real name, email, phone, or DOB. When a family is
// persisted we synthesize an identity from fixed word lists. The email MUST end
// in `@example.invalid` — there is a DB CHECK constraint
// (`family_record_synthetic_email` / `leads_new_synthetic_email`) that REJECTS any
// non-synthetic domain, so this is enforced both client-side here and server-side
// in Postgres (defense-in-depth).

const FIRST_WORDS = [
  'Maple',
  'River',
  'Cedar',
  'Willow',
  'Aspen',
  'Juniper',
  'Sage',
  'Birch',
  'Hazel',
  'Linden',
  'Marigold',
  'Sparrow',
] as const;

const LAST_WORDS = [
  'Household',
  'Family',
  'Home',
  'Residence',
  'Cottage',
  'Loft',
  'Hollow',
  'Commons',
] as const;

export interface SyntheticIdentity {
  /** Synthetic household display name, e.g. "Maple Household". */
  displayName: string;
  /** Synthetic first name (word-list, never typed). */
  firstName: string;
  /** Synthetic last name (word-list, never typed). */
  lastName: string;
  /** MUST end in @example.invalid (DB CHECK constraint). */
  email: string;
  /** Fake phone — synthetic, never collected. */
  phone: string;
  /** Synthetic ZIP — never a real postal code; shown read-only in the candidacy modal. */
  zip: string;
}

function pick<T>(arr: readonly T[]): T {
  // Non-empty arrays above; index is always in range.
  return arr[Math.floor(Math.random() * arr.length)] as T;
}

/**
 * Generate a fully synthetic identity. The email is GUARANTEED to end in
 * `@example.invalid`; no input the user provides ever reaches any field here.
 */
export function generateSyntheticIdentity(): SyntheticIdentity {
  const first = pick(FIRST_WORDS);
  const last = pick(LAST_WORDS);
  // A short random suffix keeps emails unique across submissions without any
  // user-derived data.
  const suffix = Math.random().toString(36).slice(2, 8);
  const localPart = `${first}.${last}.${suffix}`.toLowerCase();
  return {
    displayName: `${first} ${last}`,
    firstName: first,
    lastName: last,
    email: `${localPart}@example.invalid`,
    // Reserved 555-0100..555-0199 fake range; never a collected number.
    phone: `+1-555-01${String(Math.floor(Math.random() * 100)).padStart(2, '0')}`,
    // Synthetic ZIP in the reserved 00000-range pattern; never a real postal code
    // and never collected — it exists only to populate the candidacy modal's
    // read-only Zip field with structural-looking text.
    zip: `0000${Math.floor(Math.random() * 10)}`,
  };
}

/** The reserved synthetic email domain, asserted by tests + the DB CHECK. */
export const SYNTHETIC_EMAIL_DOMAIN = '@example.invalid';
