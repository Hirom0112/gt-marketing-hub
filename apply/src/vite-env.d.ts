/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  // Demo-only (MD): the cockpit URL for the pages-dropbox quick-jump, and the
  // seeded synthetic demo cohort the family-switcher lists (a JSON array of
  // {uid, familyId, label, hint}). Both are demo/seed-time config, synthetic-only.
  readonly VITE_COCKPIT_URL?: string;
  readonly VITE_DEMO_FAMILIES?: string;
  // Demo-only (MD): the seeded per-family anon-session tokens the production
  // family-switcher restores from — a JSON object mapping uid →
  // {access_token, refresh_token}. Synthetic anon tokens only (INV-1/INV-5),
  // consumed by asDemoSupabase, never shown in the UI. Unset ⇒ switcher disabled.
  readonly VITE_DEMO_SESSIONS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
