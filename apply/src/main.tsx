import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { getSupabase } from './lib/supabase';
import type { MinimalSupabase } from './lib/apply';
import {
  asDemoSupabase,
  loadDemoSessions,
  type DemoSessionClient,
} from './lib/demo';
import './theme.css';

const client = getSupabase();

// MD demo family-switcher wiring: when the director's seed supplied per-family
// anon-session tokens (VITE_DEMO_SESSIONS), wrap the REAL anon client as a
// DemoSupabase so `isDemoSupabase` is true and the switcher works live; otherwise
// inject the bare client exactly as before so the switcher stays disabled (the
// honest "no seeded families" state). Anon-only either way (INV-5).
const sessions = client ? loadDemoSessions() : {};
const sb =
  client && Object.keys(sessions).length > 0
    ? asDemoSupabase(client as unknown as DemoSessionClient, sessions)
    : client;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {sb ? (
      <App supabase={sb as unknown as MinimalSupabase} />
    ) : (
      <div className="shell">
        <p>
          Supabase env is not configured. Set <code>VITE_SUPABASE_URL</code> and{' '}
          <code>VITE_SUPABASE_ANON_KEY</code>.
        </p>
      </div>
    )}
  </React.StrictMode>,
);
