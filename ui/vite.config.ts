import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The API is the sole Temporal client (CLAUDE.md §2); the UI only ever
    // talks to it. Proxying keeps that one origin, so SSE needs no CORS.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        // `agent: false` opts this proxy out of Node's pooled keep-alive
        // sockets. Session streams are long-lived and end when their workflow
        // does; pooled upstream sockets left over from finished streams were
        // observed blocking *new* proxied requests, which reach the dev server
        // but never reach the API at all — the session pane then sits at
        // "waiting for output" forever with nothing in the API log to show for
        // it. One socket per request costs nothing at demo scale and removes
        // the shared state that failure mode depends on.
        agent: false,
      },
    },
  },
});
