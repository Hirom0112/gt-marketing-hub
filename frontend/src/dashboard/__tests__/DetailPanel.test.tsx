import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DetailPanel from '../DetailPanel';

// Acceptance test (CLAUDE §4.2) for the SHARED right-column family detail panel
// (R1). For a selected family it fetches GET /families/{id} (deal_view) + GET
// /students, and renders the twelve sections in the brief order with NO funding/
// TEFA tracker block. When familyId is null it shows the shared empty state. The
// AI drafts are ungated + editable (D-1). Native fetch stubbed (the apiFetch seam).

// A rich deal_view exercising every section, with BOTH guardians, both contacts,
// the aggregate location labels (D-5), funding type, conversion signal, source,
// and the CRM seam status.
const DEAL_VIEW = {
  display_name: 'The Rivera Family',
  funding_type: 'tefa_standard',
  attribution_source: 'Paid Search',
  crm_seam_status: 'synced',
  conversion_score: 0.79,
  conversion_band: 'High',
  conversion_top_factor_label: 'Funding lined up',
  primary_contact_name: 'Quinn Rivera',
  primary_contact_synthetic_email: 'quinn.rivera@example.invalid',
  primary_contact_synthetic_phone: '555-0185',
  guardian_1_relationship: 'mother',
  secondary_contact_name: 'Dana Rivera',
  secondary_contact_synthetic_email: 'dana.rivera@example.invalid',
  secondary_contact_synthetic_phone: '555-0199',
  guardian_2_relationship: 'father',
  neighborhood: 'Oak Hill',
  region: 'West Coast',
  state: 'TX',
};

// Two children stuck at DIFFERENT stages — they must stack as two blocks; a child
// from another household must NOT leak in.
const STUDENT_BOARD = {
  households: [
    {
      family_id: 'fam-123',
      students: [
        {
          student_id: 'stu-alex',
          family_id: 'fam-123',
          synthetic_first_name: 'Alex',
          grade: '3',
          current_stage: 'enroll',
        },
        {
          student_id: 'stu-mia',
          family_id: 'fam-123',
          synthetic_first_name: 'Mia',
          grade: '1',
          current_stage: 'apply',
        },
      ],
    },
    {
      family_id: 'fam-OTHER',
      students: [
        {
          student_id: 'stu-other',
          family_id: 'fam-OTHER',
          synthetic_first_name: 'Sam',
          grade: '5',
          current_stage: 'interest',
        },
      ],
    },
  ],
};

// Route by URL so the panel's many fetches (deal_view, students, the close-tips
// eval gate, the notes timeline) all resolve sensibly.
function mockPanelFetch(deal: unknown = DEAL_VIEW): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (url: string) => {
    if (/\/students(\?|$)/.test(url)) {
      return { ok: true, status: 200, json: async () => STUDENT_BOARD };
    }
    if (/\/evals$/.test(url)) {
      return { ok: true, status: 200, json: async () => ({ disabled: {} }) };
    }
    if (/\/notes$/.test(url)) {
      return { ok: true, status: 200, json: async () => [] };
    }
    // GET /families/{id}
    return {
      ok: true,
      status: 200,
      json: async () => ({ deal_view: deal }),
    };
  });
  vi.stubGlobal('fetch', fn);
  return fn as unknown as ReturnType<typeof vi.fn>;
}

// The twelve panel sections, in the exact brief order.
const SECTION_TESTIDS = [
  'detail-parents',
  'detail-contact',
  'detail-location',
  'detail-children',
  'detail-funding',
  'detail-conversion',
  'detail-source',
  'detail-seam',
  'detail-close-tips',
  'detail-ai-drafts',
  'detail-notes',
  'detail-log-call',
];

describe('DetailPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders an empty state when no family is selected', () => {
    mockPanelFetch();
    render(<DetailPanel familyId={null} />);
    expect(screen.getByTestId('empty-state')).toBeInTheDocument();
    expect(screen.getByText('No family selected')).toBeInTheDocument();
    expect(screen.queryByTestId('detail-panel')).toBeNull();
  });

  it('renders all twelve sections IN ORDER for a selected family', async () => {
    mockPanelFetch();
    render(<DetailPanel familyId="fam-123" />);

    expect(await screen.findByTestId('detail-panel')).toBeInTheDocument();

    // Every section is present.
    for (const id of SECTION_TESTIDS) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }

    // …and they appear in the brief's top-to-bottom order (DOM position): each
    // section precedes the next in document order.
    const els = SECTION_TESTIDS.map((id) => screen.getByTestId(id));
    for (let i = 1; i < els.length; i += 1) {
      const prev = els[i - 1] as HTMLElement;
      const curr = els[i] as HTMLElement;
      expect(
        prev.compareDocumentPosition(curr) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });

  it('shows both parents, both contacts, and the aggregate location (no street)', async () => {
    mockPanelFetch();
    render(<DetailPanel familyId="fam-123" />);
    await screen.findByTestId('detail-panel');

    const parents = screen.getByTestId('detail-parents');
    expect(parents).toHaveTextContent('Quinn Rivera');
    expect(parents).toHaveTextContent('Dana Rivera');

    expect(screen.getByTestId('detail-email-primary')).toHaveAttribute(
      'href',
      'mailto:quinn.rivera@example.invalid',
    );
    expect(screen.getByTestId('detail-phone-secondary')).toHaveAttribute(
      'href',
      'tel:555-0199',
    );

    expect(screen.getByTestId('detail-location-value')).toHaveTextContent(
      'Oak Hill · West Coast · TX',
    );
  });

  it('stacks one block per child (≥2 children → ≥2 blocks) without leaking other households', async () => {
    mockPanelFetch();
    render(<DetailPanel familyId="fam-123" />);
    await screen.findByTestId('detail-panel');

    const childrenSection = screen.getByTestId('detail-children');
    expect(within(childrenSection).getByTestId('detail-child-stu-alex')).toBeInTheDocument();
    expect(within(childrenSection).getByTestId('detail-child-stu-mia')).toBeInTheDocument();
    // A child from another household must NOT appear.
    expect(screen.queryByTestId('detail-child-stu-other')).toBeNull();
    // The count line reads two children.
    expect(screen.getByTestId('detail-children-count')).toHaveTextContent('2 children');
  });

  it('does NOT mount a FundingTracker / TEFA block (brief removal)', async () => {
    mockPanelFetch();
    render(<DetailPanel familyId="fam-123" />);
    await screen.findByTestId('detail-panel');

    // The funding/TEFA tracker is removed; only the inline funding-TYPE field stays.
    expect(screen.queryByTestId('funding-tracker')).toBeNull();
    expect(screen.queryByText(/installment/i)).toBeNull();
    expect(screen.getByTestId('detail-funding-value')).toHaveTextContent(
      'Texas voucher',
    );
  });

  it('mounts the ungated AI drafts and the shared log-call form', async () => {
    mockPanelFetch();
    render(<DetailPanel familyId="fam-123" />);
    await screen.findByTestId('detail-panel');

    // AI drafts: an email + sms generate button, both editable (no send button).
    expect(screen.getByTestId('ai-draft-generate-email')).toBeInTheDocument();
    expect(screen.getByTestId('ai-draft-generate-sms')).toBeInTheDocument();
    // The shared LogCallForm is mounted (its submit control).
    expect(screen.getByTestId('deal-outcome-submit')).toBeInTheDocument();
  });
});
