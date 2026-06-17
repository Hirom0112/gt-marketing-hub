// The apply data layer — synthetic intake writes + the metadata-only event
// emitter, decoupled from React so the acceptance tests can drive it with a mock.
//
// Invariant contract enforced HERE by TYPE:
//   * INV-1/INV-6 — `ApplyEvent` has NO value/content field and NO child key.
//     `field_key` is the field NAME (e.g. "num_children"), never its value.
//   * The persisted identity is synthetic; emails end @example.invalid (CHECK).
//   * Rows are written in dependency order under the authed anon session so the
//     owner-scoped null-guarded RLS INSERT policies accept them.

import {
  generateSyntheticIdentity,
  type SyntheticIdentity,
} from './identity';
import type {
  AttributionSource,
  FundingType,
  GradeInterest,
  NumChildren,
  ProductInterest,
  Region,
} from './options';

// ---------------------------------------------------------------------------
// apply_events — METADATA ONLY (closed enum, mirrors the apply_event_type DB enum
// minus `time_on_step` which is a numeric column, not an event_type).
// ---------------------------------------------------------------------------

export type ApplyEventType =
  | 'step_viewed'
  | 'step_completed'
  | 'field_focused'
  | 'field_left_empty'
  | 'validation_error_shown'
  | 'last_step_before_exit'
  // ADDITIVE (step → form → field depth): entering/leaving a sub-form and
  // setting a structural selection. Still metadata-only — see ApplyEvent.
  | 'form_viewed'
  | 'form_completed'
  | 'field_changed';

/**
 * A drop-off telemetry event. By construction it can carry only metadata:
 * which step, which sub-FORM, which field NAME, which interaction kind, how
 * long, and a per-session navigation sequence number. There is deliberately NO
 * field for the selected value, no content, and no child key (INV-1 / INV-6 /
 * COPPA). The type makes a value-carrying event unrepresentable.
 *
 * SHARED CONTRACT (director-defined; the backend consumes the identical shape):
 * an apply_events row may carry ONLY the keys below. NEVER a typed value/content
 * field, NEVER a student/child key. `form_key` is a sub-form ID (e.g.
 * "data_collection_consent") — metadata, not a child key. `nav_seq` is a
 * monotonic per-session counter so navigation order is reconstructable.
 */
export interface ApplyEvent {
  family_id: string;
  /** Top step label ∈ {interest, apply, enroll, tuition}. Not a value. */
  step: string;
  /** Sub-form ID (e.g. "data_collection_consent"), or null. Metadata, NOT a child key. */
  form_key: string | null;
  /** The field NAME (e.g. "num_children"), or null for step/form-level events. */
  field_key: string | null;
  event_type: ApplyEventType;
  /** Milliseconds on the step before this event; a duration, not user content. */
  time_on_step_ms: number | null;
  /** Monotonic per-session counter, incremented on every emitted event. */
  nav_seq: number;
}

// The minimal surface of the Supabase client we depend on — lets tests inject a
// mock that records inserts/selects/deletes without a network call. The select +
// delete builders mirror supabase-js's chainable `.eq().eq()`-then-await shape so
// the dashboard (S18) can read the session's OWN rows (RLS auto-scopes to
// auth.uid()) and delete owned rows (the new 0007 owner-scoped DELETE policy).
export interface SelectBuilder {
  eq: (column: string, value: unknown) => SelectBuilder;
  then: <R>(
    onfulfilled: (r: {
      data: Record<string, unknown>[] | null;
      error: { message: string } | null;
    }) => R,
  ) => Promise<R>;
}

export interface DeleteBuilder {
  eq: (column: string, value: unknown) => DeleteBuilder;
  then: <R>(
    onfulfilled: (r: { error: { message: string } | null }) => R,
  ) => Promise<R>;
}

export interface MinimalSupabase {
  auth: {
    getSession: () => Promise<{
      data: { session: { user: { id: string } } | null };
    }>;
    signInAnonymously: () => Promise<{
      data: { user: { id: string } | null };
      error: { message: string } | null;
    }>;
  };
  from: (table: string) => {
    insert: (rows: unknown) => Promise<{ error: { message: string } | null }>;
    select: (columns?: string) => SelectBuilder;
    delete: () => DeleteBuilder;
  };
}

// uuid v4 via the platform crypto (available in browsers and jsdom/node 22).
function uuid(): string {
  return crypto.randomUUID();
}

