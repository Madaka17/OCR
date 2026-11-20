"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ADMIN_HTML = path.resolve(__dirname, "..", "site", "admin.html");

// Runs the page's inline script against a stub DOM and a stub fetch, so the
// test sees what the admin page shows for a given /api/admin/overview body.
function loadAdmin(overview) {
  const html = fs.readFileSync(ADMIN_HTML, "utf8");
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

  const byId = {};
  function makeEl(id) {
    return {
      id: id || "",
      textContent: "",
      className: "",
      hidden: false,
      style: {},
      children: [],
      appendChild(child) { this.children.push(child); return child; },
      addEventListener() {},
    };
  }
  const document = {
    getElementById(id) {
      if (!byId[id]) byId[id] = makeEl(id);
      return byId[id];
    },
    createElement() { return makeEl(""); },
  };
  const sandbox = {
    document,
    window: { location: { href: "" }, setInterval() { return 0; }, confirm() { return false; } },
    fetch() {
      return Promise.resolve({
        status: 200,
        ok: true,
        json: () => Promise.resolve(overview),
      });
    },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox);
  return { byId, settle: () => new Promise((r) => setTimeout(r, 0)) };
}

const BASE = {
  resources: { cpu_percent: 5, ram_used_gb: 4, ram_total_gb: 16, disk_free_gb: 100, data_dir_gb: 1 },
  workers: { alive: true, count: 1 },
  stats: {
    avg_seconds_per_page: null, pages_7d: 0, failed_pages_7d: 0,
    pages_per_hour: [], jobs_per_day: [],
  },
  jobs: [],
  errors: [],
};

test("summary section: feature off shows the off message and no details", async () => {
  const page = loadAdmin({ ...BASE, llm: { enabled: false } });
  await page.settle();

  assert.equal(page.byId["llm-off"].hidden, false);
  // The off message is static markup, so the stub DOM never sees it; check the source.
  assert.match(
    fs.readFileSync(ADMIN_HTML, "utf8"),
    /<p class="empty" id="llm-off" hidden>ปิดอยู่ \(ตั้ง OCR_LLM_URL เพื่อเปิด\)<\/p>/,
  );
  assert.equal(page.byId["llm-on"].hidden, true);
});

test("summary section: feature on shows model, url, never-succeeded and counts", async () => {
  const page = loadAdmin({
    ...BASE,
    llm: {
      enabled: true,
      url: "http://llm.local:8080/v1",
      model: "local-model",
      last_ok_at: null,
      last_error: null,
      counts: { pending: 3, running: 1, done: 5, failed: 2, short: 4 },
    },
  });
  await page.settle();

  assert.equal(page.byId["llm-off"].hidden, true);
  assert.equal(page.byId["llm-on"].hidden, false);
  assert.equal(page.byId["llm-model"].textContent, "local-model");
  assert.equal(page.byId["llm-url"].textContent, "http://llm.local:8080/v1");
  assert.equal(page.byId["llm-ok"].textContent, "ยังไม่เคย");
  assert.equal(page.byId["llm-error"].textContent, "-");
  assert.equal(page.byId["llm-pending"].textContent, "3");
  assert.equal(page.byId["llm-running"].textContent, "1");
  assert.equal(page.byId["llm-done"].textContent, "5");
  assert.equal(page.byId["llm-failed"].textContent, "2");
  assert.equal(page.byId["llm-short"].textContent, "4");
});

test("summary section: last success is formatted like other timestamps", async () => {
  const ts = 1795000000;
  const page = loadAdmin({
    ...BASE,
    llm: {
      enabled: true, url: "u", model: "m", last_ok_at: ts, last_error: null,
      counts: { pending: 0, running: 0, done: 0, failed: 0, short: 0 },
    },
  });
  await page.settle();

  const expected = new Date(ts * 1000).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "medium" });
  assert.equal(page.byId["llm-ok"].textContent, expected);
});

test("summary section: last error is cut to 200 characters", async () => {
  const longError = "ก".repeat(250);
  const page = loadAdmin({
    ...BASE,
    llm: {
      enabled: true, url: "u", model: "m", last_ok_at: null, last_error: longError,
      counts: { pending: 0, running: 0, done: 0, failed: 0, short: 0 },
    },
  });
  await page.settle();

  assert.equal(page.byId["llm-error"].textContent, "ก".repeat(200));
});

test("summary section: server strings are written as text, not markup", async () => {
  const page = loadAdmin({
    ...BASE,
    llm: {
      enabled: true, url: "<img src=x>", model: "<b>m</b>", last_ok_at: null,
      last_error: "<script>x</script>",
      counts: { pending: 0, running: 0, done: 0, failed: 0, short: 0 },
    },
  });
  await page.settle();

  assert.equal(page.byId["llm-url"].textContent, "<img src=x>");
  assert.equal(page.byId["llm-model"].textContent, "<b>m</b>");
  assert.equal(page.byId["llm-error"].textContent, "<script>x</script>");
});
