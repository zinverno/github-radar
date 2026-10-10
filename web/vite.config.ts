import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API is served by the same process that later serves ``web/dist``; during
// development Vite proxies ``/api`` to the FastAPI server (default :8000).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
  },
});