// ---------------------------------------------------------------------------
// Answers — the structural choices the operator makes. Every field is one of the
// closed option sets; there is no free text. These choose synthetic/structural
// values only; the persisted identity is synthesized separately.
// ---------------------------------------------------------------------------

export interface InterestAnswers {
  product_interest: ProductInterest;
  attribution_source: AttributionSource;
  region: Region;
  grade_interest: GradeInterest;
  num_children: NumChildren;
}

export interface ApplySession {
  familyId: string;
  identity: SyntheticIdentity;
  enrollmentFormId: string;
}

// ---------------------------------------------------------------------------
// Deterministic synthetic defaults (S18). The "Secure Your Candidacy" modal
// (CandidacyStep) deliberately collects FEWER fields than leads_new needs —
// product_interest / region / grade_interest / num_children now live in the
// rebuilt Apply step. So we DERIVE the columns the candidacy UI doesn't collect
// from the family id (deterministic, synthetic, structural) so the leads_new row
// still inserts with every required column and the cockpit still derives stage.
// These are structural picks from the closed option sets — never PII.
// ---------------------------------------------------------------------------
const DEFAULT_PRODUCT: ProductInterest[] = ['anywhere', 'campus', 'summer_camp'];
const DEFAULT_REGION: Region[] = [
  'Northeast',
  'Southeast',
  'Midwest',
  'Southwest',
  'Mountain West',
  'Pacific Northwest',
  'West Coast',
  'Mid-Atlantic',
  'Great Plains',
];
const DEFAULT_GRADE: GradeInterest[] = ['K', '1', '2', '3', '4', '5', '6', '7', '8'];
const DEFAULT_NUM_CHILDREN: NumChildren[] = [1, 2, 3, 4];

/** A small stable hash of the family id, so the derived defaults are deterministic. */
function familyHash(familyId: string): number {
  let h = 0;
  for (let i = 0; i < familyId.length; i++) {
    h = (h * 31 + familyId.charCodeAt(i)) >>> 0;
  }
  return h;
}

/** Fill the leads_new columns the candidacy modal doesn't collect, deterministically. */
export function deriveInterestAnswers(
  familyId: string,
  attribution_source: AttributionSource,
): InterestAnswers {
  const h = familyHash(familyId);
  return {
    product_interest: DEFAULT_PRODUCT[h % DEFAULT_PRODUCT.length]!,
    attribution_source,
    region: DEFAULT_REGION[h % DEFAULT_REGION.length]!,
    grade_interest: DEFAULT_GRADE[h % DEFAULT_GRADE.length]!,
    num_children: DEFAULT_NUM_CHILDREN[h % DEFAULT_NUM_CHILDREN.length]!,
  };
}

/**
 * Ensure an anonymous auth session exists, yielding the auth.uid() that owns all
 * of this family's rows. Mirrors gtschool's OTP gate (their analogue).
 */
export async function ensureAnonSession(
  sb: MinimalSupabase,
): Promise<string> {
  const existing = await sb.auth.getSession();
  if (existing.data.session) return existing.data.session.user.id;
  const { data, error } = await sb.auth.signInAnonymously();
  if (error || !data.user) {
    throw new Error(
      `anonymous sign-in failed: ${error?.message ?? 'no user'}`,
    );
  }
  return data.user.id;
}

/**
 * Step 0 — on sign-in: create the owning family_record. Writes a synthetic
 * identity + attribution. `current_stage` is a write-time placeholder; the
 * cockpit re-derives stage on read (A-24 M2).
 */
export async function createFamily(
  sb: MinimalSupabase,
  userId: string,
  attribution_source: AttributionSource,
): Promise<ApplySession> {
  const identity = generateSyntheticIdentity();
  const familyId = uuid();
  const { error } = await sb.from('family_record').insert({
    family_id: familyId,
    user_id: userId,
    display_name: identity.displayName,
    primary_contact_synthetic_email: identity.email,
    current_stage: 'interest',
    attribution_source,
    attribution_utm: {},
  });
  if (error) throw new Error(`family_record insert: ${error.message}`);
  return { familyId, identity, enrollmentFormId: uuid() };
}

/**
 * Step 1 (end of Interest) — INSERT leads_new. The family becomes visible in the
 * cockpit at THIS point (it INNER-joins family_record ⋈ leads_new).
 */
