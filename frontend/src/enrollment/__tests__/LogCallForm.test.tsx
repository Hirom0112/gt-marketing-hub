import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LogCallForm from '../LogCallForm';

// Acceptance test (CLAUDE §4.2) for the SHARED contact-outcome form extracted
// from DealView and consumed by both DealView and the dashboard DetailPanel. It
// POSTs `/families/{id}/contact-outcome` with `{channel, disposition, note}` and
// notifies the parent (onLogged) on success. Native fetch stubbed (the apiFetch
// seam). Read-write is the backend's (INV-2); the form only sends the event.

function mockFetch(): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async () => ({
    ok: true,
    status: 201,
    json: async () => ({ family_id: 'fam-1' }),
  }));
  vi.stubGlobal('fetch', fn);
  return fn as unknown as ReturnType<typeof vi.fn>;
}

describe('LogCallForm', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('POSTs the contact-outcome with the chosen channel/disposition/note and notifies the parent', async () => {
    const fn = mockFetch();
    const onLogged = vi.fn();
    render(<LogCallForm familyId="fam-1" onLogged={onLogged} />);

    fireEvent.change(screen.getByTestId('deal-outcome-channel'), {
      target: { value: 'call' },
    });
    fireEvent.change(screen.getByTestId('deal-outcome-disposition'), {
      target: { value: 'reached' },
    });
    fireEvent.change(screen.getByTestId('deal-outcome-note'), {
      target: { value: 'spoke with the parent' },
    });
    fireEvent.click(screen.getByTestId('deal-outcome-submit'));

    await waitFor(() => {
      const post = fn.mock.calls.find(
        ([url, init]) =>
          /\/families\/fam-1\/contact-outcome$/.test(url as string) &&
          (init as RequestInit | undefined)?.method === 'POST',
      );
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as RequestInit).body as string);
      expect(body).toEqual({
        channel: 'call',
        disposition: 'reached',
        note: 'spoke with the parent',
      });
    });

    await waitFor(() => expect(onLogged).toHaveBeenCalled());
  });

  it('surfaces an error when the write fails (and does not notify the parent)', async () => {
    const onLogged = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    render(<LogCallForm familyId="fam-1" onLogged={onLogged} />);

    fireEvent.click(screen.getByTestId('deal-outcome-submit'));

    expect(await screen.findByTestId('deal-outcome-error')).toBeInTheDocument();
    expect(onLogged).not.toHaveBeenCalled();
  });
});
