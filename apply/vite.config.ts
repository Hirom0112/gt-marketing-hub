/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite + Vitest config for the S14 mock apply SPA (mirrors frontend/vite.config.ts).
// The app reads VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY (anon key ONLY — INV-5:
// the service_role key never enters a VITE_* var or this client bundle).
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