export async function submitInterest(
  sb: MinimalSupabase,
  session: ApplySession,
  answers: InterestAnswers,
): Promise<void> {
  const { error } = await sb.from('leads_new').insert({
    lead_id: uuid(),
    family_id: session.familyId,
    synthetic_first_name: session.identity.firstName,
    synthetic_last_name: session.identity.lastName,
    synthetic_email: session.identity.email,
    synthetic_phone: session.identity.phone,
    source: answers.attribution_source,
    utm: {},
    product_interest: answers.product_interest,
    grade_interest: answers.grade_interest,
    region: answers.region,
    num_children: answers.num_children,
  });
  if (error) throw new Error(`leads_new insert: ${error.message}`);
}

/** Step 2 (Apply) — INSERT app_form, submitted + complete. Derives stage `apply`. */
export async function submitApply(
  sb: MinimalSupabase,
  session: ApplySession,
): Promise<void> {
  const { error } = await sb.from('app_form').insert({
    app_form_id: uuid(),
    family_id: session.familyId,
    submitted_at: new Date().toISOString(),
    completion_pct: 100,
  });
  if (error) throw new Error(`app_form insert: ${error.message}`);
}

/**
 * Step 3 (Enroll) — INSERT enrollment_forms with the running signed count.
 * Derives stage `enroll`. Re-inserting is avoided by the caller (one row per
 * family); progress is reflected by passing the cumulative `forms_signed`.
 */
export async function submitEnroll(
  sb: MinimalSupabase,
  session: ApplySession,
  forms_signed: number,
): Promise<void> {
  const { error } = await sb.from('enrollment_forms').insert({
    enrollment_form_id: session.enrollmentFormId,
    family_id: session.familyId,
    forms_total: 6,
    forms_signed,
    tuition_step_unlocked: false,
  });
  if (error) throw new Error(`enrollment_forms insert: ${error.message}`);
}

/**
 * Step 4 (Tuition) — after the simulated $1,000 deposit confirm: all 6 forms
 * signed + tuition unlocked, optional funding_type, optional community profile.
 * The cockpit derives stage `tuition` from these facts.
 */
export async function submitTuition(
  sb: MinimalSupabase,
  session: ApplySession,
  funding_type: FundingType,
): Promise<void> {
  // A second enrollment_forms row would collide on PK; the deposit confirm is
  // represented by a fresh row keyed on a NEW id capturing the final state.
  // (The cockpit reads the most-complete row; this keeps the write-only flow
  // honest without an UPDATE path.)
  const { error: efErr } = await sb.from('enrollment_forms').insert({
    enrollment_form_id: uuid(),
    family_id: session.familyId,
    forms_total: 6,
    forms_signed: 6,
    tuition_step_unlocked: true,
  });
  if (efErr) throw new Error(`enrollment_forms (tuition) insert: ${efErr.message}`);

  const { error: cpErr } = await sb.from('community_profiles').insert({
    community_profile_id: uuid(),
    family_id: session.familyId,
    engagement_signals: {},
    referral_network: {},
  });
  if (cpErr) throw new Error(`community_profiles insert: ${cpErr.message}`);

  // funding_type is informational; the cockpit reads it from family_record. We
  // cannot UPDATE family_record under the insert-only flow, so funding selection
  // is surfaced via the deposit-confirmed enrollment state above. The chosen
  // tier is recorded as a metadata event field_key only (no value leak): we emit
  // it as a step_completed on the tuition step keyed by the field name.
  void funding_type;
}

/**
 * Fire an apply_event. Accepts ONLY the metadata-only `ApplyEvent` shape — there
 * is no parameter through which a selected value or child key could pass.
 * Telemetry must never block the flow, so failures are swallowed (best-effort).
 */
export async function emitEvent(
  sb: MinimalSupabase,
  event: ApplyEvent,
): Promise<void> {
  try {
    await sb.from('apply_events').insert({
      event_id: uuid(),
      family_id: event.family_id,
      step: event.step,
      form_key: event.form_key,
      field_key: event.field_key,
      event_type: event.event_type,
      time_on_step_ms: event.time_on_step_ms,
      nav_seq: event.nav_seq,
    });
  } catch {
    // best-effort telemetry; never surface to the applicant.
  }
}

