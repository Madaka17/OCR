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

test("login.html: blue login markup contract", () => {
  const html = read("login.html");
  assert.match(html, /<h1[^>]*>\s*เข้าสู่ระบบ\s*<\/h1>/, "visually hidden h1 heading");
  assert.match(html, /<label[^>]*for="password"[^>]*>\s*รหัสผ่าน\s*<\/label>/, "password label");
  assert.match(html, /ลืมรหัสผ่าน ติดต่อผู้ดูแล/, "forgot-password hint copy");
  assert.match(html, /<button[^>]*data-theme-toggle[^>]*aria-label="เปลี่ยนเป็นโหมดมืด"/, "theme toggle button");
  assert.doesNotMatch(html, /data-theme-toggle[^>]*aria-pressed/, "theme toggle has no aria-pressed");
  assert.match(html, /class="streak"[^>]*aria-hidden="true"/, "decorative streaks are aria-hidden");
});

test("index.html: sidebar shell contract", () => {
  const html = read("index.html");
  const body = html.slice(html.indexOf("<body"));
  // Header: drawer toggle, doc title, new-job button, search inside the header.
  assert.match(body, /<header class="app-header"[\s\S]*?<\/header>/, "app-header present");
  assert.match(body, /<button[^>]*id="menu-btn"[^>]*aria-label="เมนู"[^>]*aria-expanded="false"/, "menu button with aria-expanded");
  assert.match(body, /id="doc-title"[^>]*>\s*งานใหม่\s*</, "doc title defaults to งานใหม่");
  assert.match(body, /<button[^>]*id="new-job-btn"[^>]*>\s*งานใหม่\s*</, "new job button");
  assert.match(body, /<div class="drawer-overlay"[^>]*hidden/, "drawer overlay starts hidden");
  // Content and composer regions.
  assert.match(body, /<main class="main">/, "main region");
  assert.match(body, /<div class="content">/, "content region");
  assert.match(body, /<footer class="composer">[\s\S]*?<div id="dropzone"[\s\S]*?<\/footer>/, "dropzone is inside the composer footer");
  assert.doesNotMatch(body, /class="page-header"/, "page header removed");
  assert.doesNotMatch(body, /class="crumbs"/, "breadcrumbs removed");
  // Sidebar navigation.
  for (const label of ["หน้าแรก", "งานของฉัน", "ส่งออก"]) {
    assert.match(body, new RegExp(`<button[^>]*class="nav-item[^"]*"[^>]*>[\\s\\S]*?${label}`), `nav item ${label}`);
  }
  assert.doesNotMatch(body, /ย่อเมนู/, "no desktop collapse button");
  assert.match(body, /<h3 class="side-title">ผลลัพธ์<\/h3>/, "ผลลัพธ์ sidebar section");
  assert.match(body, /<a[^>]*id="admin-link"[^>]*href="\/admin"[^>]*hidden/, "admin link hidden by default");
  assert.match(body, /<button[^>]*data-theme-toggle[^>]*aria-label="เปลี่ยนเป็นโหมดมืด"/, "theme toggle in the sidebar");
  assert.match(body, /<aside[^>]*id="sidebar"/, "sidebar has an id for the drawer");
  // Drawer, theme tokens and the result hook.
  assert.match(html, /\.logo-bg \{ fill: var\(--primary\)/, "logo background uses --primary");
  assert.match(html, /body\.result-open/, "result-open rules exist");
  assert.match(html, /function setResultOpen\(open\)/, "setResultOpen defined");
  assert.match(html, /@media \(max-width: 768px\)/, "drawer breakpoint is 768px");
});

test("index.html: drawer moves focus in after a layout flush, and jobs nav shows the card only with items", () => {
  // No DOM harness in this repo: these pin the source-level fix (no seam for real focus).
  const html = read("index.html");
  const setDrawer = html.slice(html.indexOf("function setDrawer(open)"), html.indexOf("function closeDrawer()"));
  assert.match(setDrawer, /sidebar\.offsetWidth/, "forces a style flush before focusing");
  assert.doesNotMatch(setDrawer, /requestAnimationFrame/, "no rAF retry: visibility is switched in CSS");
  const css = html.slice(html.indexOf("@media (max-width: 768px)"), html.indexOf("@media (min-width: 1200px)"));
  assert.match(css, /\.sidebar \{[^}]*transition: transform 0\.2s ease, visibility 0s linear 0\.2s/, "closed: visibility hides after the slide");
  assert.match(css, /\.sidebar\.drawer-open \{[^}]*visibility: visible[^}]*transition: transform 0\.2s ease, visibility 0s/, "open: visibility switches on at once");
  assert.match(css, /\.sidebar, \.sidebar\.drawer-open \{ transition: none; \}/, "reduced motion disables the transition, open state too");
  const jobs = html.slice(html.indexOf('navJobs.addEventListener("click"'), html.indexOf('navExport.addEventListener("click"'));
  assert.match(jobs, /jobsOpen = true/, "jobs nav marks the jobs view open");
  assert.match(jobs, /syncJobsCard\(\)/, "jobs nav syncs the card and empty message");
  assert.match(jobs, /scrollIntoView/, "scrolls to the top of the content");
  const sync = html.slice(html.indexOf("function syncJobsCard()"), html.indexOf("function renderJobs(jobs)"));
  assert.match(sync, /jobsEmpty\.hidden = !empty/, "empty message shown only while the list is empty");
  assert.match(sync, /jobsCard\.hidden = empty && !jobsOpen/, "card stays visible in the jobs view even when empty");
  const renderBody = html.slice(html.indexOf("function renderJobs(jobs)"), html.indexOf("function notifyCompletions"));
  assert.match(renderBody, /syncJobsCard\(\)/, "poller re-renders through syncJobsCard");
  assert.doesNotMatch(renderBody, /jobsCard\.hidden = jobRows\.size === 0/, "poller no longer hides the card on its own");
  assert.match(html, /<p id="jobs-empty" class="muted jobs-empty" hidden>ยังไม่มีงาน<\/p>/, "empty message markup inside the jobs card");
});

