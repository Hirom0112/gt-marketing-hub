import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ActionPanel from '../ActionPanel';

// Acceptance test (CLAUDE §4.2). The enrollment AI action panel (FR-2.4) lets
// an operator request an AI draft for a family. The eval gate is enforced
// VISUALLY and fail-closed (INV-3 / FR-4.5): a surfaced proposal renders with
// approve/edit/discard controls ENABLED; a RED eval (or degraded / kill-switch
// mode) DISABLES the AI draft and offers a deterministic template fallback.
// Native fetch only (≤2 runtime deps). No state libraries. fireEvent only
// (no user-event dep — runtime + dev deps stay as committed).

// A passing draft — eval gate surfaced it (surfaced:true, degraded:false).
const SURFACED_DRAFT = {
  proposal_id: 'prop-123',
  surfaced: true,
  degraded: false,
  failed_rules: [] as string[],
  proposal: {
    action: 'email',
    family_id: 'fam-a',
    body: 'Hi Alvarez family — your MAP placement is ready to review.',
    claims: [{ text: 'MAP placement', source_ref: 'map_score' }],
  },
};

// A blocked draft — eval gate RED (surfaced:false, a failed grounding rule,
// no proposal). This is the INV-3 fail-closed surface.
const BLOCKED_DRAFT = {
  proposal_id: 'prop-456',
  surfaced: false,
  degraded: false,
  failed_rules: ['v2_grounding'],
  proposal: null,
};

// A degraded draft — no LLM / kill switch / cost cap (NFR-3 fallback). Same
// fail-closed posture as a red eval: drafting unavailable, template offered.
const DEGRADED_DRAFT = {
  proposal_id: 'prop-789',
  surfaced: false,
  degraded: true,
  failed_rules: [] as string[],
  proposal: null,
};

function mockFetchOnce(payload: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => payload,
    })),
  );
}

describe('ActionPanel', () => {
  beforeEach(() => {
    // Default to the surfaced draft; individual tests re-stub as needed.
    mockFetchOnce(SURFACED_DRAFT);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('test_panel_shows_proposal_for_approval', async () => {
    render(<ActionPanel familyId="fam-a" />);

    // The operator requests an email draft.
    fireEvent.click(screen.getByTestId('draft-email'));

    // The drafted body renders.
    expect(await screen.findByTestId('proposal-body')).toHaveTextContent(
      SURFACED_DRAFT.proposal.body,
    );

    // Approve / edit / discard controls are present AND enabled.
    const approve = screen.getByTestId('approve-action');
    const edit = screen.getByTestId('edit-action');
    const discard = screen.getByTestId('discard-action');
    expect(approve).toBeEnabled();
    expect(edit).toBeEnabled();
    expect(discard).toBeEnabled();

    // Approving POSTs to /proposals/{id}/decision.
    mockFetchOnce({
      decision_id: 'dec-1',
      action: 'approve',
      seam_status: 'synced',
    });
    fireEvent.click(approve);

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/proposals\/prop-123\/decision$/);
    expect(init?.method).toBe('POST');
  });

  it('deep-links the live HubSpot Note on an approved follow-up (S10 W3)', async () => {
    render(<ActionPanel familyId="fam-a" />);
    fireEvent.click(screen.getByTestId('draft-email'));
    const approve = await screen.findByTestId('approve-action');

    // Approve returns the live note id (CRM_MODE=live writes a HubSpot Note).
    mockFetchOnce({
      action: 'approve',
      seam_status: 'synced',
      note_id: 'note-77665544',
    });
    fireEvent.click(approve);

    const noteLink = await screen.findByTestId('decision-note-link');
    expect(noteLink).toHaveAttribute(
      'href',
      'https://app-na2.hubspot.com/contacts/246504420/record/0-46/note-77665544',
    );
  });

  it('test_red_eval_disables_action_in_ui', async () => {
    mockFetchOnce(BLOCKED_DRAFT);
    render(<ActionPanel familyId="fam-a" />);

    fireEvent.click(screen.getByTestId('draft-email'));

    // The blocked state is shown with the failed rule.
    const blocked = await screen.findByTestId('proposal-blocked');
    expect(within(blocked).getByText(/v2_grounding/)).toBeInTheDocument();

    // No actionable proposal body for approval is rendered.
    expect(screen.queryByTestId('proposal-body')).not.toBeInTheDocument();

    // The approve action is absent or visibly disabled — fail closed (INV-3).
    const approve = screen.queryByTestId('approve-action');
    if (approve) {
      expect(approve).toBeDisabled();
    } else {
      expect(approve).toBeNull();
    }

    // A deterministic template fallback is offered.
    expect(screen.getByTestId('template-fallback')).toBeInTheDocument();
  });

  it('test_degraded_mode_offers_template', async () => {
    mockFetchOnce(DEGRADED_DRAFT);
    render(<ActionPanel familyId="fam-a" />);

    fireEvent.click(screen.getByTestId('draft-email'));

    // Degraded: drafting unavailable, the deterministic template is offered.
    expect(await screen.findByTestId('proposal-degraded')).toBeInTheDocument();
    expect(screen.queryByTestId('proposal-body')).not.toBeInTheDocument();
    expect(screen.queryByTestId('approve-action')).toBeNull();
    expect(screen.getByTestId('template-fallback')).toBeInTheDocument();
  });

  it('requests the draft via POST /ai/enrollment/draft', async () => {
    render(<ActionPanel familyId="fam-a" />);

    fireEvent.click(screen.getByTestId('draft-email'));

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit?];
    expect(url).toMatch(/\/ai\/enrollment\/draft$/);
    expect(init?.method).toBe('POST');
    const sentBody = JSON.parse(String(init?.body)) as {
      family_id: string;
      action: string;
    };
    expect(sentBody.family_id).toBe('fam-a');
    expect(sentBody.action).toBe('email');
  });
});
