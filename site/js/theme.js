"use strict";
// Shared light/dark theme switch. Loaded in <head> before the stylesheets so the
// stored theme is on <html data-theme> before the first paint. Light is the
// default; the OS colour scheme is never consulted.
(function () {
  const STORAGE_KEY = "ocr-theme";
  const LABEL_TO_DARK = "เปลี่ยนเป็นโหมดมืด";
  const LABEL_TO_LIGHT = "เปลี่ยนเป็นโหมดสว่าง";

  function resolveTheme(stored) {
    return stored === "dark" ? "dark" : "light";
  }

  function nextTheme(t) {
    return resolveTheme(t) === "dark" ? "light" : "dark";
  }

  function readStored(storage) {
    try {
      const value = storage.getItem(STORAGE_KEY);
      return typeof value === "string" ? value : null;
    } catch (err) {
      return null;
    }
  }

  function writeStored(storage, t) {
    try {
      storage.setItem(STORAGE_KEY, t);
    } catch (err) {
      // Storage is blocked or full: the theme still applies for this page view.
    }
  }

  function apply(docEl, t) {
    docEl.dataset.theme = t;
    docEl.style.colorScheme = t;
  }

  function storageOf(win) {
    try {
      return win.localStorage;
    } catch (err) {
      return null;
    }
  }

  function syncButtons(win, t) {
    const doc = win.document;
    if (!doc || typeof doc.querySelectorAll !== "function") return;
    const label = t === "dark" ? LABEL_TO_LIGHT : LABEL_TO_DARK;
    for (const button of doc.querySelectorAll("[data-theme-toggle]")) {
      button.setAttribute("aria-label", label);
      button.setAttribute("aria-pressed", String(t === "dark"));
    }
  }

  function init(win) {
    const t = resolveTheme(readStored(storageOf(win)));
    apply(win.document.documentElement, t);
    syncButtons(win, t);
    return t;
  }

  function toggle(win) {
    const docEl = win.document.documentElement;
    const next = nextTheme(docEl.dataset ? docEl.dataset.theme : undefined);
    apply(docEl, next);
    writeStored(storageOf(win), next);
    syncButtons(win, next);
    return next;
  }

  const Theme = {
    STORAGE_KEY,
    resolveTheme,
    nextTheme,
    readStored,
    writeStored,
    apply,
    init,
    toggle,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = Theme;
  else self.Theme = Theme;

  if (typeof window !== "undefined" && window.document) {
    init(window);
    window.document.addEventListener("click", function (ev) {
      const target = ev.target;
      if (target && typeof target.closest === "function" && target.closest("[data-theme-toggle]")) {
        toggle(window);
      }
    });
  }
})();
