"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ThaiText = require(path.resolve(__dirname, "..", "site", "js", "thai_text.js"));
const cases = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "fixtures", "text_cases.json"), "utf8"),
);

test("fixture file has the expected 33 golden cases", () => {
  assert.equal(cases.length, 33);
});

for (const c of cases) {
  test(`golden case ${JSON.stringify(c.in)}`, () => {
    const canon = ThaiText.canonical(c.in);
    assert.equal(canon, c.canonical, "canonical");
    assert.equal(ThaiText.fixScripts(canon), c.fix_scripts, "fixScripts(canonical)");
    assert.ok(
      Math.abs(ThaiText.invalidShare(c.in) - c.invalid_share) < 1e-9,
      "invalidShare",
    );
    assert.equal(ThaiText.enumNumber(c.in), c.enum, "enumNumber");
  });
}

test("exports DIGITS as a Set of Arabic and Thai digit strings", () => {
  assert.ok(ThaiText.DIGITS instanceof Set);
  assert.equal(ThaiText.DIGITS.size, 20);
  assert.ok(ThaiText.DIGITS.has("0"));
  assert.ok(ThaiText.DIGITS.has("๙"));
});

test("isThai is true only for code points U+0E00..U+0E7F", () => {
  assert.equal(ThaiText.isThai("ก"), true);
  assert.equal(ThaiText.isThai("฿"), true);
  assert.equal(ThaiText.isThai("a"), false);
});

test("canonical strips only Python-style whitespace per line", () => {
  // Python str.strip() also removes U+001C..U+001F and U+0085.
  assert.equal(ThaiText.canonical("\u001Cก\u0085"), "ก");
});

test("canonical replaces every CRLF and CR, not just the first", () => {
  assert.equal(ThaiText.canonical("a\r\nb\r\nc\rd"), "a\nb\nc\nd");
});
