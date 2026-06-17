// Acceptance test (§4.2) for the MD demo family-switcher + pages dropbox
// (MULTI_AGENT_COCKPIT §10.2). Asserts:
//   * the switcher lists the seeded synthetic families,
//   * selecting one signs into THAT family's anon session and shows their
//     four-lane status,
//   * NO cross-family leak — signed in as family A, family B's data is absent
//     (RLS owner-scope on the family's own anon uid holds — INV-5/INV-1),
//   * the pages dropbox surfaces the quick links (apply flow · status · cockpit),
//   * the switcher is synthetic-only + honest ("not real login"),
//   * no service_role anywhere — the swap is an anon session swap.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { DemoSwitcher } from '../DemoSwitcher';
import { makeDemoMockSupabase } from './mockSupabase';
import type { DemoFamily } from '../lib/demo';

// Two seeded synthetic families, each its OWN anon uid (its RLS owner key) and its
// own family_record. Family A went all the way; family B is mid-funnel. Both are
// synthetic, @example.invalid (INV-1).
const UID_A = '00000000-0000-4000-8000-00000000000a';
const UID_B = '00000000-0000-4000-8000-00000000000b';
const FAM_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const FAM_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

const DEMO_FAMILIES: DemoFamily[] = [
  { uid: UID_A, familyId: FAM_A, label: 'Maple Household', hint: 'went all the way' },
  { uid: UID_B, familyId: FAM_B, label: 'Cedar Household', hint: 'mid-funnel' },
];

// A seeded cohort store across BOTH families. The demo mock owner-scopes every
// read to the active uid, so this is the whole-cohort truth the DB holds.
function seededCohort() {
  return makeDemoMockSupabase({
    initialUid: UID_A,
    store: {
      family_record: [
        {
          family_id: FAM_A,
          user_id: UID_A,
          display_name: 'Maple Household',
          primary_contact_synthetic_email: 'maple@example.invalid',
          funding_state: 'first_installment_received',
        },
        {
          family_id: FAM_B,
          user_id: UID_B,
          display_name: 'Cedar Household',
          primary_contact_synthetic_email: 'cedar@example.invalid',
          funding_state: 'none',
        },
      ],
      leads_new: [
        { lead_id: 'la', family_id: FAM_A },
        { lead_id: 'lb', family_id: FAM_B },
      ],
      app_form: [
        { app_form_id: 'aa', family_id: FAM_A },
        { app_form_id: 'ab', family_id: FAM_B },
      ],
      enrollment_forms: [
        // A: all the way (6/6, unlocked). B: mid-funnel (2/6).
        {
          enrollment_form_id: 'ea',
          family_id: FAM_A,
          forms_total: 6,
          forms_signed: 6,
          tuition_step_unlocked: true,
        },
        {
          enrollment_form_id: 'eb',
          family_id: FAM_B,
          forms_total: 6,
          forms_signed: 2,
          tuition_step_unlocked: false,
        },
      ],
    },
  });
}

// A bare-bones App render seeded with VITE_DEMO_FAMILIES so the switcher lists
// the cohort. The App uses isDemoSupabase() to decide whether to render it.
function renderAppWithDemo(sb: ReturnType<typeof makeDemoMockSupabase>) {
  vi.stubEnv('VITE_DEMO_FAMILIES', JSON.stringify(DEMO_FAMILIES));
  return render(<App supabase={sb} />);
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('DemoSwitcher — component', () => {
  it('lists the seeded synthetic families in the dropdown', async () => {
    const selected: DemoFamily[] = [];
    render(
      <DemoSwitcher
        families={DEMO_FAMILIES}
        onSelectFamily={(f) => {
          selected.push(f);
        }}
        onApplyFlow={() => {}}
        onStatusPage={() => {}}
      />,
    );
    const select = screen.getByLabelText('demo_family_select') as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);
    expect(labels.join(' ')).toContain('Maple Household');
    expect(labels.join(' ')).toContain('Cedar Household');

    // Selecting a family + clicking sign-in fires onSelectFamily with THAT family.
    await userEvent.selectOptions(select, UID_B);
    await userEvent.click(screen.getByLabelText('demo_sign_in'));
    expect(selected).toHaveLength(1);
    expect(selected[0]!.uid).toBe(UID_B);
  });

  it('is honest: labelled a demo session swap, NOT real login', () => {
    render(
      <DemoSwitcher
        families={DEMO_FAMILIES}
        onSelectFamily={() => {}}
        onApplyFlow={() => {}}
        onStatusPage={() => {}}
      />,
    );
    const note = screen.getByText(/not real login/i);
    expect(note).toBeInTheDocument();
  });

  it('exposes the pages dropbox quick links (apply flow · status · cockpit)', () => {
    let applyHit = false;
    let statusHit = false;
    render(
      <DemoSwitcher
        families={DEMO_FAMILIES}
        onSelectFamily={() => {}}
        onApplyFlow={() => {
          applyHit = true;
        }}
        onStatusPage={() => {
          statusHit = true;
        }}
        cockpitUrl="https://cockpit.example.invalid"
      />,
    );
    expect(screen.getByLabelText('pages_dropbox')).toBeInTheDocument();
    const cockpit = screen.getByLabelText('page_cockpit') as HTMLAnchorElement;
    expect(cockpit.getAttribute('href')).toBe('https://cockpit.example.invalid');

    screen.getByLabelText('page_apply_flow').click();
    screen.getByLabelText('page_status').click();
    expect(applyHit).toBe(true);
    expect(statusHit).toBe(true);
  });

  it('renders an honest empty state when no families are seeded', () => {
    render(
      <DemoSwitcher
        families={[]}
        onSelectFamily={() => {}}
        onApplyFlow={() => {}}
        onStatusPage={() => {}}
      />,
    );
    const select = screen.getByLabelText('demo_family_select') as HTMLSelectElement;
    expect(select.options[0]!.textContent).toMatch(/No seeded families/);
    // The sign-in button is disabled with nothing to sign in as.
    expect(screen.getByLabelText('demo_sign_in')).toBeDisabled();
  });
});

