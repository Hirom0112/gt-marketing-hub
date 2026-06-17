// Acceptance test (§4.2) for the mock apply SPA — S18 added three surfaces:
//   * a marketing LANDING page (hero + Begin Application),
//   * a "Secure Your Candidacy for Fall 2026" CANDIDACY modal (prefilled-
//     synthetic read-only identity, income dropdown, SMS-consent) that writes a
//     valid leads_new row,
//   * a "My Applications" DASHBOARD (one card per application, X/4 progress,
//     delete, Add Another Child).
// Plus the existing 4-step flow (Apply / Enroll / Tuition). Asserts:
//   * the form exposes ONLY <select>/checkbox/radio (no free-text PII input),
//   * walking Apply fires per-field field_changed (step → field telemetry),
//   * walking an Enroll sub-form fires form_viewed + signature events +
//     form_completed (step → form → field depth, w/ form_key),
//   * abandoning mid-Enroll fires last_step_before_exit carrying the right
//     form_key,
//   * the synthetic email ends @example.invalid,
//   * source rows are written in the unchanged dependency order.

import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { makeMockSupabase } from './mockSupabase';
import { SYNTHETIC_EMAIL_DOMAIN } from '../lib/identity';

async function selectByLabel(label: string, value: string) {
  const el = screen.getByLabelText(label) as HTMLSelectElement;
  await userEvent.selectOptions(el, value);
}

// Landing → Begin Application → into the candidacy modal.
async function begin() {
  await waitFor(() => screen.getByText(/The MIT of K/));
  await userEvent.click(await screen.findByText('Begin Application'));
  await waitFor(() => screen.getByText('Secure Your Candidacy for Fall 2026'));
}

// Fills the candidacy modal (income + SMS) and starts the application.
async function fillCandidacy() {
  await selectByLabel('Household Income', '100k_150k');
  await userEvent.click(screen.getByLabelText('sms_consent'));
  await userEvent.click(screen.getByText('Start Application'));
}

// Fills every required field on the long Apply form, then submits.
async function fillApply() {
  await waitFor(() => screen.getByText('Your application'));
  await selectByLabel('Which program interests you?', 'anywhere');
  await selectByLabel('Relationship to child', 'mother');
  await selectByLabel('State', 'Texas');
  await selectByLabel('Region', 'West Coast');
  await selectByLabel('How many children are you enrolling?', '2');
  await userEvent.click(within(screen.getByRole('group', { name: 'Have you received TEFA funds before?' })).getByLabelText('Yes'));
  await selectByLabel('Child gender', 'female');
  await selectByLabel('Grade', '4');
  await selectByLabel('Desired enrollment year', '2026');
  await selectByLabel('Current school situation', 'public_school');
  await selectByLabel('How will your child use GT?', 'full_time');
  await userEvent.click(within(screen.getByRole('group', { name: 'Does your child have an IEP, 504, or behavior plan?' })).getByLabelText('No'));
  await userEvent.click(within(screen.getByRole('group', { name: 'Any diagnosed disabilities?' })).getByLabelText('No'));
  await userEvent.click(screen.getByLabelText('child_ack'));
  await userEvent.click(screen.getByLabelText('tuition_aware'));
  await selectByLabel('Source', 'organic_search');
  await userEvent.click(screen.getByText('Submit application'));
}

// Signs every required enroll sub-form (the 6 non-optional ones).
async function signRequiredEnrollForms() {
  await waitFor(() => screen.getByText('Complete your enrollment forms'));
  const requiredTitles = [
    'Student Information',
    'Parent / Guardian Information',
    'Data Collection Consent',
    'Academic Information',
    'Privacy & Data Consent',
    'Tuition Agreement',
  ];
  for (const title of requiredTitles) {
    await userEvent.click(screen.getByRole('button', { name: new RegExp(title) }));
    await userEvent.click(await screen.findByLabelText('signature'));
    await userEvent.click(screen.getByLabelText('agree_terms'));
    await userEvent.click(screen.getByText('Submit form'));
  }
  await userEvent.click(screen.getByText('Continue to tuition'));
}

async function payDeposit() {
  await waitFor(() => screen.getByText('Reserve your spot'));
  await selectByLabel('How will tuition be funded?', 'self_pay');
  await userEvent.click(screen.getByText('Pay $1,000 deposit'));
}

