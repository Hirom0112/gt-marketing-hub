import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AiDrafts from '../AiDrafts';

// Acceptance test (CLAUDE §4.2) for the ungated detail-panel AI drafts (D-1). An
// email and an SMS draft each POST `/ai/enrollment/draft-ungated` with
// {family_id, channel}; the returned body lands in an EDITABLE textarea. There is
// deliberately NO eval-gate UI, NO approve/blocked/degraded banner, and NO send
// button — the human copies/sends manually (D-1, INV-2). Native fetch stubbed.

function mockDraftFetch(): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse((init?.body as string) ?? '{}');
    return {
      ok: true,
      status: 200,
      json: async () => ({
        proposal_id: `prop-${body.channel}`,
        channel: body.channel,
        degraded: false,
        body: `Generated ${body.channel} body for ${body.family_id}`,
        claims: [],
      }),
    };
  });
  vi.stubGlobal('fetch', fn);
  return fn as unknown as ReturnType<typeof vi.fn>;
}

describe('AiDrafts', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('requests an ungated EMAIL draft and shows it in an editable textarea', async () => {
    const fn = mockDraftFetch();
    render(<AiDrafts familyId="fam-1" />);

    fireEvent.click(screen.getByTestId('ai-draft-generate-email'));

    const area = (await screen.findByTestId(
      'ai-draft-body-email',
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(area.value).toContain('Generated email body'));

    // It hit the ungated endpoint with the email channel.
    const call = fn.mock.calls.find(([url, init]) => {
      if (!/\/ai\/enrollment\/draft-ungated$/.test(url as string)) return false;
      const b = JSON.parse((init as RequestInit).body as string);
      return b.channel === 'email';
    });
    expect(call).toBeTruthy();
    const reqBody = JSON.parse((call![1] as RequestInit).body as string);
    expect(reqBody.family_id).toBe('fam-1');

    // The textarea is EDITABLE (not readonly/disabled) and accepts edits.
    expect(area).not.toBeDisabled();
    expect(area).not.toHaveAttribute('readonly');
    fireEvent.change(area, { target: { value: 'human-edited email' } });
    expect(area.value).toBe('human-edited email');
  });

  it('requests an ungated SMS draft and shows it in an editable textarea', async () => {
    const fn = mockDraftFetch();
    render(<AiDrafts familyId="fam-1" />);

    fireEvent.click(screen.getByTestId('ai-draft-generate-sms'));

    const area = (await screen.findByTestId(
      'ai-draft-body-sms',
    )) as HTMLTextAreaElement;
    await waitFor(() => expect(area.value).toContain('Generated sms body'));

    const call = fn.mock.calls.find(([url, init]) => {
      if (!/\/ai\/enrollment\/draft-ungated$/.test(url as string)) return false;
      const b = JSON.parse((init as RequestInit).body as string);
      return b.channel === 'sms';
    });
    expect(call).toBeTruthy();

    expect(area).not.toBeDisabled();
    fireEvent.change(area, { target: { value: 'human-edited sms' } });
    expect(area.value).toBe('human-edited sms');
  });

  it('shows no eval-gate / approve / blocked / send UI (D-1)', async () => {
    mockDraftFetch();
    render(<AiDrafts familyId="fam-1" />);

    fireEvent.click(screen.getByTestId('ai-draft-generate-email'));
    await screen.findByTestId('ai-draft-body-email');

    // No gate banner, no approve/discard, no send button — the human is the gate.
    expect(screen.queryByText(/approve/i)).toBeNull();
    expect(screen.queryByText(/blocked/i)).toBeNull();
    expect(screen.queryByText(/degraded/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /send/i })).toBeNull();
    expect(screen.queryByTestId('ai-draft-degraded-email')).toBeNull();
  });
});
