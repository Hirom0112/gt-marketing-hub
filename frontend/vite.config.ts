/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite + Vitest config (TECH_STACK §3.3). The front end reads
// VITE_GT_API_BASE_URL (TECH_STACK §5.1 GT_API_BASE_URL, injected at build).
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
