import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import BulkBar from '../BulkBar';

// Acceptance test (CLAUDE §4.2). The sticky bulk dock: shows the selected count,
// fires the batch actions, renders the optional pre-send partition (the visible
// gate trust signal), and swaps to a reason-picker rail in dismiss mode.

describe('BulkBar', () => {
  it('renders nothing when nothing is selected and there is no view to select', () => {
    const { container } = render(<BulkBar count={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders the thin select-all rail when 0 selected but rows are in view', () => {
    const onSelectAll = vi.fn();
    render(<BulkBar count={0} viewCount={42} onSelectAll={onSelectAll} />);
    const rail = screen.getByTestId('bulk-rail');
    expect(rail).toHaveTextContent('Select all 42 in view');
    fireEvent.click(screen.getByTestId('bulk-rail-select-all'));
    expect(onSelectAll).toHaveBeenCalledTimes(1);
    // The dark dock is NOT shown at 0 selected.
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
  });

  it('shows the selection recoverable total in the dock when provided', () => {
    render(<BulkBar count={3} recoverableLabel="$88,000" />);
    expect(screen.getByTestId('bulk-bar-recoverable')).toHaveTextContent(
      '$88,000 recoverable',
    );
  });

  it('shows the selected count and fires the batch actions', () => {
    const onNudge = vi.fn();
    const onCapture = vi.fn();
    const onClear = vi.fn();
    const onDismissStart = vi.fn();
    render(
      <BulkBar
        count={12}
        onNudge={onNudge}
        onCapture={onCapture}
        onClear={onClear}
        onDismissStart={onDismissStart}
      />,
    );
    expect(screen.getByTestId('bulk-bar')).toHaveTextContent('12 selected');

    fireEvent.click(screen.getByTestId('bulk-nudge'));
    fireEvent.click(screen.getByTestId('bulk-capture'));
    fireEvent.click(screen.getByTestId('bulk-dismiss-start'));
    fireEvent.click(screen.getByTestId('bulk-clear'));
    expect(onNudge).toHaveBeenCalledTimes(1);
    expect(onCapture).toHaveBeenCalledTimes(1);
    expect(onDismissStart).toHaveBeenCalledTimes(1);
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('renders the pre-send partition (visible gate) when provided', () => {
    render(<BulkBar count={80} partition={{ willSend: 68, blocked: 12 }} />);
    expect(screen.getByTestId('bulk-bar-partition')).toHaveTextContent(
      '68 will send · 12 blocked by the gate',
    );
  });

  it('omits the partition line when no partition is given', () => {
    render(<BulkBar count={80} />);
    expect(screen.queryByTestId('bulk-bar-partition')).toBeNull();
  });

  it('swaps to the reason-picker in dismiss mode and fires onDismiss', () => {
    const onDismiss = vi.fn();
    const onCancelDismiss = vi.fn();
    render(
      <BulkBar
        count={5}
        pendingDismiss
        reasons={['Declined', 'Bad fit']}
        onDismiss={onDismiss}
        onCancelDismiss={onCancelDismiss}
      />,
    );
    // The dark dock is replaced by the reasons rail.
    expect(screen.queryByTestId('bulk-bar')).toBeNull();
    expect(screen.getByTestId('bulk-bar-reasons')).toHaveTextContent(
      'dismiss 5 · pick a reason:',
    );

    fireEvent.click(screen.getByTestId('bulk-reason-Declined'));
    expect(onDismiss).toHaveBeenCalledWith('Declined');

    fireEvent.click(screen.getByTestId('bulk-reason-cancel'));
    expect(onCancelDismiss).toHaveBeenCalledTimes(1);
  });
});