test("index.html: home and new job bring the hero back after clearing a result", () => {
  const html = read("index.html");
  const home = block(html, null, 'navHome.addEventListener("click"', 'navJobs.addEventListener("click"');
  assert.match(home, /clearResults\(\);/, "home clears the result");
  assert.match(home, /if \(!busy\) emptyState\.hidden = false;/, "home un-hides the hero when no upload is running");
  const fresh = block(html, null, 'newJobBtn.addEventListener("click"', "// The admin link");
  assert.match(fresh, /clearResults\(\);/, "new job clears the result");
  assert.match(fresh, /if \(!busy\) emptyState\.hidden = false;/, "new job un-hides the hero when no upload is running");
});

test("index.html: Ctrl K only takes over when a result is open", () => {
  const html = read("index.html");
  const handler = block(html, null, 'e.key.toLowerCase() === "k"', "ui.searchInput.select();");
  const guard = handler.indexOf('classList.contains("result-open")');
  const prevent = handler.indexOf("e.preventDefault()");
  assert.ok(guard >= 0, "checks result-open");
  assert.ok(guard < prevent, "returns before preventDefault when no result is open");
  assert.match(handler, /if \(!document\.body\.classList\.contains\("result-open"\)\) return;/, "early return without a result");
});

test("index.html: no empty side-section left in the sidebar", () => {
  const html = read("index.html");
  assert.doesNotMatch(html, /<div class="side-section">\s*<\/div>/, "no empty side-section");
});

// ---- chat-style empty state and bottom upload bar ----

function block(html, tagPattern, from, to) {
  const start = html.indexOf(from);
  assert.ok(start >= 0, `${from} is in the markup`);
  const end = html.indexOf(to, start);
  assert.ok(end > start, `${to} follows ${from}`);
  return html.slice(start, end);
}

function startCard(hero, attr) {
  const m = hero.match(new RegExp(`<button class="start-card"[^>]*${attr}[^>]*>([\\s\\S]*?)<\\/button>`));
  assert.ok(m, `start card with ${attr}`);
  return m[1];
}

