import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import EnrollmentWorkspace from '../workspaces/EnrollmentWorkspace';

// Acceptance test (CLAUDE §4.2). The enrollment workspace composes the deal
// panel (DealView + ActionPanel + FundingTracker) and must focus a REAL family
// id — a UUID from GET /families — never the placeholder 'fam-a', which 422s
// against the API. On mount it loads /families, defaults the focus to the first
// family's id, and mounts the deal panel only once a real id is selected.
// Clicking a work-queue row switches the focused family.
//
// The workspace mounts many children that each fetch, so we use a ROUTED fetch
// mock that returns a sensible payload per endpoint and asserts on call URLs.

// Two REAL families (UUID-shaped ids — never 'fam-a').
const FAM_ONE = '11111111-1111-4111-8111-111111111111';
const FAM_TWO = '22222222-2222-4222-8222-222222222222';

const FAMILIES_PAYLOAD = [
  { family_id: FAM_ONE, display_name: 'The Alvarez Family' },
  { family_id: FAM_TWO, display_name: 'The Bauer Family' },
];

const WORK_QUEUE_PAYLOAD = [
  {
    family_id: FAM_ONE,
    display_name: 'The Alvarez Family',
    current_stage: 'enroll',
    score: 0.91,
    recoverability: 0.95,
    value: 10474,
    recoverable_now: 9000,
    freshness: 0.9,
    contact_status: 'overdue',
    last_contact_at: null,
    recovery_state: 'stalled',
  },
  {
    family_id: FAM_TWO,
    display_name: 'The Bauer Family',
    current_stage: 'apply',
    score: 0.74,
    recoverability: 0.6,
    value: 30000,
    recoverable_now: 20000,
    freshness: 0.95,
    contact_status: 'fresh',
    last_contact_at: null,
    recovery_state: 'stalled',
  },
];

const PIPELINE_PAYLOAD = {
  counts: { interest: 83, apply: 65, enroll: 31, tuition: 21 },
  total: 200,
  seam: { synced: 116, unsynced: 67, conflict: 17 },
};

const SEAM_PAYLOAD = [{ family_id: FAM_ONE, seam_status: 'unsynced' }];

function familyResponse(): unknown {
  return {
    deal_view: {
      display_name: 'The Alvarez Family',
      stall_reason: 'Awaiting funding confirmation',
      funding_type: 'TEFA',
      map_score: 0.82,
      attribution_source: 'Paid Search',
      crm_seam_status: 'synced',
      completion_pct: 45.6,
      forms_signed: 0,
      forms_total: 6,
      next_unsigned_form: 'enrollment_agreement',
      contact_status: 'overdue',
      last_contact_at: null,
      recovery_state: 'stalled',
    },
    family: {},
    lead: {},
    app_form: {},
  };
}

const CALENDAR_PAYLOAD = {
  month: '2026-06',
  entries: [
    {
      family_id: FAM_ONE,
      display_name: 'The Alvarez Family',
      stall_date: '2026-06-10T09:00:00Z',
      apply_date: '2026-05-02T09:00:00Z',
      current_stage: 'enroll',
      contact_status: 'overdue',
      value: 10474,
      score: 0.91,
      recoverable_now: 9000,
      freshness: 0.9,
      recovery_state: 'stalled',
    },
    {
      family_id: FAM_TWO,
      display_name: 'The Bauer Family',
      stall_date: '2026-06-18T09:00:00Z',
      apply_date: '2026-05-09T09:00:00Z',
      current_stage: 'apply',
      contact_status: 'fresh',
      value: 30000,
      score: 0.74,
      recoverable_now: 20000,
      freshness: 0.95,
      recovery_state: 'stalled',
    },
  ],
};

const NOTES_PAYLOAD = [
  {
    note_id: 'note-1',
    family_id: FAM_ONE,
    author: 'operator',
    kind: 'manual',
    body: 'Left a voicemail with the family.',
    created_at: '2026-06-11T10:00:00Z',
  },
];

function fundingResponse(familyId: string): unknown {
  return {
    family_id: familyId,
    funding_state: 'awarded',
    funding_type: 'TEFA',
    installments: ['$2,618.50', '$2,618.50', '$5,237.00'],
    tuition_unlocked: false,
  };
}

