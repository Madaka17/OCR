"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { loadLibs, loadModels, loadFixture } = require("./helpers.js");

const Engine = require(path.resolve(__dirname, "..", "site", "js", "engine.js"));
const FIXTURE_NAMES = [
  "paragraph", "two_columns", "table", "numbered_across", "sideways", "skewed",
  "upside_down", "single_line", "color_banner", "blank", "marks", "margin_note",
];

let setupPromise = null;
function setup() {
  if (!setupPromise) {
    setupPromise = (async () => {
      const { cv, ort, ClipperLib } = await loadLibs();
      const { det, rec, chars, userConfig } = await loadModels(ort);
      const cfg = Engine.mergeConfig(userConfig);
      const lineOcr = new Engine.LineOCR({ cv, ort, ClipperLib, det, rec, chars, cfg });
      return { cv, lineOcr, cfg };
    })();
  }
  return setupPromise;
}

function matFrom(cv, fx) {
  return cv.matFromArray(fx.img.height, fx.img.width, cv.CV_8UC3, fx.img.data);
}

function maxBoxDiff(a, b) {
  let m = 0;
  for (let k = 0; k < 4; k++) {
    for (let c = 0; c < 2; c++) m = Math.max(m, Math.abs(a[k][c] - b[k][c]));
  }
  return m;
}

for (const name of FIXTURE_NAMES) {
  test(`${name}: read() matches Python LineOCR.read()`, async () => {
    const { cv, lineOcr } = await setup();
    const fx = loadFixture(name);
    const src = matFrom(cv, fx);
    try {
      const out = await lineOcr.read(src);
      const exp = fx.expected.read;
      assert.equal(out.text, exp.text, "text");
      assert.equal(out.rotation, exp.rotation, "rotation");
      assert.equal(out.lines.length, exp.lines.length, "line count");
      out.lines.forEach((ln, i) => {
        const e = exp.lines[i];
        assert.equal(ln.text, e.text, `line ${i} text`);
        assert.ok(Math.abs(ln.confidence - e.confidence) <= 0.01,
          `line ${i} confidence ${ln.confidence} vs ${e.confidence}`);
        const d = maxBoxDiff(ln.box, e.box);
        assert.ok(d <= 2, `line ${i} box diff ${d} px`);
      });
      assert.ok(Math.abs(out.text_angle - exp.text_angle) <= 0.01,
        `text_angle ${out.text_angle} vs ${exp.text_angle}`);
      assert.equal(src.isDeleted(), false, "read must not delete its input");
    } finally {
      src.delete();
    }
  });
}

test("blank: read() returns empty text and no lines", async () => {
  const { cv, lineOcr } = await setup();
  const fx = loadFixture("blank");
  const src = matFrom(cv, fx);
  try {
    const out = await lineOcr.read(src);
    assert.equal(out.text, "");
    assert.deepEqual(out.lines, []);
  } finally {
    src.delete();
  }
});

test("margin_note: page-level recognition matches Python _page", async () => {
  const { cv, lineOcr } = await setup();
  const fx = loadFixture("margin_note");
  const src = matFrom(cv, fx);
  try {
    const boxes = await lineOcr.detect(src);
    const page = await lineOcr._page(src, boxes);
    const exp = fx.expected.page;
    assert.deepEqual(page.rec.map((r) => r.text), exp.texts, "texts");
    assert.equal(page.rec.length, exp.confs.length, "conf count");
    page.rec.forEach((r, i) => {
      assert.ok(Math.abs(r.conf - exp.confs[i]) <= 0.01, `conf ${i}: ${r.conf} vs ${exp.confs[i]}`);
    });
    assert.deepEqual(page.rows, exp.rows, "rows");
  } finally {
    src.delete();
  }
});

test("recognizeDetail returns [text, conf] per crop and does not delete the crops", async () => {
  const { cv, lineOcr } = await setup();
  const fx = loadFixture("paragraph");
  const src = matFrom(cv, fx);
  let roi = null;
  try {
    roi = src.roi(new cv.Rect(0, 0, 200, 48));
    const out = await lineOcr.recognizeDetail([roi]);
    assert.equal(out.length, 1);
    assert.equal(typeof out[0][0], "string");
    assert.equal(typeof out[0][1], "number");
    assert.equal(roi.isDeleted(), false, "crop must not be deleted");
  } finally {
    if (roi) roi.delete();
    src.delete();
  }
});

// This OpenCV.js build does not expose cv.HEAPU8. Its WASM memory is the buffer
// behind every Mat's data view, and that buffer changes size only when the heap grows.
function wasmHeapBytes(cv) {
  const m = cv.matFromArray(1, 1, cv.CV_8UC1, new Uint8Array(1));
  try {
    return m.data.buffer.byteLength;
  } finally {
    m.delete();
  }
}

test("read(): WASM heap does not grow between run 2 and run 20", async () => {
  const { cv, lineOcr } = await setup();
  const fx = loadFixture("paragraph");
  const src = matFrom(cv, fx);
  try {
    for (let i = 0; i < 2; i++) await lineOcr.read(src);
    const after2 = wasmHeapBytes(cv);
    for (let i = 2; i < 20; i++) await lineOcr.read(src);
    assert.equal(wasmHeapBytes(cv), after2);
  } finally {
    src.delete();
  }
});
