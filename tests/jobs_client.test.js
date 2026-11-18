"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const JobsClient = require(path.resolve(__dirname, "..", "site", "js", "jobs_client.js"));
const MB = 1024 * 1024;

test("decideMode: small file with few pages runs in the browser", () => {
  assert.equal(JobsClient.decideMode(50 * MB, 30), "browser");
});

test("decideMode: file over 50 MB goes to the server, even when not counted", () => {
  assert.equal(JobsClient.decideMode(50 * MB + 1, null), "server");
});

test("decideMode: more than 30 pages goes to the server", () => {
  assert.equal(JobsClient.decideMode(1000, 31), "server");
});

test("chunkRanges: last chunk is shorter, end is exclusive", () => {
  assert.deepEqual(JobsClient.chunkRanges(10, 4), [[0, 4], [4, 8], [8, 10]]);
});

test("chunkRanges: exact multiple gives whole chunks", () => {
  assert.equal(JobsClient.chunkRanges(8, 4).length, 2);
  assert.deepEqual(JobsClient.chunkRanges(8, 4), [[0, 4], [4, 8]]);
});

test("chunkRanges: zero-byte file has no chunks", () => {
  assert.deepEqual(JobsClient.chunkRanges(0, 4), []);
});

test("formatEta: unknown estimate", () => {
  assert.equal(JobsClient.formatEta(null), "กำลังประเมิน…");
});

test("formatEta: under a minute", () => {
  assert.equal(JobsClient.formatEta(59), "ไม่ถึง 1 นาที");
  assert.equal(JobsClient.formatEta(0), "ไม่ถึง 1 นาที");
});

test("formatEta: minutes only", () => {
  assert.equal(JobsClient.formatEta(120), "ประมาณ 2 นาที");
});

test("formatEta: hours and minutes", () => {
  assert.equal(JobsClient.formatEta(3720), "ประมาณ 1 ชม. 2 นาที");
});

test("formatBytes: megabytes with one decimal", () => {
  assert.equal(JobsClient.formatBytes(12.3 * MB), "12.3 MB");
  assert.equal(JobsClient.formatBytes(50 * MB), "50.0 MB");
});

test("formatBytes: small values stay in bytes", () => {
  assert.equal(JobsClient.formatBytes(512), "512 B");
});

const ORIGIN = "https://ocr.example";
const BS = "\x5c";   // backslash
const TAB = "\x09";
const LF = "\x0a";
const CR = "\x0d";

test("safeNext: same-site paths are kept with query and hash", () => {
  assert.equal(JobsClient.safeNext("/admin", ORIGIN), "/admin");
  assert.equal(JobsClient.safeNext("/?job=abc", ORIGIN), "/?job=abc");
  assert.equal(JobsClient.safeNext("/a/b?x=1#h", ORIGIN), "/a/b?x=1#h");
});

test("safeNext: protocol-relative, backslash and absolute off-site targets fall back to /", () => {
  assert.equal(JobsClient.safeNext("//evil.com", ORIGIN), "/");
  assert.equal(JobsClient.safeNext("/" + BS + "evil.com", ORIGIN), "/");
  assert.equal(JobsClient.safeNext("https://evil.com", ORIGIN), "/");
});

test("safeNext: tab, LF or CR inside the value is rejected (decoded from /%09/evil.com etc.)", () => {
  assert.equal(JobsClient.safeNext("/" + TAB + "/evil.com", ORIGIN), "/");
  assert.equal(JobsClient.safeNext("/" + LF + "/evil.com", ORIGIN), "/");
  assert.equal(JobsClient.safeNext("/" + CR + "/evil.com", ORIGIN), "/");
});

test("safeNext: empty, missing, relative or non-string values fall back to /", () => {
  assert.equal(JobsClient.safeNext(null, ORIGIN), "/");
  assert.equal(JobsClient.safeNext("", ORIGIN), "/");
  assert.equal(JobsClient.safeNext("admin", ORIGIN), "/");
  assert.equal(JobsClient.safeNext(42, ORIGIN), "/");
});

// login.html carries its own copy of safeNext (the page is public, so it cannot load
// js/jobs_client.js). Run the same cases against that copy so the two cannot drift.
test("login.html safeNext matches JobsClient.safeNext on the same cases", () => {
  const fs = require("node:fs");
  const html = fs.readFileSync(path.resolve(__dirname, "..", "site", "login.html"), "utf8");
  const start = html.indexOf("function safeNext(value, origin)");
  assert.ok(start >= 0, "safeNext not found in login.html");
  const end = html.indexOf("\x0a  }\x0a", start) + 5;
  const loginSafeNext = new Function(html.slice(start, end) + "\nreturn safeNext;")();
  const cases = [
    ["/admin", "/admin"],
    ["/?job=abc", "/?job=abc"],
    ["//evil.com", "/"],
    ["/" + BS + "evil.com", "/"],
    ["https://evil.com", "/"],
    ["/" + TAB + "/evil.com", "/"],
    ["/" + LF + "/evil.com", "/"],
    ["/" + CR + "/evil.com", "/"],
    [null, "/"],
    ["admin", "/"],
  ];
  for (const [input, expected] of cases) {
    assert.equal(loginSafeNext(input, ORIGIN), expected, "input " + JSON.stringify(input));
    assert.equal(loginSafeNext(input, ORIGIN), JobsClient.safeNext(input, ORIGIN));
  }
});

test("isRetryableStatus: network-ish and server errors retry, other 4xx do not", () => {
  for (const status of [408, 429, 500, 502, 503, 599]) {
    assert.equal(JobsClient.isRetryableStatus(status), true, String(status));
  }
  for (const status of [200, 400, 401, 403, 404, 409, 413, 422]) {
    assert.equal(JobsClient.isRetryableStatus(status), false, String(status));
  }
});

test("chunkRetryDelayMs: waits 1 s, 2 s, then 4 s between attempts", () => {
  assert.equal(JobsClient.chunkRetryDelayMs(0), 1000);
  assert.equal(JobsClient.chunkRetryDelayMs(1), 2000);
  assert.equal(JobsClient.chunkRetryDelayMs(2), 4000);
});
