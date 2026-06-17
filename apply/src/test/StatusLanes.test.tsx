// Acceptance test (§4.2) for the MD "Closed — pending SIS confirmation" status
// (MULTI_AGENT_COCKPIT §10.2). Extends the R3 four-lane StatusLanes:
//
//   * a family who went ALL THE WAY (enrollment done) but is NOT yet SIS-confirmed
//     shows the enrollment lane as "Closed — pending SIS confirmation" (read from
//     the family's OWN sis_status row — the M5 bucket),
//   * when the sis_status bucket flips to ✅ `confirmed`, the lane flips to
//     "Confirmed",
//   * the verdict is derived from the bucket: enrollment-done + bucket != 'confirmed'
//     ⇒ pending; bucket == 'confirmed' ⇒ confirmed,
//   * a mid-funnel family (enrollment NOT done) shows the ordinary forms progress,
//     never the SIS-pending copy (the pending state is gated on enrollment-done),
//   * the SIS read is the family's own row (anon+RLS), and a MISSING sis_status row
//     is fail-safe (still "pending", never a false confirm).
//
// The flip is driven through the dashboard: it reads sis_status on every fetch, so
// updating the seeded row + re-fetching (an Add-Another-Child refresh) re-derives
// the lane — the same refresh path the voucher-lane test uses.

import { describe, expect, it } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { fetchApplications } from '../lib/apply';
import { makeMockSupabase } from './mockSupabase';

const UID = '00000000-0000-4000-8000-000000000abc';
const FAM = '11111111-1111-4111-8111-111111111111';

// A "went all the way" family: leads + app + a 6/6, unlocked enrollment_forms row.
function wentAllTheWay(sisBucket: string | null) {
  return makeMockSupabase({
    uid: UID,
    persistedSession: true,
    seed: {
      family_record: [
        {
          family_id: FAM,
          user_id: UID,
          display_name: 'Maple Household',
          funding_state: 'none',
        },
      ],
      leads_new: [{ lead_id: 'l1', family_id: FAM }],
      app_form: [{ app_form_id: 'a1', family_id: FAM }],
      enrollment_forms: [
        {
          enrollment_form_id: 'e1',
          family_id: FAM,
          forms_total: 6,
          forms_signed: 6,
          tuition_step_unlocked: true,
        },
      ],
      ...(sisBucket
        ? {
            sis_status: [
              {
                family_id: FAM,
                present: sisBucket === 'confirmed',
                confirmed_at: sisBucket === 'confirmed' ? '2026-06-01T00:00:00Z' : null,
                bucket: sisBucket,
              },
            ],
          }
        : {}),
    },
  });
}

async function resumeToStatus() {
  await waitFor(() => screen.getByLabelText('resume_banner'));
  await userEvent.click(screen.getByText('Resume my status page'));
  await waitFor(() => screen.getByText('My Applications'));
  return screen.findByLabelText('application_card');
}

describe('StatusLanes — Closed — pending SIS confirmation (MD)', () => {
  it('enrollment-done + SIS-unconfirmed shows "Closed — pending SIS confirmation"', async () => {
    // bucket = records_lag ⇒ went all the way, but the SIS has NOT confirmed yet.
    const sb = wentAllTheWay('records_lag');
    render(<App supabase={sb} />);
    const card = await resumeToStatus();

    const enrollment = within(card).getByLabelText('lane_enrollment');
    expect(enrollment).toHaveTextContent('Closed — pending SIS confirmation');
    // It is NOT yet "done" — pending the school's system (a partial state).
    expect(enrollment).not.toHaveTextContent('Confirmed');
    expect(enrollment.className).not.toContain('done');
  });

  it('a MISSING sis_status row is fail-safe — still pending, never a false confirm', async () => {
    const sb = wentAllTheWay(null); // no verdict yet
    render(<App supabase={sb} />);
    const card = await resumeToStatus();

    const enrollment = within(card).getByLabelText('lane_enrollment');
    expect(enrollment).toHaveTextContent('Closed — pending SIS confirmation');
    expect(enrollment.className).not.toContain('done');
  });

  it('flips to "Confirmed" when the sis_status bucket says ✅ confirmed', async () => {
    const sb = wentAllTheWay('records_lag');
    render(<App supabase={sb} />);
    const card = await resumeToStatus();
    expect(within(card).getByLabelText('lane_enrollment')).toHaveTextContent(
      'Closed — pending SIS confirmation',
    );

    // The daily reconcile flips the family's OWN sis_status row to ✅ confirmed.
    await sb
      .from('sis_status')
      .update({ bucket: 'confirmed', present: true, confirmed_at: '2026-06-10T00:00:00Z' })
      .eq('family_id', FAM);

    // Re-fetch via the dashboard refresh (Add Another Child re-fetches).
    await userEvent.click(screen.getByText('+ Add Another Child'));
    await waitFor(() => {
      const c = screen.getByLabelText('application_card');
      const enrollment = within(c).getByLabelText('lane_enrollment');
      expect(enrollment).toHaveTextContent('Confirmed');
      expect(enrollment).not.toHaveTextContent('pending SIS');
      expect(enrollment.className).toContain('done');
    });
  });

  it('a mid-funnel family (enrollment NOT done) shows forms progress, never the SIS-pending copy', async () => {
    const sb = makeMockSupabase({
      uid: UID,
      persistedSession: true,
      seed: {
        family_record: [
          { family_id: FAM, user_id: UID, display_name: 'Cedar Household', funding_state: 'none' },
        ],
        leads_new: [{ lead_id: 'l1', family_id: FAM }],
        app_form: [{ app_form_id: 'a1', family_id: FAM }],
        enrollment_forms: [
          {
            enrollment_form_id: 'e1',
            family_id: FAM,
            forms_total: 6,
            forms_signed: 2, // mid-funnel
            tuition_step_unlocked: false,
          },
        ],
      },
    });
    render(<App supabase={sb} />);
    const card = await resumeToStatus();
    const enrollment = within(card).getByLabelText('lane_enrollment');
    // Forms progress, NOT the SIS-pending copy (pending is gated on enrollment-done).
    expect(enrollment).toHaveTextContent('2/6 forms');
    expect(enrollment).not.toHaveTextContent('pending SIS');
  });
});

describe('fetchApplications — SIS verdict projection (MD)', () => {
  it('derives sisBucket + sisConfirmed from the family\'s own sis_status row', async () => {
    const sb = wentAllTheWay('records_lag');
    let apps = await fetchApplications(sb);
    expect(apps[0]!.sisBucket).toBe('records_lag');
    expect(apps[0]!.sisConfirmed).toBe(false);

    await sb
      .from('sis_status')
      .update({ bucket: 'confirmed', present: true, confirmed_at: '2026-06-10T00:00:00Z' })
      .eq('family_id', FAM);
    apps = await fetchApplications(sb);
    expect(apps[0]!.sisBucket).toBe('confirmed');
    expect(apps[0]!.sisConfirmed).toBe(true);
  });

  it('a family with no sis_status row reports sisBucket=null, sisConfirmed=false (fail-safe)', async () => {
    const sb = wentAllTheWay(null);
    const apps = await fetchApplications(sb);
    expect(apps[0]!.sisBucket).toBeNull();
    expect(apps[0]!.sisConfirmed).toBe(false);
  });
});
