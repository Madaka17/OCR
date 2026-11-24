"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const Theme = require(path.resolve(__dirname, "..", "site", "js", "theme.js"));

function fakeWin({ getItem, setItem, stored } = {}) {
  const data = new Map();
  if (stored !== undefined) data.set("ocr-theme", stored);
  const localStorage = {
    getItem: getItem || ((k) => (data.has(k) ? data.get(k) : null)),
    setItem: setItem || ((k, v) => data.set(k, String(v))),
  };
  const documentElement = { dataset: {}, style: {} };
  const buttons = [];
  return {
    localStorage,
    document: {
      documentElement,
      querySelectorAll(sel) {
        return sel === "[data-theme-toggle]" ? buttons : [];
      },
    },
    _buttons: buttons,
    _data: data,
  };
}

test("resolveTheme maps unknown values to light and keeps dark", () => {
  assert.equal(Theme.resolveTheme(undefined), "light");
  assert.equal(Theme.resolveTheme("x"), "light");
  assert.equal(Theme.resolveTheme(null), "light");
  assert.equal(Theme.resolveTheme("dark"), "dark");
});

test("nextTheme flips the theme", () => {
  assert.equal(Theme.nextTheme("light"), "dark");
  assert.equal(Theme.nextTheme("dark"), "light");
});

test("STORAGE_KEY is ocr-theme", () => {
  assert.equal(Theme.STORAGE_KEY, "ocr-theme");
});

test("init applies stored dark before body", () => {
  const win = fakeWin({ stored: "dark" });
  const theme = Theme.init(win);
  assert.equal(theme, "dark");
  assert.equal(win.document.documentElement.dataset.theme, "dark");
  assert.equal(win.document.documentElement.style.colorScheme, "dark");
});

test("storage throwing falls back to light and toggle still works", () => {
  const boom = () => {
    throw new Error("blocked");
  };
  const win = fakeWin({ getItem: boom, setItem: boom });
  assert.equal(Theme.init(win), "light");
  assert.equal(win.document.documentElement.dataset.theme, "light");
  let next;
  assert.doesNotThrow(() => {
    next = Theme.toggle(win);
  });
  assert.equal(next, "dark");
  assert.equal(win.document.documentElement.dataset.theme, "dark");
});

test("readStored and writeStored never throw and round-trip", () => {
  assert.equal(Theme.readStored(null), null);
  assert.doesNotThrow(() => Theme.writeStored(null, "dark"));
  const store = fakeWin().localStorage;
  Theme.writeStored(store, "dark");
  assert.equal(Theme.readStored(store), "dark");
});

test("toggle stores the new theme and updates toggle buttons", () => {
  const win = fakeWin({ stored: "light" });
  const button = { attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); } };
  win._buttons.push(button);
  Theme.init(win);
  Theme.toggle(win);
  assert.equal(win._data.get("ocr-theme"), "dark");
  assert.equal(button.attrs["aria-pressed"], "true");
  assert.equal(button.attrs["aria-label"], "เปลี่ยนเป็นโหมดสว่าง");
  Theme.toggle(win);
  assert.equal(win._data.get("ocr-theme"), "light");
  assert.equal(button.attrs["aria-pressed"], "false");
  assert.equal(button.attrs["aria-label"], "เปลี่ยนเป็นโหมดมืด");
});
