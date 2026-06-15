// Runtime config for the front end.
//
// `apiBaseUrl` is the base URL the React app calls (TECH_STACK §5.1
// GT_API_BASE_URL, injected at build as VITE_GT_API_BASE_URL). No real
// API calls are made yet — this is the seam later slices read from.
export const DEFAULT_API_BASE_URL = 'http://localhost:8000';

export const apiBaseUrl: string =
  import.meta.env.VITE_GT_API_BASE_URL ?? DEFAULT_API_BASE_URL;
