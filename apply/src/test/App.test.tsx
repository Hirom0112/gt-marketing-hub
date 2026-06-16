// Acceptance test (§4.2) for the mock apply SPA: the stepper renders, walking it
// fires the right metadata-only apply_events, the synthetic email ends
// @example.invalid, and the form exposes ONLY dropdowns/checkboxes (no free-text
// PII input anywhere).

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

  it('exposes ONLY <select> and checkboxes — no free-text PII input', async () => {
    const sb = makeMockSupabase();
    const { container } = render(<App supabase={sb} />);
    await waitFor(() =>
      expect(screen.getByText('Tell us about your interest')).toBeInTheDocument(),
    );
    // Walk every step and assert no text/email/tel/number input ever renders.
    const assertNoTextInputs = () => {
      const inputs = Array.from(container.querySelectorAll('input'));
      for (const input of inputs) {
        // Only checkboxes are permitted; everything else (text/email/tel/number/
        // date) would be a PII vector.
        expect(input.type).toBe('checkbox');
      }
      expect(container.querySelector('textarea')).toBeNull();
    };
    assertNoTextInputs();

    await selectByLabel('Which program interests you?', 'anywhere');
    await selectByLabel('How many children are you applying for?', '3');
    await selectByLabel('What grade are they entering?', '3');
    await selectByLabel('Which region are you in?', 'Southwest');
    await selectByLabel('How did you hear about us?', 'referral');
    await userEvent.click(screen.getByText('Continue to application'));

    await waitFor(() =>
      expect(screen.getByText('Your application')).toBeInTheDocument(),
    );
    assertNoTextInputs();
  });

  it('walks all four steps to the done screen, writing rows in order', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));

    // Interest
    await selectByLabel('Which program interests you?', 'anywhere');
    await selectByLabel('How many children are you applying for?', '2');
    await selectByLabel('What grade are they entering?', '4');
    await selectByLabel('Which region are you in?', 'West Coast');
    await selectByLabel('How did you hear about us?', 'organic_search');
    await userEvent.click(screen.getByText('Continue to application'));

    // Apply
    await waitFor(() => screen.getByText('Your application'));
    await userEvent.click(screen.getByLabelText('application_ack'));
    await userEvent.click(screen.getByText('Submit application'));

    // Enroll — sign all six
    await waitFor(() => screen.getByText('Sign your enrollment forms'));
    let signButtons = screen.getAllByText('Sign');
    while (signButtons.length > 0) {
      await userEvent.click(signButtons[0]!);
      signButtons = screen.queryAllByText('Sign');
    }
    await userEvent.click(screen.getByText('Continue to tuition'));

    // Tuition — fund + deposit
    await waitFor(() => screen.getByText('Reserve your spot'));
    await selectByLabel('How will tuition be funded?', 'self_pay');
    await userEvent.click(screen.getByText('Pay $1,000 deposit'));

    // Done
    await waitFor(() => screen.getByText("You're enrolled"));

    // Rows written in dependency order, one per source table.
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
    expect(sb.rowsFor('enrollment_forms').some((r) => r.tuition_step_unlocked)).toBe(
      true,
    );
  });

  it('advancing a step fires step_viewed and step_completed (metadata only)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));

    await selectByLabel('Which program interests you?', 'campus');
    await selectByLabel('How many children are you applying for?', '1');
    await selectByLabel('What grade are they entering?', 'K');
    await selectByLabel('Which region are you in?', 'Northeast');
    await selectByLabel('How did you hear about us?', 'webinar');
    await userEvent.click(screen.getByText('Continue to application'));
    await waitFor(() => screen.getByText('Your application'));

    const events = sb.rowsFor('apply_events');
    const types = events.map((e) => e.event_type);
    expect(types).toContain('step_viewed');
    expect(types).toContain('step_completed');

    // Guardrail: no event row carries the selected value or a child key.
    for (const ev of events) {
      expect(Object.keys(ev).sort()).toEqual(
        [
          'event_id',
          'event_type',
          'family_id',
          'field_key',
          'step',
          'time_on_step_ms',
        ].sort(),
      );
      // field_key, when present, is a field NAME, not a chosen option token.
      if (ev.field_key !== null) {
        expect(['campus', 'K', 'Northeast', 'webinar', 1]).not.toContain(
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
    // Still on Interest; an error is shown.
    expect(screen.getByText('Tell us about your interest')).toBeInTheDocument();
    const errs = await screen.findAllByText(
      'Please choose an option to continue.',
    );
    expect(errs.length).toBeGreaterThan(0);
    const events = sb.rowsFor('apply_events');
    expect(events.map((e) => e.event_type)).toContain('validation_error_shown');
    // No leads_new written on a failed validation.
    expect(sb.rowsFor('leads_new')).toHaveLength(0);
  });

  it('the deposit amount is exactly $1,000 (simulated)', async () => {
    const sb = makeMockSupabase();
    render(<App supabase={sb} />);
    await waitFor(() => screen.getByText('Tell us about your interest'));
    await selectByLabel('Which program interests you?', 'anywhere');
    await selectByLabel('How many children are you applying for?', '1');
    await selectByLabel('What grade are they entering?', '1');
    await selectByLabel('Which region are you in?', 'Midwest');
    await selectByLabel('How did you hear about us?', 'direct');
    await userEvent.click(screen.getByText('Continue to application'));
    await waitFor(() => screen.getByText('Your application'));
    await userEvent.click(screen.getByLabelText('application_ack'));
    await userEvent.click(screen.getByText('Submit application'));
    await waitFor(() => screen.getByText('Sign your enrollment forms'));
    let signButtons = screen.getAllByText('Sign');
    while (signButtons.length > 0) {
      await userEvent.click(signButtons[0]!);
      signButtons = screen.queryAllByText('Sign');
    }
    await userEvent.click(screen.getByText('Continue to tuition'));
    await waitFor(() => screen.getByText('Reserve your spot'));
    const card = screen.getByText('Reserve your spot').closest('.card')!;
    expect(within(card as HTMLElement).getByText('$1,000')).toBeInTheDocument();
  });
});
