// Unit tests for the apply data layer — the INV-1/INV-6 guardrail at the source.

import { describe, expect, it } from 'vitest';
import {
  addStudent,
  createFamily,
  deleteApplication,
  deriveInterestAnswers,
  emitEvent,
  ensureAnonSession,
  fetchApplications,
  submitApply,
  submitEnroll,
  submitInterest,
  submitTuition,
  type ApplySession,
  type ApplyEvent,
} from '../lib/apply';
import {
  ATTRIBUTION_SOURCE,
  GRADE_INTEREST,
  NUM_CHILDREN,
  PRODUCT_INTEREST,
  REGION,
} from '../lib/options';
import {
  generateSyntheticIdentity,
  SYNTHETIC_EMAIL_DOMAIN,
} from '../lib/identity';
import { makeMockSupabase } from './mockSupabase';

// The set of keys an apply_events row may ever carry. NO "value", no "content",
// no child key. `form_key` (a sub-form ID, e.g. "data_collection_consent") and
// `nav_seq` (a monotonic per-session counter) are metadata-only and allowed. If
// a future change adds a value/content/child key, this test fails. (SHARED
// CONTRACT — the backend consumes the identical allowed-key set.)
const ALLOWED_EVENT_KEYS = new Set([
  'event_id',
  'family_id',
  'step',
  'form_key',
  'field_key',
  'event_type',
  'time_on_step_ms',
  'nav_seq',
]);

// Substrings that would indicate a leaked selected value or child identity.
// NOTE: `form_key` is allowed metadata — "form" is deliberately NOT forbidden.
const FORBIDDEN_SUBSTRINGS = ['value', 'content', 'child', 'student', 'dob'];

describe('synthetic identity (INV-1)', () => {
  it('always generates an @example.invalid email', () => {
    for (let i = 0; i < 200; i++) {
      const id = generateSyntheticIdentity();
      expect(id.email.endsWith(SYNTHETIC_EMAIL_DOMAIN)).toBe(true);
    }
  });

  it('never includes a child/student/DOB field', () => {
    const id = generateSyntheticIdentity() as unknown as Record<
      string,
      unknown
    >;
    for (const key of Object.keys(id)) {
      expect(['child', 'student', 'dob']).not.toContain(key.toLowerCase());
    }
  });
});

async function walk(): Promise<ReturnType<typeof makeMockSupabase>> {
  const sb = makeMockSupabase();
  const uid = await ensureAnonSession(sb);
  const session = await createFamily(sb, uid, 'referral');
  const answers = {
    product_interest: 'anywhere',
    attribution_source: 'referral',
    region: 'Southwest',
    grade_interest: '3',
    num_children: 2,
  } as const;
  await submitInterest(sb, session, answers);
  await submitApply(sb, session);
  await submitEnroll(sb, session, 6);
  await submitTuition(sb, session, 'self_pay');
  await emitEvent(sb, {
    family_id: session.familyId,
    step: 'interest',
    form_key: null,
    field_key: 'num_children',
    event_type: 'step_completed',
    time_on_step_ms: 4200,
    nav_seq: 1,
  });
  // A sub-form event proving form_key + the new event types are metadata-only.
  await emitEvent(sb, {
    family_id: session.familyId,
    step: 'enroll',
    form_key: 'data_collection_consent',
    field_key: 'signature',
    event_type: 'field_changed',
    time_on_step_ms: 1200,
    nav_seq: 2,
  });
  return sb;
}

describe('intake write order + RLS-compatible shapes', () => {
  it('writes family_record first, then leads_new, in dependency order', async () => {
    const sb = await walk();
    const tables = sb.inserts.map((i) => i.table);
    expect(tables[0]).toBe('family_record');
    expect(tables.indexOf('family_record')).toBeLessThan(
      tables.indexOf('leads_new'),
    );
  });

  it('every persisted email ends @example.invalid', async () => {
    const sb = await walk();
    const fr = sb.rowsFor('family_record')[0]!;
    const lead = sb.rowsFor('leads_new')[0]!;
    expect(
      String(fr.primary_contact_synthetic_email).endsWith(
        SYNTHETIC_EMAIL_DOMAIN,
      ),
    ).toBe(true);
    expect(
      String(lead.synthetic_email).endsWith(SYNTHETIC_EMAIL_DOMAIN),
    ).toBe(true);
  });

  it('family_record.user_id is the authed uid (RLS owner-scope)', async () => {
    const sb = await walk();
    const fr = sb.rowsFor('family_record')[0]!;
    expect(fr.user_id).toBe(sb.uid);
  });

  it('leads_new carries num_children (the value term, A-23)', async () => {
    const sb = await walk();
    const lead = sb.rowsFor('leads_new')[0]!;
    expect(lead.num_children).toBe(2);
  });
});