// Route a fetch by URL + method to a sensible payload per endpoint.
function routedFetchMock(): ReturnType<typeof vi.fn> {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    let payload: unknown = {};
    // Order matters: more specific (/funding, /notes) before the family base path.
    const fundingMatch = /\/families\/([^/]+)\/funding$/.exec(u);
    if (fundingMatch !== null) {
      payload = fundingResponse(fundingMatch[1] ?? '');
    } else if (/\/families\/[^/]+\/notes$/.test(u)) {
      payload = init?.method === 'POST' ? NOTES_PAYLOAD[0] : NOTES_PAYLOAD;
    } else if (/\/enrollment\/calendar/.test(u)) {
      payload = CALENDAR_PAYLOAD;
    } else if (/\/families\/[^/]+$/.test(u)) {
      payload = familyResponse();
    } else if (/\/families$/.test(u)) {
      payload = FAMILIES_PAYLOAD;
    } else if (/\/work-queue$/.test(u)) {
      payload = WORK_QUEUE_PAYLOAD;
    } else if (/\/pipeline$/.test(u)) {
      payload = PIPELINE_PAYLOAD;
    } else if (/\/seam$/.test(u)) {
      payload = SEAM_PAYLOAD;
    } else if (/\/ai\/enrollment\/draft$/.test(u)) {
      payload = {
        proposal_id: 'prop-1',
        surfaced: true,
        degraded: false,
        failed_rules: [],
        proposal: {
          action: 'email',
          family_id: FAM_ONE,
          body: 'Draft outreach body.',
          claims: [],
        },
      };
    } else if (/\/proposals\/[^/]+\/decision$/.test(u)) {
      payload = { decision_id: 'dec-1', action: 'approve', seam_status: 'synced' };
    } else if (/\/ai\/enrollment\/bulk-nudge$/.test(u)) {
      payload = {
        batch_id: 'b-1',
        counts: { sent: 1, blocked: 1, capped: 0 },
        sent: [{ family_id: FAM_ONE, note_id: 'note-x' }],
        blocked: [{ family_id: FAM_TWO, failed_rules: ['v2_grounding'] }],
        capped: [],
      };
    } else if (/\/enrollment\/families\/bulk-seed$/.test(u)) {
      payload = {
        batch_id: 'b-2',
        counts: { captured: 2 },
        captured: [
          { family_id: FAM_ONE, deal_id: 'd-1', seam_status: 'synced' },
          { family_id: FAM_TWO, deal_id: 'd-2', seam_status: 'synced' },
        ],
      };
    } else if (/\/enrollment\/families\/bulk-dismiss$/.test(u)) {
      payload = {
        batch_id: 'b-3',
        counts: { dismissed: 1 },
        dismissed: [FAM_ONE],
      };
    } else {
      // Default — empty object/array tolerant.
      payload = init?.method === 'POST' ? {} : {};
    }
    return { ok: true, status: 200, json: async () => payload };
  });
}

