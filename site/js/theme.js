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

  // Sets every [data-theme-toggle] button to the theme currently on <html>.
  // Safe to call before the buttons exist: it simply finds none.
  function syncButtons(win) {
    const doc = win.document;
    if (!doc || typeof doc.querySelectorAll !== "function") return;
    const t = resolveTheme(doc.documentElement.dataset ? doc.documentElement.dataset.theme : undefined);
    const label = t === "dark" ? LABEL_TO_LIGHT : LABEL_TO_DARK;
    for (const button of doc.querySelectorAll("[data-theme-toggle]")) {
      button.setAttribute("aria-label", label);
      // The label carries the state; aria-pressed would contradict it.
      button.removeAttribute("aria-pressed");
    }
  }

  function init(win) {
    const t = resolveTheme(readStored(storageOf(win)));
    apply(win.document.documentElement, t);
    syncButtons(win);
    return t;
  }

  function toggle(win) {
    const docEl = win.document.documentElement;
    const next = nextTheme(docEl.dataset ? docEl.dataset.theme : undefined);
    apply(docEl, next);
    writeStored(storageOf(win), next);
    syncButtons(win);
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
    syncButtons,
    toggle,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = Theme;
  else self.Theme = Theme;

  if (typeof window !== "undefined" && window.document) {
    init(window);
    // Buttons are in <body>, which does not exist yet while this runs in <head>.
    if (window.document.readyState === "loading") {
      window.document.addEventListener("DOMContentLoaded", function () {
        syncButtons(window);
      });
    } else {
      syncButtons(window);
    }
    window.document.addEventListener("click", function (ev) {
      const target = ev.target;
      if (target && typeof target.closest === "function" && target.closest("[data-theme-toggle]")) {
        toggle(window);
      }
    });
  }
})();
