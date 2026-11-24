"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const SITE = path.resolve(__dirname, "..", "site");
const PAGES = ["index.html", "login.html", "admin.html"];

function read(page) {
  return fs.readFileSync(path.join(SITE, page), "utf8");
}

function head(html) {
  const end = html.indexOf("</head>");
  assert.ok(end > 0, "page has a </head>");
  return html.slice(0, end);
}

function styleBlocks(html) {
  return [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]);
}

for (const page of PAGES) {
  test(`${page}: head loads theme.js before stylesheets`, () => {
    const h = head(read(page));
    const js = h.indexOf("js/theme.js");
    assert.ok(js >= 0, "js/theme.js is loaded in head");
    const firstStyle = h.indexOf("<style");
    if (firstStyle >= 0) assert.ok(js < firstStyle, "theme.js comes before the first <style");
    const css = h.indexOf("css/theme.css");
    assert.ok(css >= 0, "css/theme.css is linked in head");
    assert.ok(js < css, "theme.js comes before css/theme.css");
  });

  test(`${page}: every getElementById id exists in the markup`, () => {
    const html = read(page);
    const ids = new Set([...html.matchAll(/getElementById\("([^"]+)"\)/g)].map((m) => m[1]));
    for (const id of ids) {
      assert.ok(html.includes(`id="${id}"`), `id="${id}" is in the markup`);
    }
  });

  test(`${page}: no hard-coded colours in the page style blocks`, () => {
    const blocks = styleBlocks(read(page));
    for (const raw of blocks) {
      const css = raw
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/var\([^)]*\)/g, "");
      assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "no #hex colour");
      assert.doesNotMatch(css, /rgba?\(/, "no rgb()/rgba() colour");
    }
  });
}
