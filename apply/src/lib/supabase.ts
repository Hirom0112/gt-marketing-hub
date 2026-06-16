// Supabase client — anon key ONLY (INV-5).
//
// This client app uses exclusively the publishable anon key. The service_role key
// is server-only (D-RLS-4) and is NEVER read here, never placed in a VITE_* var,
// and never bundled. RLS (deny-by-default, owner-scoped + null-guarded) is the
// boundary; the anon key is safe client-side precisely because of that doctrine
// (the literal fix for the disclosed IDOR).

import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

let client: SupabaseClient | null = null;

/**
 * Lazily build the singleton Supabase client. Returns null when env is absent
 * (e.g. in unit tests, which inject a mock client instead) so the module is
 * import-safe without credentials.
 */
export function getSupabase(): SupabaseClient | null {
  if (client) return client;
  if (!url || !anonKey) return null;
  client = createClient(url, anonKey, {
    auth: {
      // Anonymous sessions persist in-tab so all of a family's rows share one
      // auth.uid() across the multi-step flow.
      persistSession: true,
      autoRefreshToken: true,
    },
  });
  return client;
}