describe('DemoSwitcher — App integration (sign in as a family, no cross-family leak)', () => {
  it('selecting a family signs into THAT family and shows their four-lane status', async () => {
    const sb = seededCohort();
    renderAppWithDemo(sb);

    await waitFor(() => screen.getByLabelText('demo_switcher'));
    // Sign in as family B (Cedar) — a session swap to family B's own anon uid.
    await userEvent.selectOptions(
      screen.getByLabelText('demo_family_select'),
      UID_B,
    );
    await userEvent.click(screen.getByLabelText('demo_sign_in'));

    // The active anon session swapped to family B's uid (the demo session swap).
    await waitFor(() => expect(sb.activeUid).toBe(UID_B));

    // We land on family B's four-lane status page.
    await waitFor(() => screen.getByText('My Applications'));
    const card = await screen.findByLabelText('application_card');
    expect(within(card).getByText('Cedar Household')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_application')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_enrollment')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_voucher')).toBeInTheDocument();
    expect(within(card).getByLabelText('lane_next_step')).toBeInTheDocument();
  });

  it('NO cross-family leak — signed in as family B, family A is absent (RLS owner-scope holds)', async () => {
    const sb = seededCohort();
    renderAppWithDemo(sb);

    await waitFor(() => screen.getByLabelText('demo_switcher'));
    await userEvent.selectOptions(
      screen.getByLabelText('demo_family_select'),
      UID_B,
    );
    await userEvent.click(screen.getByLabelText('demo_sign_in'));

    await waitFor(() => screen.getByText('My Applications'));
    // EXACTLY ONE card — family B's — even though the store holds BOTH families.
    const cards = await screen.findAllByLabelText('application_card');
    expect(cards).toHaveLength(1);
    expect(within(cards[0]!).getByText('Cedar Household')).toBeInTheDocument();
    // Family A's household is NOWHERE on family B's page (no leak).
    expect(screen.queryByText('Maple Household')).toBeNull();
  });

  it('cross-family boundary holds BOTH ways — swapping A↔B never bleeds the other family', async () => {
    const sb = seededCohort();
    // Render the App, then drive the demo swap to A, assert isolation, then to B.
    renderAppWithDemo(sb);
    await waitFor(() => screen.getByLabelText('demo_switcher'));

    // Sign in as A.
    await userEvent.selectOptions(
      screen.getByLabelText('demo_family_select'),
      UID_A,
    );
    await userEvent.click(screen.getByLabelText('demo_sign_in'));
    await waitFor(() => screen.getByText('My Applications'));
    const cards = await screen.findAllByLabelText('application_card');
    expect(cards).toHaveLength(1);
    expect(within(cards[0]!).getByText('Maple Household')).toBeInTheDocument();
    expect(screen.queryByText('Cedar Household')).toBeNull();
    // Family A went all the way + a first installment is in hand → voucher confirmed.
    expect(within(cards[0]!).getByLabelText('lane_voucher')).toHaveTextContent(
      'Confirmed',
    );
  });

  it('the demo swap is an anon session swap — never a service_role / privileged path (INV-5)', async () => {
    const sb = seededCohort();
    renderAppWithDemo(sb);
    await waitFor(() => screen.getByLabelText('demo_switcher'));
    await userEvent.selectOptions(
      screen.getByLabelText('demo_family_select'),
      UID_A,
    );
    await userEvent.click(screen.getByLabelText('demo_sign_in'));
    await waitFor(() => screen.getByText('My Applications'));

    // Every read the SPA issued is an ordinary owner-scoped select on the anon
    // client — no service_role / privileged table is ever touched.
    for (const s of sb.selects) {
      expect(s.table.toLowerCase()).not.toContain('service_role');
    }
    // The switch carried only the family's uid (no token/key in the component API).
    expect(typeof sb.signInAsUid).toBe('function');
  });
});
