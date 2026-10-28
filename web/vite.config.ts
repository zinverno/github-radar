import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The API is served by the same process that later serves ``web/dist``; during
// development Vite proxies ``/api`` to the FastAPI server (default :8000).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    headers: { "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer" },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});