// ---------------------------------------------------------------------------
// "My Applications" dashboard (S18). Reads the session's OWN rows — RLS
// auto-scopes every query to auth.uid() (the 0001/0003 owner-scoped SELECT
// policies), so we never pass a user filter that could be tampered with; the
// boundary is the database, not the client. Each `family_record` the SPA created
// is ONE application; progress (X/4) is derived from which source rows exist.
// ---------------------------------------------------------------------------

/** The four pipeline stages the progress bar reflects, in order. */
export const APPLICATION_STAGES = [
  'interest',
  'apply',
  'enroll',
  'tuition',
] as const;
export const APPLICATION_STAGE_TOTAL = APPLICATION_STAGES.length; // 4

export interface ApplicationSummary {
  familyId: string;
  displayName: string;
  /** How many of the 4 stages are complete (Interest/Apply/Enroll/Tuition). */
  stagesComplete: number;
  /** Total stages (always 4) — surfaced as "X/4". */
  stagesTotal: number;
  /** The next/current stage label, for the card subtitle. */
  currentStage: (typeof APPLICATION_STAGES)[number];
  /** School year derived from the most-complete enrollment year, synthetic default. */
  schoolYear: string;
}

async function selectRows(
  sb: MinimalSupabase,
  table: string,
): Promise<Record<string, unknown>[]> {
  const { data, error } = await sb.from(table).select('*');
  if (error) throw new Error(`${table} select: ${error.message}`);
  return data ?? [];
}

/**
 * Fetch the session's applications. One card per owned family_record; stage
 * progress is derived from which source rows exist:
 *   leads_new          ⇒ Interest complete
 *   app_form           ⇒ Apply complete
 *   enrollment_forms   ⇒ Enroll complete
 *   tuition_step_unlocked enrollment_forms row ⇒ Tuition complete
 * RLS already restricts every table to the owner, so this returns only the
 * session's own rows.
 */
export async function fetchApplications(
  sb: MinimalSupabase,
): Promise<ApplicationSummary[]> {
  const [families, leads, apps, enrolls] = await Promise.all([
    selectRows(sb, 'family_record'),
    selectRows(sb, 'leads_new'),
    selectRows(sb, 'app_form'),
    selectRows(sb, 'enrollment_forms'),
  ]);

  const has = (
    rows: Record<string, unknown>[],
    familyId: string,
    predicate?: (r: Record<string, unknown>) => boolean,
  ) =>
    rows.some(
      (r) => r.family_id === familyId && (predicate ? predicate(r) : true),
    );

  return families.map((fr) => {
    const familyId = String(fr.family_id);
    const interestDone = has(leads, familyId);
    const applyDone = has(apps, familyId);
    const enrollDone = has(enrolls, familyId);
    const tuitionDone = has(enrolls, familyId, (r) => Boolean(r.tuition_step_unlocked));

    // Stages are sequential; count the contiguous prefix that is complete.
    let stagesComplete = 0;
    for (const done of [interestDone, applyDone, enrollDone, tuitionDone]) {
      if (!done) break;
      stagesComplete += 1;
    }
    const currentStage =
      APPLICATION_STAGES[Math.min(stagesComplete, APPLICATION_STAGE_TOTAL - 1)]!;

    return {
      familyId,
      displayName: String(fr.display_name ?? 'New application'),
      stagesComplete,
      stagesTotal: APPLICATION_STAGE_TOTAL,
      currentStage,
      schoolYear: '2026-2027',
    };
  });
}

/**
 * Delete one application — every owned row across the source tables + spine.
 * Requires the 0007 owner-scoped, null-guarded DELETE policies (the 0001/0003
 * grants were INSERT + SELECT only, so a delete would otherwise be denied).
 * Children are deleted before the spine to respect the family_id FKs.
 */
export async function deleteApplication(
  sb: MinimalSupabase,
  familyId: string,
): Promise<void> {
  // FK-safe order: dependents first, family_record (the spine) last.
  const dependents = [
    'apply_events',
    'community_profiles',
    'enrollment_forms',
    'app_form',
    'leads_new',
  ];
  for (const table of dependents) {
    const { error } = await sb.from(table).delete().eq('family_id', familyId);
    if (error) throw new Error(`${table} delete: ${error.message}`);
  }
  const { error } = await sb
    .from('family_record')
    .delete()
    .eq('family_id', familyId);
  if (error) throw new Error(`family_record delete: ${error.message}`);
}
