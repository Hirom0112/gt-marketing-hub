import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DataConfidenceBanner from '../DataConfidenceBanner';

// Acceptance test (CLAUDE §4.2) for the cross-module data-confidence banner
// (TODO_v2 §A4). It reads GET /crm/status and renders a warning ONLY when the
// backend says `data_confidence_banner` is true, showing `parity_overall` as a
// one-decimal percent. When false — or when the status read fails — it renders
// NOTHING (fail-safe: a banner that can't load its status must not block the UI).
// The fetch layer is stubbed (native fetch; apiFetch wraps it), mirroring
// TeamRosterTab.test.

function stubStatus(payload: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(payload),
      } as Response),
    ),
  );
}

const BASE = {
  crm_mode: 'live',
  kill_switch: false,
  effective_mode: 'live',
  token_configured: true,
  calls_per_run_cap: 100,
  parity_by_field: { stage: 0.9, value: 0.78 },
};

describe('DataConfidenceBanner (A4)', () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the warning with the parity percent when data_confidence_banner is true', async () => {
    stubStatus({ ...BASE, parity_overall: 0.842, data_confidence_banner: true });
    render(<DataConfidenceBanner />);
    const banner = await screen.findByTestId('data-confidence-banner');
    expect(banner).toBeInTheDocument();
    // 0.842 → one-decimal percent.
    expect(screen.getByTestId('data-confidence-parity')).toHaveTextContent('84.2%');
  });

  it('renders nothing when data_confidence_banner is false', async () => {
    stubStatus({ ...BASE, parity_overall: 0.99, data_confidence_banner: false });
    const { container } = render(<DataConfidenceBanner />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByTestId('data-confidence-banner')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when the status read fails (fail-safe)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('network down'))),
    );
    const { container } = render(<DataConfidenceBanner />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByTestId('data-confidence-banner')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });
});
