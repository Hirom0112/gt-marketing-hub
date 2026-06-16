import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import DropOffHeatmap from '../DropOffHeatmap';

// Acceptance test (CLAUDE §4.2). The aggregate heatmap reads GET
// /drop-off/heatmap and renders one "step · form · field — N froze here" row per
// bucket, count-desc, with a visual-weight bar. Empty ⇒ a graceful empty state.

const POPULATED = {
  buckets: [
    {
      step: 'enroll',
      form_key: 'data_collection_consent',
      field_key: 'signature',
      count: 12,
    },
    { step: 'apply', form_key: 'household_info', field_key: null, count: 5 },
    { step: 'tuition', form_key: null, field_key: null, count: 2 },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('DropOffHeatmap', () => {
  it('renders one humanized row per bucket with the froze-here count', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => POPULATED,
      })),
    );

    render(<DropOffHeatmap />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-heatmap-rows')).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId('dropoff-heatmap-row');
    expect(rows).toHaveLength(3);
    const [topRow, , lastRow] = rows as [HTMLElement, HTMLElement, HTMLElement];

    // Top row = busiest cell, humanized path + count.
    expect(
      within(topRow).getByTestId('dropoff-heatmap-path'),
    ).toHaveTextContent('Enroll · Data Collection Consent · Signature');
    expect(
      within(topRow).getByTestId('dropoff-heatmap-count'),
    ).toHaveTextContent('12 froze here');

    // A step-only bucket (null form + field) humanizes to just the step.
    expect(
      within(lastRow).getByTestId('dropoff-heatmap-path'),
    ).toHaveTextContent('Tuition');

    // No raw snake_case anywhere.
    expect(screen.queryByText(/data_collection_consent/)).toBeNull();
  });

  it('scales the busiest bar to full width and a smaller cell narrower', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => POPULATED,
      })),
    );

    render(<DropOffHeatmap />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-heatmap-rows')).toBeInTheDocument();
    });
    const bars = screen.getAllByTestId(
      'dropoff-heatmap-bar',
    ) as HTMLElement[];
    const [topBar, midBar] = bars as [HTMLElement, HTMLElement];
    expect(topBar.style.width).toBe('100%'); // 12/12
    // 5/12 ≈ 41.67% — strictly less than the busiest bar.
    expect(parseFloat(midBar.style.width)).toBeLessThan(100);
  });

  it('renders the graceful empty state on empty buckets', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ buckets: [] }),
      })),
    );

    render(<DropOffHeatmap />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-heatmap-empty')).toBeInTheDocument();
    });
    expect(screen.getByTestId('dropoff-heatmap-empty')).toHaveTextContent(
      'No drop-off data yet',
    );
    expect(screen.queryByTestId('dropoff-heatmap-rows')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows a quiet error line on a non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 500,
        json: async () => ({}),
      })),
    );

    render(<DropOffHeatmap />);

    await waitFor(() => {
      expect(screen.getByTestId('dropoff-heatmap-error')).toBeInTheDocument();
    });
  });
});
