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

  it('Add Another Child starts a fresh application (a new family_record) under the same uid', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await begin();
    await fillCandidacy();
    await fillApply();
    await signRequiredEnrollForms();
    await payDeposit();

    await waitFor(() => screen.getByText('My Applications'));
    expect(sb.rowsFor('family_record')).toHaveLength(1);

    await userEvent.click(screen.getByText('+ Add Another Child'));
    await waitFor(() =>
      expect(
        screen.getByText('Secure Your Candidacy for Fall 2026'),
      ).toBeInTheDocument(),
    );
    const families = sb.rowsFor('family_record');
    expect(families).toHaveLength(2);
    // Both families belong to the SAME auth.uid().
    expect(new Set(families.map((f) => f.user_id))).toEqual(new Set([sb.uid]));
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
