import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DropOffPanel from '../DropOffPanel';

// Acceptance test (CLAUDE §4.2). The per-family drop-off panel reads GET
// /families/{id}/drop-off and surfaces the humanized last-step-before-exit.
// Degrades cleanly on 204 / unknown shape — never an error or infinite spinner.

const POPULATED = {
  family_id: 'fam-123',
  step: 'enroll',
  form_key: 'data_collection_consent',
  field_key: 'signature',
  event_type: 'field_blur',
  occurred_at: '2026-06-12T10:00:00Z',
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DropOffPanel', () => {
  it('renders the humanized last-step-before-exit from a populated point', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => POPULATED,
      })),
    );

    render(<DropOffPanel familyId="fam-123" />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-panel')).toBeInTheDocument();
    });
    expect(screen.getByTestId('dropoff-path')).toHaveTextContent(
      'Enroll · Data Collection Consent · Signature',
    );
    // No raw snake_case leaks through.
    expect(screen.queryByText(/data_collection_consent/)).toBeNull();
  });

  it('hits GET /families/{id}/drop-off', async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => POPULATED,
    }));
    vi.stubGlobal('fetch', fetchMock);

    render(<DropOffPanel familyId="fam-123" />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-panel')).toBeInTheDocument();
    });
    const calls = fetchMock.mock.calls as unknown as unknown[][];
    const url = String(calls[0]?.[0] ?? '');
    expect(url).toMatch(/\/families\/fam-123\/drop-off$/);
  });

  it('renders a quiet empty line on HTTP 204 (no telemetry), never an error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 204,
        json: async () => {
          throw new Error('204 has no body');
        },
      })),
    );

    render(<DropOffPanel familyId="fam-123" />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-panel-empty')).toBeInTheDocument();
    });
    expect(screen.getByTestId('dropoff-panel-empty')).toHaveTextContent(
      'No drop-off telemetry',
    );
    expect(screen.queryByTestId('dropoff-panel')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('treats an unknown payload shape as empty (never masquerades as a drop-off)', async () => {
    // A stray fetch that resolves to some OTHER object (no `step`) must not pose
    // as telemetry — same fail-safe posture as DealView's isCrmStatus.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ deal_view: { display_name: 'X' } }),
      })),
    );

    render(<DropOffPanel familyId="fam-123" />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-panel-empty')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('dropoff-panel')).toBeNull();
  });

  it('renders empty (no error) on a network failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('network down');
      }),
    );

    render(<DropOffPanel familyId="fam-123" />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-panel-empty')).toBeInTheDocument();
    });
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
