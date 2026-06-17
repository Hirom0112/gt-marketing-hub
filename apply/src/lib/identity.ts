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

// ---------------------------------------------------------------------------
// Synthetic CHILD identity (R1 — the per-child `student` grain). A child is a
// `student` row under a household's `family_record`; it carries ONLY non-PII,
// synthetic-shaped labels (migration 0009: `synthetic_first_name`, `grade`,
// `display_label`). Names come from the same fixed word lists — never typed,
// never a real child's name (INV-1 / INV-6 / COPPA). No DOB, no precise geo.
// ---------------------------------------------------------------------------

// Grade BANDS only — mirror the leads_new grade_interest set (never a real DOB).
const CHILD_GRADES = ['K', '1', '2', '3', '4', '5', '6', '7', '8'] as const;

export interface SyntheticChild {
  /** Synthetic given name (word-list, never typed). */
  syntheticFirstName: string;
  /** Grade BAND only — never a DOB (INV-1/INV-6). */
  grade: string;
  /** Non-PII human display label for the per-child card. */
  displayLabel: string;
}

/**
 * Generate a fully synthetic child for the `student` grain. Every field is drawn
 * from the fixed word/grade lists — no input the user provides ever reaches it,
 * so a real child's name/DOB can never land here (INV-1 / INV-6 by shape).
 */
export function generateSyntheticChild(): SyntheticChild {
  const first = pick(FIRST_WORDS);
  const grade = pick(CHILD_GRADES);
  return {
    syntheticFirstName: first,
    grade,
    displayLabel: `${first} · Grade ${grade}`,
  };
}
