import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { getSupabase } from './lib/supabase';
import type { MinimalSupabase } from './lib/apply';
import './theme.css';

const sb = getSupabase();

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
