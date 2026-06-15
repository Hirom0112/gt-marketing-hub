/// <reference types="vite/client" />

// Front-end env surface. The only var the client reads today is
// VITE_GT_API_BASE_URL (build-time injection of GT_API_BASE_URL,
// TECH_STACK §5.1). The canonical registry lives in TECH_STACK §5 —
// add new vars there, not here.
interface ImportMetaEnv {
  readonly VITE_GT_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
