/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server forwards /api to the backend, so the pages and the API share one origin (no
// CORS). In the container the backend on the laptop is host.docker.internal (CMP_API_URL).
const api = process.env.CMP_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: false } },
    watch: { usePolling: !!process.env.CMP_POLL }, // Windows bind mounts miss file events
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
  },
});
