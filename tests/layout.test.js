"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const Layout = require(path.resolve(__dirname, "..", "site", "js", "layout.js"));
const FIXTURE_DIR = path.resolve(__dirname, "fixtures");
const FIXTURE_NAMES = [
  "paragraph", "two_columns", "table", "numbered_across", "sideways", "skewed",
  "upside_down", "single_line", "color_banner", "blank", "marks", "margin_note",
];

function loadFixture(name) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, name + ".json"), "utf8"));
}

function recFor(texts, confs) {
  return new Map(texts.map((t, i) => [i, [t, confs[i]]]));
}

function matmul(a, b) {
  const out = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++)
      for (let k = 0; k < 3; k++) out[i][j] += a[i][k] * b[k][j];
  return out;
}

test("median: even count is the mean of the two middle values", () => {
  assert.equal(Layout.median([1, 2, 3, 4]), 2.5);
  assert.equal(Layout.median([5, 1, 3]), 3);
});

test("invert3(rot90Matrix(1, [10, 20])) times rot90Matrix(1, [10, 20]) is identity", () => {
  const m = Layout.rot90Matrix(1, [10, 20]);
  const p = matmul(Layout.invert3(m), m);
  for (let i = 0; i < 3; i++)
    for (let j = 0; j < 3; j++)
      assert.ok(Math.abs(p[i][j] - (i === j ? 1 : 0)) < 1e-9, `p[${i}][${j}]=${p[i][j]}`);
});

for (const name of FIXTURE_NAMES) {
  const fx = loadFixture(name);
  const page = fx.page;
  if (page.boxes.length === 0) {
    test(`${name}: empty page is skipped`, () => {
      assert.equal(page.boxes.length, 0);
    });
    continue;
  }

  test(`${name}: readingOrder matches Python rows`, () => {
    assert.deepEqual(Layout.readingOrder(page.boxes, page.angle, page.texts), page.rows);
  });

  test(`${name}: fragments match Python`, () => {
    const got = [...Layout.fragments(page.boxes, recFor(page.texts, page.confs))].sort((a, b) => a - b);
    assert.deepEqual(got, [...page.fragments].sort((a, b) => a - b));
  });

  test(`${name}: pageSkew matches Python angle`, () => {
    assert.ok(Math.abs(Layout.pageSkew(page.boxes) - page.angle) < 1e-4);
  });
}

const cases = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, "fragment_cases.json"), "utf8"));
for (const c of cases) {
  test(`fragment case ${c.name}`, () => {
    const got = [...Layout.fragments(c.boxes, recFor(c.texts, c.confs))].sort((a, b) => a - b);
    assert.deepEqual(got, c.expected);
  });
}