describe('apply_events are metadata-only (INV-1/INV-6/COPPA)', () => {
  it('only emits allowed keys and never a value/content/child key', async () => {
    const sb = await walk();
    const events = sb.rowsFor('apply_events');
    expect(events.length).toBeGreaterThan(0);
    for (const ev of events) {
      for (const key of Object.keys(ev)) {
        expect(ALLOWED_EVENT_KEYS.has(key)).toBe(true);
        for (const bad of FORBIDDEN_SUBSTRINGS) {
          expect(key.toLowerCase()).not.toContain(bad);
        }
      }
    }
  });

  it('field_key is a field NAME, never the chosen value', async () => {
    const sb = await walk();
    const ev = sb
      .rowsFor('apply_events')
      .find((e) => e.field_key !== null)!;
    // The recorded field_key matches a known field name, not an option token.
    expect(ev.field_key).toBe('num_children');
    // It must NOT be one of the value tokens the user actually selected.
    expect(['anywhere', 'referral', 'Southwest', '3', 2]).not.toContain(
      ev.field_key,
    );
  });

  it('the ApplyEvent type cannot carry a value (compile-time + runtime)', async () => {
    // Constructing a well-typed event yields exactly the metadata fields —
    // including the new form_key + nav_seq, and NOTHING value-bearing.
    const ev: ApplyEvent = {
      family_id: 'f',
      step: 'enroll',
      form_key: 'tuition_agreement',
      field_key: 'billing_cadence',
      event_type: 'field_changed',
      time_on_step_ms: 10,
      nav_seq: 3,
    };
    expect(Object.keys(ev).sort()).toEqual(
      [
        'event_type',
        'family_id',
        'field_key',
        'form_key',
        'nav_seq',
        'step',
        'time_on_step_ms',
      ].sort(),
    );
  });
});

describe('submitTuition (R2 — persist funding_type)', () => {
  it('UPDATEs family_record.funding_type, owner-scoped by family_id', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');
    await submitTuition(sb, session, 'tefa_standard');

    const upd = sb.updates.find(
      (u) => u.table === 'family_record' && 'funding_type' in u.values,
    )!;
    expect(upd).toBeDefined();
    expect(upd.filter.family_id).toBe(session.familyId);
    expect(upd.values.funding_type).toBe('tefa_standard');
    // It persisted onto the row the cockpit reads.
    expect(sb.rowsFor('family_record')[0]!.funding_type).toBe('tefa_standard');
    // Fail-closed (INV-10): the SPA never advances funding_state itself.
    expect(upd.values.funding_state).toBeUndefined();
  });

  it('surfaces a funding_type update failure to the caller', async () => {
    const sb = makeMockSupabase({ failUpdateOn: 'family_record' });
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');
    await expect(submitTuition(sb, session, 'self_pay')).rejects.toThrow(
      /funding_type/,
    );
  });
});

describe('addStudent (R1 — per-child grain under an existing household)', () => {
  it('inserts a synthetic-shaped student keyed to the household family_id (no new family_record)', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    // A-24: createFamily now creates the FIRST child as a student, threaded on the
    // session, so every application is per-child from the start.
    const session = await createFamily(sb, uid, 'direct');
    const familiesBefore = sb.rowsFor('family_record').length;
    expect(sb.rowsFor('student')).toHaveLength(1); // the first child
    expect(session.studentId).toBe(sb.rowsFor('student')[0]!.student_id);

    const studentId = await addStudent(sb, session.familyId); // a SECOND child

    expect(sb.rowsFor('family_record')).toHaveLength(familiesBefore); // no new family
    const students = sb.rowsFor('student');
    expect(students).toHaveLength(2); // first + added, both under the one household
    const s = students.find((x) => x.student_id === studentId)!;
    expect(s.family_id).toBe(session.familyId); // FK → the household spine
    expect(s.current_stage).toBe('interest'); // write-time placeholder
    expect(s.funding_state).toBe('none'); // fail-closed default (INV-10)
    // Synthetic-shaped, no PII (INV-1/INV-6): name/grade/label only, no DOB.
    expect(String(s.synthetic_first_name).length).toBeGreaterThan(0);
    expect(String(s.grade).length).toBeGreaterThan(0);
    for (const key of Object.keys(s)) {
      expect(key.toLowerCase()).not.toContain('dob');
    }
  });

  it('surfaces a student insert failure to the caller', async () => {
    // createFamily creates the first child (a student insert), so a student-insert
    // failure surfaces from it (the per-child grain starts at sign-up).
    const sb = makeMockSupabase({ failInsertOn: 'student' });
    const uid = await ensureAnonSession(sb);
    await expect(createFamily(sb, uid, 'direct')).rejects.toThrow(/student/);
  });
});

