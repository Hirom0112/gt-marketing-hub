import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from '../App';
import { DEFAULT_API_BASE_URL } from '../config';

describe('App shell', () => {
  it('renders the cockpit app shell', () => {
    render(<App />);
    expect(
      screen.getByRole('heading', { name: /gt growth cockpit/i }),
    ).toBeInTheDocument();
  });

  it('exposes the API base URL from config', () => {
    render(<App />);
    // No env override in the test runner, so we fall back to the default.
    expect(screen.getByTestId('api-base-url')).toHaveTextContent(
      DEFAULT_API_BASE_URL,
    );
  });
});