test("index.html: empty-state hero with two start cards", () => {
  const html = read("index.html");
  const hero = block(html, null, '<div id="empty-state"', '<div id="status"');
  assert.match(hero, /<img class="hero-sparkle" src="img\/hero\.webp" width="96" height="96" alt=""/, "hero photo");
  assert.ok(fs.existsSync(path.join(SITE, "img", "hero.webp")), "site/img/hero.webp exists");
  assert.match(hero, /<h2 class="empty-heading">มาแล้วจ้าาาา นายจ๋าาาาา<\/h2>/, "hero heading");
  assert.match(hero, /<p class="empty-sub">ภาษาไทยและอังกฤษ · PNG, JPG หรือ PDF<\/p>/, "hero sub line");
  assert.equal((hero.match(/class="start-card"/g) || []).length, 2, "two start cards");
  assert.doesNotMatch(hero, /data-mode=/, "no data-mode card left in the hero");

  const upload = startCard(hero, 'data-action="upload"');
  assert.match(upload, /อัปโหลดไฟล์/, "upload card title");
  assert.match(upload, /ส่งเข้าคิว พร้อมสรุปด้วย AI/, "upload card sub line");
  assert.match(upload, /<svg/, "upload card icon");

  const jobs = startCard(hero, 'data-action="jobs"');
  assert.match(jobs, /งานของฉัน/, "jobs card title");
  assert.match(jobs, /<svg/, "jobs card icon");
});

test("index.html: no mode choice left, one file input", () => {
  const html = read("index.html");
  assert.equal((html.match(/id="mode-select"/g) || []).length, 0, "mode-select is gone");
  assert.equal((html.match(/id="ram-warning"/g) || []).length, 0, "ram-warning is gone");
  assert.equal((html.match(/id="file-input"/g) || []).length, 1, "file-input appears once");
});

test("index.html: model pill is static server text", () => {
  const html = read("index.html");
  assert.match(html, /<span id="model-pill">อ่านผ่าน server<\/span>/, "model pill text");
});

test("index.html: bottom upload bar contract", () => {
  const html = read("index.html");
  const composer = block(html, null, '<footer class="composer">', "</footer>");
  assert.match(composer, /<div id="dropzone"(?![^>]*role=)(?![^>]*tabindex)[^>]*>/, "dropzone is a plain drop target");
  assert.match(composer, /ลากไฟล์มาวางที่นี่ หรือกด Ctrl\+V/, "placeholder copy");
  assert.match(composer, /<label for="file-input"[^>]*>[\s\S]*?เพิ่มไฟล์[\s\S]*?<\/label>/, "เพิ่มไฟล์ label for file-input");
  assert.match(composer, /<button type="button" class="composer-send"[^>]*aria-label="เลือกไฟล์"/, "send-style arrow button");
  assert.doesNotMatch(composer, /<button[^>]*role="button"/, "no nested role=button");
});

test("index.html: start cards and bar wire to the existing handlers", () => {
  const html = read("index.html");
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.match(script, /action === "upload"[\s\S]{0,120}fileInput\.click\(\)/, "upload card opens the picker");
  assert.match(script, /action === "jobs"[\s\S]{0,120}navJobs\.click\(\)/, "jobs card reuses the nav handler");
  assert.match(html, /body:not\(\.result-open\) \.new-job-btn \{ display: none; \}/, "new-job button hidden without a result");
  assert.match(html, /\.composer-bar\.drag \{[^}]*outline: 2px dashed var\(--primary\)/, "drag state uses --primary dashed outline");
});

// ---- fix round 1: pinned composer, mobile wrap, limits, ram warning, busy guard ----

test("index.html: main is a flex column so the composer pins to the bottom", () => {
  const html = read("index.html");
  const main = html.match(/\n {2}\.main \{[^}]*\}/);
  assert.ok(main, ".main rule exists");
  assert.match(main[0], /display: flex/, ".main is a flex container");
  assert.match(main[0], /flex-direction: column/, ".main stacks content above the composer");
  assert.match(main[0], /min-height: 100vh/, ".main fills the viewport");
  assert.match(html, /\.content \{[^}]*flex: 1/, ".content grows so the composer sits at the bottom");
});

