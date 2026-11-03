import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The API is served by the same process that later serves ``web/dist``; during
// development Vite proxies ``/api`` to the FastAPI server (default :8000).
export default defineConfig(({ mode }) => {
  const preview = mode === "static-preview";
  return {
    base: preview ? "/github-radar/" : "/",
    // A preview must never load local dotenv files or copy arbitrary public files.
    envDir: preview ? false : undefined,
    envPrefix: preview ? [] : undefined,
    publicDir: preview ? false : undefined,
    plugins: [react(), ...(preview ? [{
      name: "static-preview-disclosure",
      transformIndexHtml(html: string) {
        return {
          html: html.replace("<title>github-radar</title>", "<title>GitHub Radar v0.1.0-preview · DEMO DATA</title>")
            .replace("github-radar — ecosystem intelligence across repositories, topics and developers.",
              "DEMO DATA: a static GitHub Radar preview with fictional repositories and synthetic metrics. No live analytics."),
          tags: [{ tag: "link", attrs: { rel: "icon", href: "data:," } },
            { tag: "meta", attrs: { "http-equiv": "Content-Security-Policy",
            content: "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'" } }],
        };
      },
    }] : [])],
    server: {
      host: "127.0.0.1",
      headers: { "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer" },
      proxy: preview ? undefined : {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: false,
        },
      },
    },
    build: {
      outDir: preview ? "dist-preview" : "dist",
      sourcemap: false,
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      globals: true,
    },
  };
});
