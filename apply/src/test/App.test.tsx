// Acceptance test (§4.2) for the rebuilt mock apply SPA. Mirrors apply.gt.school
// at faithful structure / trimmed depth: a labelled 4-node stepper, a long
// multi-section Apply form, a 7-form left-rail Enroll sub-stepper with signature
// blocks, and a $1,000 Tuition deposit. Asserts:
//   * the form exposes ONLY <select>/checkbox/radio (no free-text PII input),
//   * walking Apply fires per-field field_changed (step → field telemetry),
//   * walking an Enroll sub-form fires form_viewed + signature field events +
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

async function fillInterest() {
  await selectByLabel('Which program interests you?', 'anywhere');
  await selectByLabel('How many children are you applying for?', '2');
  await selectByLabel('What grade are they entering?', '4');
  await selectByLabel('Which region are you in?', 'West Coast');
  await selectByLabel('How did you hear about us?', 'organic_search');
  await userEvent.click(screen.getByText('Continue to application'));
}

// Fills every required field on the long Apply form, then submits.
async function fillApply() {
  await waitFor(() => screen.getByText('Your application'));
  await selectByLabel('Relationship to child', 'mother');
  await selectByLabel('State', 'Texas');
  await selectByLabel('Region', 'West Coast');
  await selectByLabel('How many children are you enrolling?', '2');
  // yes/no radios (by value attribute within their group)
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

// Signs every required enroll sub-form (the 6 non-optional ones), navigating the
// left rail. Each pane: Sign as <name> → agree checkbox → Submit form.
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
    // Open the form via the rail.
    await userEvent.click(screen.getByRole('button', { name: new RegExp(title) }));
    await userEvent.click(await screen.findByLabelText('signature'));
    await userEvent.click(screen.getByLabelText('agree_terms'));
    await userEvent.click(screen.getByText('Submit form'));
  }
  await userEvent.click(screen.getByText('Continue to tuition'));
}

describe('mock apply SPA — acceptance', () => {
  it('signs in anonymously and creates the family_record on load', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() =>
      expect(screen.getByText('Tell us about your interest')).toBeInTheDocument(),
    );
    const fr = sb.rowsFor('family_record');
    expect(fr).toHaveLength(1);
    expect(fr[0]!.user_id).toBe(sb.uid);
    expect(
      String(fr[0]!.primary_contact_synthetic_email).endsWith(
        SYNTHETIC_EMAIL_DOMAIN,
      ),
    ).toBe(true);
  });

  it('exposes ONLY <select>, checkboxes and radios — no free-text PII input', async () => {
    const sb = makeMockSupabase();
    const { container } = render(<App supabase={sb} />);
    await waitFor(() =>
      expect(screen.getByText('Tell us about your interest')).toBeInTheDocument(),
    );
    // No text/email/tel/number/date input ever renders; only checkbox + radio.
    const assertNoTextInputs = () => {
      const inputs = Array.from(container.querySelectorAll('input'));
      for (const input of inputs) {
        expect(['checkbox', 'radio']).toContain(input.type);
      }
      expect(container.querySelector('textarea')).toBeNull();
    };
    assertNoTextInputs();

    await fillInterest();
    await waitFor(() =>
      expect(screen.getByText('Your application')).toBeInTheDocument(),
    );
    assertNoTextInputs();
  });

  it('walks all four steps to the done screen, writing rows in order', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));

    await fillInterest();
    await fillApply();
    await signRequiredEnrollForms();

    await waitFor(() => screen.getByText('Reserve your spot'));
    await selectByLabel('How will tuition be funded?', 'self_pay');
    await userEvent.click(screen.getByText('Pay $1,000 deposit'));

    await waitFor(() => screen.getByText("You're enrolled"));

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
    expect(sb.rowsFor('leads_new')[0]!.num_children).toBe(2);
    expect(
      sb.rowsFor('enrollment_forms').some((r) => r.tuition_step_unlocked),
    ).toBe(true);
  });

  it('walking Apply fires per-field field_changed (step → field telemetry)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await fillInterest();
    await fillApply();
    await waitFor(() => screen.getByText('Complete your enrollment forms'));

    const applyEvents = sb
      .rowsFor('apply_events')
      .filter((e) => e.step === 'apply');
    const changed = applyEvents.filter((e) => e.event_type === 'field_changed');
    // Multiple distinct Apply fields recorded a change, keyed by field NAME.
    const changedFields = new Set(changed.map((e) => e.field_key));
    expect(changedFields.has('relationship')).toBe(true);
    expect(changedFields.has('child_gender')).toBe(true);
    expect(changedFields.has('tefa_funds')).toBe(true);
    expect(changedFields.has('iep_plan')).toBe(true);
    // Apply-step field events carry form_key = null (sections are not sub-forms).
    for (const e of applyEvents) {
      expect(e.form_key).toBeNull();
    }
  });

  it('walking an Enroll sub-form fires form_viewed + signature events + form_completed (step → form → field)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await fillInterest();
    await fillApply();
    await waitFor(() => screen.getByText('Complete your enrollment forms'));

    // Open + complete the Data Collection Consent sub-form specifically.
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
    // The signature + agree field events fired inside this sub-form.
    const fieldChanged = dcc.filter((e) => e.event_type === 'field_changed');
    const fields = new Set(fieldChanged.map((e) => e.field_key));
    expect(fields.has('signature')).toBe(true);
    expect(fields.has('agree_terms')).toBe(true);
    // Every event for this sub-form is on the enroll step and carries the form_key.
    for (const e of dcc) {
      expect(e.step).toBe('enroll');
      expect(e.form_key).toBe('data_collection_consent');
    }
  });

  it('abandoning mid-Enroll fires last_step_before_exit with the current form_key', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await fillInterest();
    await fillApply();
    await waitFor(() => screen.getByText('Complete your enrollment forms'));

    // Enter a sub-form but DON'T complete it, then abandon (tab hidden).
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
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await fillInterest();
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
      // nav_seq is a monotonically increasing per-session counter.
      expect(typeof ev.nav_seq).toBe('number');
      expect(ev.nav_seq as number).toBeGreaterThan(prevSeq);
      prevSeq = ev.nav_seq as number;
      // field_key, when present, is a field NAME, not a chosen option token.
      if (ev.field_key !== null) {
        expect(['anywhere', '4', 'West Coast', 'organic_search', 2]).not.toContain(
          ev.field_key,
        );
      }
    }
  });

  it('shows validation errors (and fires validation_error_shown) on empty submit', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await userEvent.click(screen.getByText('Continue to application'));
    expect(screen.getByText('Tell us about your interest')).toBeInTheDocument();
    const errs = await screen.findAllByText(
      'Please choose an option to continue.',
    );
    expect(errs.length).toBeGreaterThan(0);
    const events = sb.rowsFor('apply_events');
    expect(events.map((e) => e.event_type)).toContain('validation_error_shown');
    expect(sb.rowsFor('leads_new')).toHaveLength(0);
  });

  it('the deposit amount is exactly $1,000 (simulated)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await fillInterest();
    await fillApply();
    await signRequiredEnrollForms();
    await waitFor(() => screen.getByText('Reserve your spot'));
    const card = screen.getByText('Reserve your spot').closest('.card')!;
    expect(within(card as HTMLElement).getByText('$1,000')).toBeInTheDocument();
  });
});