describe('per-child apply writes (A-24 — packets keyed + linked to the student)', () => {
  it('writes app_form/enrollment with the child student_id and links the student', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');
    await submitApply(sb, session);
    await submitEnroll(sb, session, 6);
    await submitTuition(sb, session, 'self_pay');

    // Every packet carries THIS child's student_id (the per-child grain).
    for (const af of sb.rowsFor('app_form')) {
      expect(af.student_id).toBe(session.studentId);
      expect(af.family_id).toBe(session.familyId);
    }
    const enrolls = sb.rowsFor('enrollment_forms');
    expect(enrolls.length).toBeGreaterThan(0);
    for (const ef of enrolls) {
      expect(ef.student_id).toBe(session.studentId);
    }

    // The student is LINKED to its own packets + funding (so the cockpit embeds the
    // child's own funnel). funding_state is NOT written here (DERIVED/GT-gated, INV-10).
    const studentUpdates = sb.updates.filter((u) => u.table === 'student');
    const linked = Object.assign({}, ...studentUpdates.map((u) => u.values));
    expect(linked.app_form_id).toBeDefined();
    expect(linked.enrollment_form_id).toBeDefined();
    expect(linked.funding_type).toBe('self_pay');
    expect('funding_state' in linked).toBe(false);
  });
});

describe('deriveInterestAnswers (S18 candidacy — derive uncollected columns)', () => {
  it('derives every leads_new column the candidacy modal does not collect, from the closed option sets', () => {
    const a = deriveInterestAnswers('11111111-1111-4111-8111-111111111111', 'direct');
    expect(PRODUCT_INTEREST).toContain(a.product_interest);
    expect(REGION).toContain(a.region);
    expect(GRADE_INTEREST).toContain(a.grade_interest);
    expect(NUM_CHILDREN).toContain(a.num_children);
    expect(ATTRIBUTION_SOURCE).toContain(a.attribution_source);
    expect(a.attribution_source).toBe('direct');
  });

  it('is deterministic for a given family id', () => {
    const id = '22222222-2222-4222-8222-222222222222';
    expect(deriveInterestAnswers(id, 'direct')).toEqual(
      deriveInterestAnswers(id, 'direct'),
    );
  });
});

