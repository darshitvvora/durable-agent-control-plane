import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The API is the sole Temporal client (CLAUDE.md §2); the UI only ever
    // talks to it. Proxying keeps that one origin, so SSE needs no CORS.
    proxy: { "/api": "http://localhost:8000" },
  },
});