function urlsCalled(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe('EnrollmentWorkspace', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('enrollmentWorkspaceSelectsRealFamily', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // The deal panel renders for a REAL family (deal view resolves).
    expect(await screen.findByTestId('deal-view')).toBeInTheDocument();

    // The deal/funding fetches targeted the first family's UUID...
    await waitFor(() => {
      const urls = urlsCalled();
      expect(urls.some((u) => u.includes(`/families/${FAM_ONE}`))).toBe(true);
    });

    // ...and NO fetch ever used the placeholder 'fam-a' (the bug).
    expect(urlsCalled().some((u) => u.includes('fam-a'))).toBe(false);
  });

  it('switches the focused family when a calendar chip is clicked', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // The calendar is the default "find" surface — click the second family's
    // chip to focus it in the work-panel.
    const chip = await screen.findByTestId(`calendar-chip-${FAM_TWO}`);
    fireEvent.click(chip);

    // Selecting that chip triggers a fetch for that family's id.
    await waitFor(() => {
      const urls = urlsCalled();
      expect(urls.some((u) => u.includes(`/families/${FAM_TWO}`))).toBe(true);
    });

    // Still never the placeholder.
    expect(urlsCalled().some((u) => u.includes('fam-a'))).toBe(false);
  });

  it('switches the focused family from the show-all ranked list', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // Reveal the ranked working set, then click the second family's row.
    const toggle = await screen.findByTestId('enrollment-view-toggle');
    fireEvent.click(within(toggle).getByRole('tab', { name: /show all/i }));

    const secondRow = await screen.findByTestId(`drill-row-${FAM_TWO}`);
    fireEvent.click(secondRow);

    await waitFor(() => {
      const urls = urlsCalled();
      expect(urls.some((u) => u.includes(`/families/${FAM_TWO}`))).toBe(true);
    });
    expect(urlsCalled().some((u) => u.includes('fam-a'))).toBe(false);
  });

  it('renders the notes timeline in the deal panel', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // The notes timeline is mounted and shows the family's notes (FR-2.3).
    expect(await screen.findByTestId('notes-timeline')).toBeInTheDocument();
    expect(
      await screen.findByText('Left a voicemail with the family.'),
    ).toBeInTheDocument();
  });

  it('shows a situation bar with derived recovery headline numbers', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // The situation bar renders, derived from the /work-queue rows. A-17: a fresh
    // lead is still inside its contact window, so it is NOT stalled — only the
    // overdue row counts ⇒ stalled=1; overdue=1; both rows are still recoverable
    // ⇒ recoverable $ = 10474 + 30000 = $40,474.
    const bar = await screen.findByTestId('situation-bar');
    expect(within(bar).getByTestId('situation-stalled')).toHaveTextContent('1');
    expect(within(bar).getByTestId('situation-overdue')).toHaveTextContent('1');
    expect(within(bar).getByTestId('situation-recoverable')).toHaveTextContent(
      '$40,474',
    );
  });

  it('defaults to the calendar and toggles to the show-all ranked list', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // Calendar is the default primary "find": it's visible, the ranked list is
    // out of view.
    expect(await screen.findByTestId('enrollment-calendar')).toBeInTheDocument();
    expect(screen.queryByTestId('show-all-list')).not.toBeInTheDocument();

    // One click on "Show all" swaps to the ranked working set.
    const toggle = screen.getByTestId('enrollment-view-toggle');
    fireEvent.click(within(toggle).getByRole('tab', { name: /show all/i }));

    expect(await screen.findByTestId('show-all-list')).toBeInTheDocument();
    expect(screen.queryByTestId('enrollment-calendar')).not.toBeInTheDocument();

    // ...and back to the calendar (still one action).
    fireEvent.click(within(toggle).getByRole('tab', { name: /calendar/i }));
    expect(await screen.findByTestId('enrollment-calendar')).toBeInTheDocument();
    expect(screen.queryByTestId('show-all-list')).not.toBeInTheDocument();
  });

  it('bulk-nudges a selection from show-all and renders the gate partition toast', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // Open the show-all list, select a row, then bulk-nudge.
    const toggle = await screen.findByTestId('enrollment-view-toggle');
    fireEvent.click(within(toggle).getByRole('tab', { name: /show all/i }));

    const check = await screen.findByTestId(`drill-row-check-${FAM_ONE}`);
    fireEvent.click(check);

    // The bulk bar appears; nudge the selection.
    const nudge = await screen.findByTestId('bulk-nudge');
    fireEvent.click(nudge);

    // The bulk-nudge route was POSTed...
    await waitFor(() => {
      expect(
        urlsCalled().some((u) => u.includes('/ai/enrollment/bulk-nudge')),
      ).toBe(true);
    });

    // ...and the partition (1 sent · 1 blocked) is SHOWN in a toast — blocked
    // families are never hidden (visible fail-closed gate, INV-3/4).
    const toast = await screen.findByTestId('toast');
    expect(toast).toHaveTextContent('1 nudges sent');
    expect(toast).toHaveTextContent('1 blocked by the gate');
  });

  it('refreshes the deal view + notes after an approved follow-up', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // Request a draft, then approve it (the follow-up).
    fireEvent.click(await screen.findByTestId('draft-email'));
    fireEvent.click(await screen.findByTestId('approve-action'));

    // The decision was recorded...
    expect(await screen.findByTestId('decision-recorded')).toBeInTheDocument();

    // ...and the approve triggered a re-pull of the deal view + notes (the loop):
    // the family detail and the notes endpoint are fetched MORE THAN ONCE.
    await waitFor(() => {
      const urls = urlsCalled();
      const dealPulls = urls.filter((u) =>
        new RegExp(`/families/${FAM_ONE}$`).test(u),
      ).length;
      const notePulls = urls.filter((u) =>
        new RegExp(`/families/${FAM_ONE}/notes$`).test(u),
      ).length;
      expect(dealPulls).toBeGreaterThan(1);
      expect(notePulls).toBeGreaterThan(1);
    });
  });
});
