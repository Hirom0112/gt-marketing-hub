// Vitest setup (TECH_STACK §3.3: Vitest + React Testing Library).
// Registers jest-dom matchers (e.g. toBeInTheDocument) and tears down
// the DOM between tests.
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});
