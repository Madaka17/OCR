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
  assert.match(html, /<button[^>]*data-theme-toggle[^>]*aria-label="เปลี่ยนเป็นโหมดมืด"[^>]*aria-pressed="false"/, "theme toggle button");
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
  assert.match(jobs, /jobs-list[\s\S]*children\.length/, "jobs card shown only when the list has items");
  assert.match(jobs, /scrollIntoView/, "scrolls to the top of the content");
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

test("index.html: empty-state hero with four start cards", () => {
  const html = read("index.html");
  const hero = block(html, null, '<div id="empty-state"', '<div id="status"');
  assert.match(hero, /<svg class="hero-sparkle" width="40" height="40"/, "40px sparkle svg");
  assert.match(hero, /<h2 class="empty-heading">อ่านข้อความจากรูปหรือ PDF<\/h2>/, "hero heading");
  assert.match(hero, /<p class="empty-sub">ภาษาไทยและอังกฤษ · PNG, JPG หรือ PDF<\/p>/, "hero sub line");
  assert.equal((hero.match(/class="start-card"/g) || []).length, 4, "four start cards");

  const auto = startCard(hero, 'data-mode="auto"');
  assert.match(auto, /อ่านอัตโนมัติ/, "auto card title");
  assert.match(auto, /<svg/, "auto card icon");

  const browser = startCard(hero, 'data-mode="browser"');
  assert.match(browser, /อ่านบนเครื่องนี้/, "browser card title");
  assert.match(browser, /ไฟล์ไม่ออกนอกเครื่อง/, "browser card sub line");
  assert.match(browser, /<svg/, "browser card icon");

  const server = startCard(hero, 'data-mode="server"');
  assert.match(server, /ส่งเข้าคิว server/, "server card title");
  assert.match(server, /พร้อมสรุปด้วย AI/, "server card sub line");
  assert.match(server, /<svg/, "server card icon");

  const jobs = startCard(hero, 'data-action="jobs"');
  assert.match(jobs, /งานของฉัน/, "jobs card title");
  assert.match(jobs, /<svg/, "jobs card icon");
});

test("index.html: #mode-select exists once, lives in the composer, and keeps its options", () => {
  const html = read("index.html");
  assert.equal((html.match(/id="mode-select"/g) || []).length, 1, "mode-select appears once");
  assert.equal((html.match(/id="file-input"/g) || []).length, 1, "file-input appears once");
  const composer = block(html, null, '<footer class="composer">', "</footer>");
  assert.match(composer, /<select id="mode-select"/, "mode-select is in the composer");
  assert.match(composer, /<label for="mode-select"[^>]*class="sr-only"[^>]*>โหมดการอ่าน<\/label>/, "visually hidden label");
  for (const value of ["auto", "browser", "server"]) {
    assert.match(composer, new RegExp(`<option value="${value}">`), `option ${value}`);
  }
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
  assert.match(script, /modeSelect\.value = mode;[\s\S]{0,200}modeSelect\.dispatchEvent\(new Event\("change", \{ bubbles: true \}\)\);[\s\S]{0,80}fileInput\.click\(\);/, "mode card sets, dispatches change, opens picker");
  assert.match(script, /action === "jobs"[\s\S]{0,120}navJobs\.click\(\)/, "jobs card reuses the nav handler");
  assert.match(html, /body:not\(\.result-open\) \.new-job-btn \{ display: none; \}/, "new-job button hidden without a result");
  assert.match(html, /\.composer-bar\.drag \{[^}]*outline: 2px dashed var\(--primary\)/, "drag state uses --primary dashed outline");
});
