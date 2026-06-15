import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  },
  {
    family_id: FAM_TWO,
    display_name: 'The Bauer Family',
    current_stage: 'apply',
    score: 0.74,
    recoverability: 0.6,
    value: 30000,
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
    },
    family: {},
    lead: {},
    app_form: {},
  };
}

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
    // Order matters: more specific (/funding) before the family base path.
    const fundingMatch = /\/families\/([^/]+)\/funding$/.exec(u);
    if (fundingMatch !== null) {
      payload = fundingResponse(fundingMatch[1] ?? '');
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

  it('switches the focused family when a work-queue row is clicked', async () => {
    vi.stubGlobal('fetch', routedFetchMock());
    render(<EnrollmentWorkspace />);

    // Wait for the second row to be available (work queue loaded).
    const secondRow = await screen.findByTestId(`work-queue-row-${FAM_TWO}`);

    fireEvent.click(secondRow);

    // Selecting the second row triggers a fetch for that family's id.
    await waitFor(() => {
      const urls = urlsCalled();
      expect(urls.some((u) => u.includes(`/families/${FAM_TWO}`))).toBe(true);
    });

    // Still never the placeholder.
    expect(urlsCalled().some((u) => u.includes('fam-a'))).toBe(false);
  });
});
