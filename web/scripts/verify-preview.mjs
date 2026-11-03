/* global document, window */
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { URL } from "node:url";
import process from "node:process";
import console from "node:console";

// Optional test tools live outside the application lock/runtime; see STATIC_PREVIEW.md.
const require = createRequire(resolve(process.argv[2], "package.json"));
const { chromium } = require("playwright");
const AxeBuilder = require("@axe-core/playwright").default;
const base = process.argv[3] ?? "http://127.0.0.1:4173/github-radar/";
assert(["localhost", "127.0.0.1"].includes(new URL(base).hostname), "Use an isolated loopback server");
const output = resolve("../build/preview-evidence");
mkdirSync(output, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PREVIEW_CHROMIUM ?? "/usr/bin/chromium", headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const failures = [], requests = [], pages = [];
context.on("request", request => {
  requests.push({ url: request.url(), type: request.resourceType() });
  if (["fetch", "xhr", "websocket"].includes(request.resourceType())) failures.push(`Background request: ${request.url()}`);
});
await context.route("**/*", route => {
  const url = new URL(route.request().url());
  if (url.origin !== new URL(base).origin || !url.pathname.startsWith("/github-radar/") || url.pathname.includes("/api/")) {
    failures.push(`Unexpected request blocked: ${url.origin}${url.pathname}`);
    return route.abort();
  }
  return route.continue();
});
page.on("pageerror", error => failures.push(error.message));
page.on("console", message => { if (["error", "warning"].includes(message.type())) failures.push(message.text()); });
page.on("response", response => { if (response.status() >= 400) failures.push(`HTTP ${response.status()}: ${response.url()}`); });
const routes = [
  ["/", "Explore"], ["/repositories", "Repositories"],
  ["/repositories/demo-lab/signal-kit", "demo-lab/signal-kit"],
  ["/repositories/demo-lab/new-spark", "demo-lab/new-spark"],
  ["/topics", "Topics"], ["/topics/developer-tools", "Topic: developer-tools"],
  ["/topics/demo-empty", "Topic: demo-empty"], ["/developers", "Developers"],
  ["/ecosystem", "Ecosystem"], ["/bridges", "Bridges"],
  ["/ai", "Unavailable in preview"], ["/workspace/repositories", "Unavailable in preview"],
];
try {
  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [route, heading] of routes) {
      await page.goto(`${base}#${route}`);
      await page.getByRole("heading", { name: heading, exact: true }).waitFor();
      if (route === "/ecosystem") await page.getByRole("group", { name: /Ecosystem graph/ }).waitFor();
      const geometry = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }));
      const violations = width === 320 ? (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze()).violations : [];
      const result = { route, width, ...geometry, violations: violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target) })) };
      pages.push(result);
      if (geometry.document > width) failures.push(`Overflow at ${width}: ${route} (${geometry.document}px)`);
      if (violations.length) failures.push(`Accessibility: ${route}: ${violations.map(v => v.id).join(", ")}`);
      assert(await page.getByRole("complementary", { name: "Demo data disclosure" }).isVisible());
      if ((width === 320 && ["/repositories/demo-lab/signal-kit", "/topics/developer-tools"].includes(route)) || (width === 1440 && ["/", "/ecosystem"].includes(route))) {
        await page.screenshot({ path: resolve(output, `${width}-${route.replaceAll("/", "_") || "home"}.png`), fullPage: true });
      }
    }
  }
  await page.setViewportSize({ width: 320, height: 900 });
  await page.goto(`${base}#/repositories/demo-lab/signal-kit`);
  await page.getByRole("heading", { name: "demo-lab/signal-kit", exact: true }).waitFor();
  await page.reload();
  await page.getByRole("heading", { name: "demo-lab/signal-kit", exact: true }).waitFor();
  await page.keyboard.press("Tab");
  assert.equal(await page.locator(":focus").innerText(), "Skip to content");
  await page.keyboard.press("Enter");
  assert.equal(await page.locator(":focus").getAttribute("id"), "main");
  assert(page.url().endsWith("#/repositories/demo-lab/signal-kit"));
  await page.getByRole("link", { name: "developer-tools", exact: true }).click();
  await page.getByRole("heading", { name: "Topic: developer-tools" }).waitFor();
  const table = page.getByRole("region", { name: "Topic repositories table" });
  await table.focus(); await page.keyboard.press("ArrowRight");
  await page.waitForFunction(() => (document.querySelector(".table-scroll")?.scrollLeft ?? 0) > 0);
  assert(await table.evaluate(element => element.scrollLeft > 0), "Wide table must scroll by keyboard");
  await page.goto(`${base}#/repositories`);
  const language = page.getByRole("searchbox", { name: "Language" });
  await language.click(); await page.keyboard.type("TypeScript", { delay: 30 });
  assert.equal(await language.inputValue(), "TypeScript", "Filtering must preserve keyboard focus");
  await page.getByRole("link", { name: "demo-lab/signal-kit", exact: true }).waitFor();
  await page.getByRole("checkbox", { name: "Hide forks" }).check();
  await page.getByRole("link", { name: "demo-lab/signal-kit-fork", exact: true }).waitFor({ state: "hidden" });
  await language.fill("unknown");
  await page.getByText("No repositories match the current filters.").waitFor();
  await page.goto(`${base}#/ecosystem`);
  const node = page.getByRole("button", { name: /demo-lab\/signal-kit$/ });
  await node.focus(); await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Focused node: demo-lab/signal-kit" }).waitFor();
  await page.getByRole("link", { name: "Open repository page →" }).click();
  await page.getByRole("heading", { name: "demo-lab/signal-kit", exact: true }).waitFor();
  await page.goto(`${base}#/repositories/demo-lab/missing`);
  await page.getByRole("heading", { name: "Demo record unavailable" }).waitFor();
  await page.goto(`${base}#/not-a-route`);
  await page.getByText(/not found/i).first().waitFor();
} catch (error) { failures.push(String(error)); }
finally {
  await browser.close();
  writeFileSync(resolve(output, "browser.json"), JSON.stringify({ pages, requests, failures }, null, 2));
}
console.log(JSON.stringify({ responsive_pages: pages.length, accessibility_pages: pages.filter(p => p.width === 320).length, failures }, null, 2));
assert.deepEqual(failures, []);