describe('fetchApplications (S18 dashboard — owner-scoped read + X/4 derivation)', () => {
  it('derives stage progress from which source rows exist', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');

    // Only family_record + leads_new ⇒ Interest done (1/4).
    await submitInterest(sb, session, deriveInterestAnswers(session.familyId, 'direct'));
    let apps = await fetchApplications(sb);
    expect(apps).toHaveLength(1);
    expect(apps[0]!.stagesComplete).toBe(1);
    expect(apps[0]!.stagesTotal).toBe(4);
    expect(apps[0]!.displayName).toBe(session.identity.displayName);

    // + app_form ⇒ Apply done (2/4).
    await submitApply(sb, session);
    apps = await fetchApplications(sb);
    expect(apps[0]!.stagesComplete).toBe(2);

    // + enrollment_forms ⇒ Enroll done (3/4).
    await submitEnroll(sb, session, 6);
    apps = await fetchApplications(sb);
    expect(apps[0]!.stagesComplete).toBe(3);

    // + tuition (unlocked) ⇒ Tuition done (4/4).
    await submitTuition(sb, session, 'self_pay');
    apps = await fetchApplications(sb);
    expect(apps[0]!.stagesComplete).toBe(4);
  });

  it('projects funding fields + per-lane booleans (R3), voucher fail-closed', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');
    await submitInterest(sb, session, deriveInterestAnswers(session.familyId, 'direct'));
    await submitApply(sb, session);
    await submitEnroll(sb, session, 3); // partial enrollment

    let apps = await fetchApplications(sb);
    let a = apps[0]!;
    // Per-lane booleans projected from the source rows.
    expect(a.applicationDone).toBe(true);
    expect(a.enrollmentDone).toBe(false);
    expect(a.formsSigned).toBe(3);
    expect(a.formsTotal).toBe(6);
    // Funding fields default fail-closed before any tier is persisted.
    expect(a.fundingType).toBeNull();
    expect(a.fundingState).toBe('none');
    expect(a.voucherConfirmed).toBe(false);

    // After tuition: a tier is persisted, but funding_state stays 'none' (the SPA
    // never self-confirms a voucher) → voucherConfirmed STAYS false (INV-10).
    await submitTuition(sb, session, 'tefa_standard');
    apps = await fetchApplications(sb);
    a = apps[0]!;
    expect(a.fundingType).toBe('tefa_standard');
    expect(a.enrollmentDone).toBe(true);
    expect(a.voucherConfirmed).toBe(false);

    // Only a money-in-hand state flips the voucher lane to confirmed.
    await sb
      .from('family_record')
      .update({ funding_state: 'first_installment_received' })
      .eq('family_id', session.familyId);
    apps = await fetchApplications(sb);
    expect(apps[0]!.voucherConfirmed).toBe(true);
  });

  it('returns one card per family_record (separate households are separate cards)', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    const s1 = await createFamily(sb, uid, 'direct');
    const s2 = await createFamily(sb, uid, 'direct');
    await submitInterest(sb, s1, deriveInterestAnswers(s1.familyId, 'direct'));
    await submitInterest(sb, s2, deriveInterestAnswers(s2.familyId, 'direct'));
    const apps = await fetchApplications(sb);
    expect(apps).toHaveLength(2);
    expect(new Set(apps.map((a) => a.familyId))).toEqual(
      new Set([s1.familyId, s2.familyId]),
    );
  });
});

describe('deleteApplication (S18 dashboard — owner-scoped delete)', () => {
  it('issues a family_id-filtered delete on every owned table and removes the row', async () => {
    const sb = makeMockSupabase();
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');
    await submitInterest(sb, session, deriveInterestAnswers(session.familyId, 'direct'));
    await submitApply(sb, session);

    await deleteApplication(sb, session.familyId);

    const tables = new Set(sb.deletes.map((d) => d.table));
    expect(tables.has('family_record')).toBe(true);
    expect(tables.has('leads_new')).toBe(true);
    expect(tables.has('app_form')).toBe(true);
    for (const d of sb.deletes) {
      expect(d.filter.family_id).toBe(session.familyId);
    }
    // The application is gone after the delete.
    expect(await fetchApplications(sb)).toHaveLength(0);
  });

  it('surfaces a delete failure to the caller', async () => {
    const sb = makeMockSupabase({ failDeleteOn: 'leads_new' });
    const uid = await ensureAnonSession(sb);
    const session = await createFamily(sb, uid, 'direct');
    await submitInterest(sb, session, deriveInterestAnswers(session.familyId, 'direct'));
    await expect(deleteApplication(sb, session.familyId)).rejects.toThrow(
      /leads_new/,
    );
  });
});

describe('failure handling', () => {
  it('surfaces a leads_new insert failure to the caller', async () => {
    const sb = makeMockSupabase({ failInsertOn: 'leads_new' });
    const uid = await ensureAnonSession(sb);
    const session: ApplySession = await createFamily(sb, uid, 'direct');
    await expect(
      submitInterest(sb, session, {
        product_interest: 'campus',
        attribution_source: 'direct',
        region: 'Midwest',
        grade_interest: 'K',
        num_children: 1,
      }),
    ).rejects.toThrow(/leads_new/);
  });

  it('emitEvent never throws even if the insert fails (best-effort telemetry)', async () => {
    const sb = makeMockSupabase({ failInsertOn: 'apply_events' });
    await expect(
      emitEvent(sb, {
        family_id: 'f',
        step: 'interest',
        form_key: null,
        field_key: null,
        event_type: 'step_viewed',
        time_on_step_ms: 0,
        nav_seq: 0,
      }),
    ).resolves.toBeUndefined();
  });
});
