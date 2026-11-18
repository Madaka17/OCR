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

function centre(box) {
  return [(box[0][0] + box[2][0]) / 2, (box[0][1] + box[2][1]) / 2];
}

// Per-fixture detect tolerance in px. Everything is 1.0 except `table`:
// onnxruntime-web 1.31.1 and native onnxruntime 1.32 give prob maps that differ
// by up to 0.0028, which flips 8 pixels across the 0.2 threshold in one box
// (max 1.39 px). Feeding the JS prob map to the Python pipeline reproduces the
// JS box exactly, so the gap is runtime numerics, not the port.
const DETECT_TOL = { table: 2.0 };

for (const name of FIXTURE_NAMES) {
  const tol = DETECT_TOL[name] ?? 1.0;
  test(`${name}: detect matches Python LineOCR.detect within ${tol} px`, async () => {
    const { cv, lineOcr } = await setup();
    const fx = loadFixture(name);
    const src = matFrom(cv, fx);
    try {
      const boxes = await lineOcr.detect(src);
      const py = fx.expected.detect;
      assert.equal(boxes.length, py.length, "box count");
      let maxDiff = 0;
      for (const box of boxes) {
        const c = centre(box);
        let best = null;
        let bestD = Infinity;
        for (const p of py) {
          const pc = centre(p);
          const d = Math.hypot(c[0] - pc[0], c[1] - pc[1]);
          if (d < bestD) {
            bestD = d;
            best = p;
          }
        }
        for (let k = 0; k < 4; k++) {
          for (let a = 0; a < 2; a++) {
            maxDiff = Math.max(maxDiff, Math.abs(box[k][a] - best[k][a]));
          }
        }
      }
      assert.ok(maxDiff <= tol, `max coordinate diff ${maxDiff} px`);
    } finally {
      src.delete();
    }
  });
}

test("paragraph: splitOne counts match Python split_one for every detect box", async () => {
  const { cv, cfg } = await setup();
  const fx = loadFixture("paragraph");
  const src = matFrom(cv, fx);
  try {
    assert.equal(cfg.det.split_gap, 1.2);
    assert.equal(fx.expected.split_counts.length, fx.expected.detect.length);
    fx.expected.detect.forEach((b, i) => {
      const pieces = Engine.splitOne(cv, src, b, cfg.det.split_gap);
      assert.equal(pieces.length, fx.expected.split_counts[i], `box ${i}`);
    });
  } finally {
    src.delete();
  }
});

test("splitOne returns the same array object when the box is unchanged", async () => {
  const { cv } = await setup();
  const fx = loadFixture("paragraph");
  const src = matFrom(cv, fx);
  try {
    const b = fx.expected.detect[0];
    const out = Engine.splitOne(cv, src, b, 0);
    assert.equal(out.length, 1);
    assert.equal(out[0], b);
  } finally {
    src.delete();
  }
});

test("flatten on a 48x200 crop returns a CV_8UC1 Mat", async () => {
  const { cv } = await setup();
  const fx = loadFixture("paragraph");
  const src = matFrom(cv, fx);
  let roi = null;
  let gray = null;
  let out = null;
  try {
    roi = src.roi(new cv.Rect(0, 0, 200, 48));
    gray = new cv.Mat();
    cv.cvtColor(roi, gray, cv.COLOR_BGR2GRAY);
    out = Engine.flatten(cv, gray);
    assert.equal(out.type(), cv.CV_8UC1);
    assert.equal(out.rows, 48);
    assert.equal(out.cols, 200);
  } finally {
    if (out) out.delete();
    if (gray) gray.delete();
    if (roi) roi.delete();
    src.delete();
  }
});

test("DEFAULT_CONFIG carries det.yml thresholds and mergeConfig merges per section", () => {
  assert.equal(Engine.DEFAULT_CONFIG.det.thresh, 0.2);
  assert.equal(Engine.DEFAULT_CONFIG.det.box_thresh, 0.4);
  assert.equal(Engine.DEFAULT_CONFIG.det.unclip, 1.4);
  assert.equal(Engine.DEFAULT_CONFIG.det.split_gap, 1.2);
  assert.equal(Engine.DEFAULT_CONFIG.rec.height, 48);

  const merged = Engine.mergeConfig({ rec: { drop_below: 0.8, color: "flatten" } });
  assert.equal(merged.rec.drop_below, 0.8);
  assert.equal(merged.rec.height, 48, "untouched keys of the section survive");
  assert.equal(merged.det.thresh, 0.2, "untouched sections keep defaults");
  assert.equal(Engine.DEFAULT_CONFIG.rec.drop_below, 0.0, "DEFAULT_CONFIG is not mutated");
});