test("index.html: composer wraps on mobile, hint takes its own row", () => {
  const html = read("index.html");
  const start = html.indexOf("@media (max-width: 768px)");
  const media = html.slice(start, html.indexOf("@media (prefers-reduced-motion: reduce)", start));
  assert.match(media, /\.composer-bar \{[^}]*flex-wrap: wrap/, "bar wraps");
  assert.match(media, /\.composer-hint \{[^}]*flex: 1 1 100%/, "hint takes a full row");
});

test("index.html: upload limits line under the hero sub", () => {
  const html = read("index.html");
  assert.match(html, /<p class="empty-sub">ภาษาไทยและอังกฤษ · PNG, JPG หรือ PDF<\/p>\s*<p class="empty-sub">ไฟล์ละไม่เกิน 500 MB<\/p>/, "limits line");
});

test("index.html: start cards do nothing while busy", () => {
  const html = read("index.html");
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.match(script, /addEventListener\("click", function \(\) \{\s*if \(busy\) return;\s*if \(card\.dataset\.action === "jobs"\)/, "busy guard first");
});

test("index.html: inline script has no browser OCR worker", () => {
  const html = read("index.html");
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.doesNotMatch(script, /new Worker\(/, "no in-browser worker");
  assert.doesNotMatch(script, /startWorker/, "no startWorker");
});

// ---- model select in the bottom bar (AI summary model) ----

test("index.html: model select sits in the composer, hidden until models load", () => {
  const html = read("index.html");
  assert.equal((html.match(/id="model-select"/g) || []).length, 1, "model-select appears once");
  const composer = block(html, null, '<footer class="composer">', "</footer>");
  assert.match(composer, /<select id="model-select" class="model-select" hidden><\/select>/, "hidden select in the composer");
  assert.match(composer, /<label for="model-select" class="sr-only">โมเดลสรุป AI<\/label>/, "sr-only label");
  const label = composer.indexOf('for="model-select"');
  const hint = composer.indexOf("composer-hint");
  const add = composer.indexOf("เพิ่มไฟล์");
  assert.ok(label > hint && label < add, "label sits after the hint and before เพิ่มไฟล์");
});

test("index.html: inline script loads models and remembers the choice", () => {
  const html = read("index.html");
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.match(script, /api\("GET", "\/api\/models"\)/, "loads the model list");
  assert.match(script, /"ocr-model"/, "remembers the choice under ocr-model");
});

test("index.html: upload body sends the chosen model only when the select shows", () => {
  const html = read("index.html");
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.match(script, /api\("POST", "\/api\/jobs", body\)/, "create call sends the body");
  assert.match(script, /if \(!modelSelect\.hidden\) \{/, "model added only when the select is shown");
});

test("index.html: mobile composer gives the model select a full row share", () => {
  const html = read("index.html");
  const start = html.indexOf("@media (max-width: 768px)");
  const media = html.slice(start, html.indexOf("@media (prefers-reduced-motion: reduce)", start));
  assert.match(media, /\.composer-bar \.model-select \{[^}]*flex: 1 1 0/, "mobile flex rule");
  assert.match(media, /\.composer-bar \.model-select \{[^}]*min-height: 44px/, "mobile touch height");
});

test("index.html: model-select rules use theme tokens only", () => {
  const html = read("index.html");
  const rules = [...html.matchAll(/[^{}]*\.(?:composer-bar )?\.?model-select[^{]*\{[^}]*\}/g)].map((m) => m[0]);
  assert.ok(rules.length >= 2, "model-select rules exist");
  for (const rule of rules) {
    assert.doesNotMatch(rule, /#[0-9a-fA-F]{3,8}\b/, "no hex colour");
    assert.doesNotMatch(rule, /rgba?\(/, "no rgb colour");
  }
});

test("index.html: option values are plain model ids; the key is never sent", () => {
  const html = read("index.html");
  const script = html.slice(html.lastIndexOf("<script>"));
  assert.match(script, /opt\.value = m\.id;/, "option value is the plain model id");
  assert.match(script, /opt\.textContent = m\.name;/, "text is the short name");
  assert.match(script, /opt\.title = m\.id;/, "title is the model id");
  assert.doesNotMatch(script, /m\.key/, "no key field in the option list");
  assert.doesNotMatch(script, /body\.key/, "the upload body never carries a key");
  assert.match(script, /lastIndexOf\("\|"\)/, "a saved key|id value is read after its last |");
});