describe('mock apply SPA — acceptance', () => {
  it('shows the marketing landing first and Begin Application advances', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() =>
      expect(screen.getByText(/The MIT of K/)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/For students who.*ask for more academics/),
    ).toBeInTheDocument();
    // No family_record is created until the applicant begins.
    expect(sb.rowsFor('family_record')).toHaveLength(0);
    await userEvent.click(await screen.findByText('Begin Application'));
    await waitFor(() =>
      expect(
        screen.getByText('Secure Your Candidacy for Fall 2026'),
      ).toBeInTheDocument(),
    );
    // Begin creates the owning family_record under the authed uid.
    const fr = sb.rowsFor('family_record');
    expect(fr).toHaveLength(1);
    expect(fr[0]!.user_id).toBe(sb.uid);
  });

  it('candidacy modal: prefilled identity is read-only (no typed PII), income + SMS fire field_changed, Start writes a valid leads_new row', async () => {
    const sb = makeMockSupabase();
    const { container } = render(<App supabase={sb} />);
    await begin();

    // No text/email/tel/number/date input or textarea renders in the modal —
    // only the income <select> + the SMS checkbox.
    const inputs = Array.from(container.querySelectorAll('input'));
    for (const input of inputs) {
      expect(['checkbox', 'radio']).toContain(input.type);
    }
    expect(container.querySelector('textarea')).toBeNull();

    // The prefilled synthetic identity is shown read-only (presentational text).
    const email = screen.getByLabelText('email_synthetic');
    expect(email.textContent).toContain(SYNTHETIC_EMAIL_DOMAIN);

    await selectByLabel('Household Income', '100k_150k');
    await userEvent.click(screen.getByLabelText('sms_consent'));

    const fr = sb.rowsFor('family_record')[0]!;
    await userEvent.click(screen.getByText('Start Application'));

    // Per-field telemetry fired for income + SMS consent.
    const changed = sb
      .rowsFor('apply_events')
      .filter((e) => e.step === 'candidacy' && e.event_type === 'field_changed')
      .map((e) => e.field_key);
    expect(changed).toContain('household_income');
    expect(changed).toContain('sms_consent');

    // A valid leads_new row was written with EVERY required column, derived
    // where the candidacy modal doesn't collect it.
    const lead = sb.rowsFor('leads_new')[0]!;
    expect(lead.family_id).toBe(fr.family_id);
    for (const col of [
      'lead_id',
      'family_id',
      'synthetic_first_name',
      'synthetic_last_name',
      'synthetic_email',
      'synthetic_phone',
      'source',
      'product_interest',
      'grade_interest',
      'region',
      'num_children',
    ]) {
      expect(lead[col]).toBeDefined();
    }
    expect(String(lead.synthetic_email).endsWith(SYNTHETIC_EMAIL_DOMAIN)).toBe(
      true,
    );
    // household_income is UI + telemetry only — it is NOT persisted to leads_new.
    expect(lead.household_income).toBeUndefined();
  });

  it('exposes ONLY <select>, checkboxes and radios — no free-text PII input', async () => {
    const sb = makeMockSupabase();
    const { container } = render(<App supabase={sb} />);
    const assertNoTextInputs = () => {
      const inputs = Array.from(container.querySelectorAll('input'));
      for (const input of inputs) {
        expect(['checkbox', 'radio']).toContain(input.type);
      }
      expect(container.querySelector('textarea')).toBeNull();
    };
    await begin();
    assertNoTextInputs();
    await fillCandidacy();
    await waitFor(() =>
      expect(screen.getByText('Your application')).toBeInTheDocument(),
    );
    assertNoTextInputs();
  });

  it('walks the full flow to the dashboard, writing rows in order', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();

    await waitFor(() => screen.getByText('My Applications'));

    const order = sb.inserts
      .map((i) => i.table)
      .filter((t, idx, a) => a.indexOf(t) === idx);
    expect(order.indexOf('family_record')).toBeLessThan(
      order.indexOf('leads_new'),
    );
    expect(order.indexOf('leads_new')).toBeLessThan(order.indexOf('app_form'));
    expect(order.indexOf('app_form')).toBeLessThan(
      order.indexOf('enrollment_forms'),
    );
    expect(
      sb.rowsFor('enrollment_forms').some((r) => r.tuition_step_unlocked),
    ).toBe(true);
  });

  it('dashboard lists the application with correct X/4 progress', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();

    await waitFor(() => screen.getByText('My Applications'));
    const card = await screen.findByLabelText('application_card');
    // A completed flow is all 4 stages done.
    expect(within(card).getByText('4/4')).toBeInTheDocument();
  });

  // R1 — "Add Another Child" inserts a `student` UNDER the existing household's
  // family_record, NOT a new family_record. The child is a row in the live
  // `student` table keyed by family_id → the household spine.
  it('Add Another Child inserts a student under the existing household, not a new family_record', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();

    await waitFor(() => screen.getByText('My Applications'));
    expect(sb.rowsFor('family_record')).toHaveLength(1);
    expect(sb.rowsFor('student')).toHaveLength(0);
    const householdFamilyId = sb.rowsFor('family_record')[0]!.family_id as string;

    await userEvent.click(screen.getByText('+ Add Another Child'));

    // A `student` row was inserted — NOT a second family_record.
    await waitFor(() => expect(sb.rowsFor('student')).toHaveLength(1));
    expect(sb.rowsFor('family_record')).toHaveLength(1);

    const student = sb.rowsFor('student')[0]!;
    // The child is a child of the EXISTING household (FK family_id → the spine).
    expect(student.family_id).toBe(householdFamilyId);
    // Synthetic-shaped child identity only (INV-1/INV-6): a name, a grade, a
    // display label — no DOB, no precise geo, never a typed value.
    expect(student.synthetic_first_name).toBeDefined();
    expect(student.grade).toBeDefined();
    expect(student.display_label).toBeDefined();
    // The new child surfaces on the dashboard as a household child.
    await waitFor(() =>
      expect(screen.getByLabelText('student_card')).toBeInTheDocument(),
    );
  });

  // The student write must go through the SAME anon+RLS path — never the
  // service_role/cockpit path (INV-5). The SPA's only client is the anon one;
  // this asserts the insert is the only write and carries no privileged key.
  it('the Add-Another-Child student insert is an ordinary owner-scoped insert (anon+RLS, never service_role)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();
    await waitFor(() => screen.getByText('My Applications'));

    await userEvent.click(screen.getByText('+ Add Another Child'));
    await waitFor(() => expect(sb.rowsFor('student')).toHaveLength(1));

    const studentInserts = sb.inserts.filter((i) => i.table === 'student');
    expect(studentInserts).toHaveLength(1);
    // No service_role / privileged column ever leaves the client.
    for (const row of studentInserts[0]!.rows) {
      for (const key of Object.keys(row)) {
        expect(key.toLowerCase()).not.toContain('service_role');
        expect(key.toLowerCase()).not.toContain('user_id'); // ownership is via the FK
      }
    }
  });

  it('delete issues a delete on the owned rows and removes the card', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();

    await waitFor(() => screen.getByText('My Applications'));
    const card = await screen.findByLabelText('application_card');
    const familyId = sb.rowsFor('family_record')[0]!.family_id as string;
    const trash = within(card).getByLabelText(`delete_${familyId}`);
    await userEvent.click(trash);

    // A delete was issued against the owned tables, filtered by family_id.
    const deletedTables = new Set(sb.deletes.map((d) => d.table));
    expect(deletedTables.has('family_record')).toBe(true);
    expect(deletedTables.has('leads_new')).toBe(true);
    for (const d of sb.deletes) {
      expect(d.filter.family_id).toBe(familyId);
    }
    // The card is gone after the refresh.
    await waitFor(() =>
      expect(screen.queryByLabelText('application_card')).toBeNull(),
    );
    expect(sb.rowsFor('family_record')).toHaveLength(0);
  });

  it('walking Apply fires per-field field_changed (step → field telemetry)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await waitFor(() => screen.getByText('Complete your enrollment forms'));

    const applyEvents = sb
      .rowsFor('apply_events')
      .filter((e) => e.step === 'apply');
    const changed = applyEvents.filter((e) => e.event_type === 'field_changed');
    const changedFields = new Set(changed.map((e) => e.field_key));
    expect(changedFields.has('relationship')).toBe(true);
    expect(changedFields.has('child_gender')).toBe(true);
    expect(changedFields.has('tefa_funds')).toBe(true);
    expect(changedFields.has('iep_plan')).toBe(true);
    for (const e of applyEvents) {
      expect(e.form_key).toBeNull();
    }
  });

  it('walking an Enroll sub-form fires form_viewed + signature events + form_completed (step → form → field)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await waitFor(() => screen.getByText('Complete your enrollment forms'));

    await userEvent.click(
      screen.getByRole('button', { name: /Data Collection Consent/ }),
    );
    await userEvent.click(await screen.findByLabelText('signature'));
    await userEvent.click(screen.getByLabelText('agree_terms'));
    await userEvent.click(screen.getByText('Submit form'));

    const dcc = sb
      .rowsFor('apply_events')
      .filter((e) => e.form_key === 'data_collection_consent');
    const types = dcc.map((e) => e.event_type);
    expect(types).toContain('form_viewed');
    expect(types).toContain('form_completed');
    const fieldChanged = dcc.filter((e) => e.event_type === 'field_changed');
    const fields = new Set(fieldChanged.map((e) => e.field_key));
    expect(fields.has('signature')).toBe(true);
    expect(fields.has('agree_terms')).toBe(true);
    for (const e of dcc) {
      expect(e.step).toBe('enroll');
      expect(e.form_key).toBe('data_collection_consent');
    }
  });

  it('abandoning mid-Enroll fires last_step_before_exit with the current form_key', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await waitFor(() => screen.getByText('Complete your enrollment forms'));

    await userEvent.click(
      screen.getByRole('button', { name: /Academic Information/ }),
    );
    await screen.findByText(/Prior academic records/);

    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    document.dispatchEvent(new Event('visibilitychange'));

    const exits = sb
      .rowsFor('apply_events')
      .filter((e) => e.event_type === 'last_step_before_exit');
    expect(exits.length).toBeGreaterThan(0);
    const last = exits[exits.length - 1]!;
    expect(last.step).toBe('enroll');
    expect(last.form_key).toBe('academic_information');
  });

  it('every apply_event is metadata-only with the enriched key set + nav_seq', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await waitFor(() => screen.getByText('Your application'));

    const events = sb.rowsFor('apply_events');
    expect(events.length).toBeGreaterThan(0);
    const allowed = [
      'event_id',
      'event_type',
      'family_id',
      'field_key',
      'form_key',
      'nav_seq',
      'step',
      'time_on_step_ms',
    ].sort();
    let prevSeq = -1;
    for (const ev of events) {
      expect(Object.keys(ev).sort()).toEqual(allowed);
      expect(typeof ev.nav_seq).toBe('number');
      expect(ev.nav_seq as number).toBeGreaterThan(prevSeq);
      prevSeq = ev.nav_seq as number;
      if (ev.field_key !== null) {
        expect(['100k_150k', 'anywhere', 'organic_search']).not.toContain(
          ev.field_key,
        );
      }
    }
  });

  it('shows validation errors (and fires validation_error_shown) on empty candidacy submit', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await userEvent.click(screen.getByText('Start Application'));
    expect(
      screen.getByText('Secure Your Candidacy for Fall 2026'),
    ).toBeInTheDocument();
    const errs = await screen.findAllByText('Please choose an option to continue.');
    expect(errs.length).toBeGreaterThan(0);
    const events = sb.rowsFor('apply_events');
    expect(events.map((e) => e.event_type)).toContain('validation_error_shown');
    expect(sb.rowsFor('leads_new')).toHaveLength(0);
  });

  // R2 — the chosen funding_type is PERSISTED onto the household's family_record
  // (owner-scoped UPDATE), so the cockpit's funding gate reads live truth.
  it('persists the chosen funding_type onto family_record when tuition is submitted', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit(); // selects 'self_pay'

    await waitFor(() => screen.getByText('My Applications'));

    const familyId = sb.rowsFor('family_record')[0]!.family_id as string;
    // The update was issued, owner-scoped by family_id, carrying the chosen tier.
    const ftUpdate = sb.updates.find(
      (u) => u.table === 'family_record' && 'funding_type' in u.values,
    );
    expect(ftUpdate).toBeDefined();
    expect(ftUpdate!.filter.family_id).toBe(familyId);
    expect(ftUpdate!.values.funding_type).toBe('self_pay');
    // It landed on the row.
    expect(sb.rowsFor('family_record')[0]!.funding_type).toBe('self_pay');
    // Fail-closed: funding_state is NOT advanced to confirmed by the SPA (INV-10).
    expect(ftUpdate!.values.funding_state).toBeUndefined();
  });

  // R3 — the four-lane status page: Application · Enrollment · Voucher
  // Confirmation · Next Step, with the voucher lane FAIL-CLOSED.
  it('renders the four status lanes; voucher lane is fail-closed (not "confirmed" after a completed flow with no installment)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();

    await waitFor(() => screen.getByText('My Applications'));
    const card = await screen.findByLabelText('application_card');

    // All four lanes are present.
    expect(within(card).getByLabelText('lane_application')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_enrollment')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_voucher')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_next_step')).toBeInTheDocument();

    // Application + Enrollment are complete after the full flow.
    expect(within(card).getByLabelText('lane_application')).toHaveTextContent(
      'Submitted',
    );
    expect(within(card).getByLabelText('lane_enrollment')).toHaveTextContent(
      'Complete',
    );

    // FAIL-CLOSED: the family submitted a deposit + chose a tier, but no voucher
    // installment is in hand → the voucher lane is NEVER "Confirmed" (INV-10).
    const voucher = within(card).getByLabelText('lane_voucher');
    expect(voucher).not.toHaveTextContent('Confirmed');
    expect(voucher.className).not.toContain('done');

    // The Next Step lane shows the params-driven reconfirm action + a by-when.
    const next = within(card).getByLabelText('lane_next_step');
    expect(next.textContent?.toLowerCase()).toContain('reconfirm');
    expect(next.textContent).toMatch(/by \d{4}-\d{2}-\d{2}/);
  });

  it('voucher lane reads "Confirmed" ONLY once a voucher installment is in hand', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();
    await waitFor(() => screen.getByText('My Applications'));

    // Simulate a GT-confirmed installment landing on the family_record (the only
    // path to a confirmed voucher lane, INV-10) and re-render via Add Child refresh.
    const fr = sb.rowsFor('family_record')[0]!;
    await sb
      .from('family_record')
      .update({ funding_state: 'first_installment_received' })
      .eq('family_id', fr.family_id);

    // Trigger a dashboard refresh (adding a child re-fetches applications).
    await userEvent.click(screen.getByText('+ Add Another Child'));
    await waitFor(() => {
      const card = screen.getByLabelText('application_card');
      expect(within(card).getByLabelText('lane_voucher')).toHaveTextContent(
        'Confirmed',
      );
    });
  });

  // R3 anon-resume — a returning family with a PERSISTED anon session + an owned
  // application sees a resume affordance and lands back on their status page.
  it('offers a synthetic resume affordance when the persisted anon session owns an application', async () => {
    const uid = '00000000-0000-4000-8000-000000000abc';
    const familyId = '11111111-1111-4111-8111-111111111111';
    const sb = makeMockSupabase({
      uid,
      persistedSession: true,
      seed: {
        family_record: [
          {
            family_id: familyId,
            user_id: uid,
            display_name: 'Maple Household',
            funding_state: 'none',
          },
        ],
        leads_new: [{ lead_id: 'l1', family_id: familyId }],
        app_form: [{ app_form_id: 'a1', family_id: familyId }],
      },
    });
    render(<App supabase={sb} />);

    // The resume banner appears (no new sign-in, no PII — the persisted anon
    // session was reused).
    await waitFor(() =>
      expect(screen.getByLabelText('resume_banner')).toBeInTheDocument(),
    );
    expect(screen.getByText(/Welcome back/)).toBeInTheDocument();

    // Resuming reaches the status page with the family's owned application.
    await userEvent.click(screen.getByText('Resume my status page'));
    await waitFor(() => screen.getByText('My Applications'));
    const card = await screen.findByLabelText('application_card');
    expect(within(card).getByText('Maple Household')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_application')).toBeInTheDocument();
  });

  it('shows NO resume affordance on a first visit (no persisted applications)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText(/The MIT of K/));
    // No applications owned ⇒ no resume banner.
    expect(screen.queryByLabelText('resume_banner')).toBeNull();
  });

  it('the deposit amount is exactly $1,000 (simulated)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await waitFor(() => screen.getByText('Reserve your spot'));
    const card = screen.getByText('Reserve your spot').closest('.card')!;
    expect(within(card as HTMLElement).getByText('$1,000')).toBeInTheDocument();
  });
});
