/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Render the live token stream in the session pane. Off unless "true" —
   *  see SessionTerminal.tsx for why this is a UI switch and not a worker one. */
  readonly VITE_SHOW_TOKEN_STREAM?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
