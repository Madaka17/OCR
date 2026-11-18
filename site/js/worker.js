"use strict";
// Classic worker. Loads OpenCV.js, onnxruntime-web and Clipper, then the engine
// (thai_text.js, layout.js, engine.js, fields.js in that order), creates both
// sessions and reads one RGBA page per "ocr" message.
//
// In:  {type: "ocr", id, width, height, rgba: ArrayBuffer /* transferred */}
// Out: {type: "ready"}                              once the models are loaded
//      {type: "error", id: null | id, message}      load failure, or one page failed
//      {type: "result", id, seconds, text, lines, fields}
//                                                   lines[].box as integer [[x, y] x4];
//                                                   fields is Field[] (always an array,
//                                                   [] for rotated/skewed pages or if
//                                                   field detection fails)

let cv = null;
let ocr = null;
let loadError = null;

async function initCv(mod) {
  if (mod && typeof mod.then === "function") return await mod;
  if (mod && mod.Mat) return mod;
  await new Promise((resolve) => {
    mod.onRuntimeInitialized = resolve;
  });
  return mod;
}

function errorMessage(err) {
  return err && err.message ? String(err.message) : String(err);
}

async function fetchBytes(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.arrayBuffer();
}

async function fetchText(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.text();
}

async function load() {
  importScripts("../vendor/opencv.js", "../vendor/ort.wasm.min.js", "../vendor/clipper.js");
  importScripts("thai_text.js", "layout.js", "engine.js", "fields.js");

  const ort = self.ort;
  ort.env.wasm.wasmPaths = "../vendor/";
  ort.env.wasm.numThreads = self.crossOriginIsolated
    ? Math.min(4, navigator.hardwareConcurrency || 1)
    : 1;

  cv = await initCv(self.cv);

  const [detBytes, recBytes, dictText, cfgText] = await Promise.all([
    fetchBytes("../models/det.onnx"),
    fetchBytes("../models/rec.onnx"),
    fetchText("../models/rec_dict.txt"),
    fetchText("../models/config.json"),
  ]);
  const det = await ort.InferenceSession.create(new Uint8Array(detBytes));
  const rec = await ort.InferenceSession.create(new Uint8Array(recBytes));

  // Python: [c for c in read_text().split("\n") if c], after universal newlines.
  const dict = dictText.replace(/\r\n?/g, "\n").split("\n").filter((c) => c);
  const chars = ["", ...dict, " "];
  const cfg = self.Engine.mergeConfig(JSON.parse(cfgText));

  ocr = new self.Engine.LineOCR({
    cv,
    ort,
    ClipperLib: self.ClipperLib,
    det,
    rec,
    chars,
    cfg,
  });
}

// Blank-field detection on the page as given. Rotated or skewed pages are out of
// scope for phase 1, and a detection failure must never fail the page.
async function detectPageFields(img, out) {
  try {
    if (out.rotation !== 0 || Math.abs(out.text_angle) >= ocr.cfg.ori.deskew_above) return [];
    return await self.Fields.detectFields({ cv, img, lines: out.lines, ocr });
  } catch (err) {
    console.warn("fields:", errorMessage(err));
    return [];
  }
}

async function readPage(msg) {
  const { id, width, height, rgba } = msg;
  let src = null;
  let img = null;
  try {
    if (!(rgba instanceof ArrayBuffer) || rgba.byteLength !== width * height * 4) {
      throw new Error(`page buffer does not match ${width}x${height} RGBA`);
    }
    // Transparent pixels arrive as RGBA; the engine wants BGR and ignores alpha.
    src = cv.matFromArray(height, width, cv.CV_8UC4, new Uint8Array(rgba));
    img = new cv.Mat();
    cv.cvtColor(src, img, cv.COLOR_RGBA2BGR);

    const t0 = performance.now();
    const out = await ocr.read(img);
    const fields = await detectPageFields(img, out);
    const seconds = Math.round((performance.now() - t0)) / 1000;

    self.postMessage({
      type: "result",
      id,
      seconds,
      text: out.text,
      lines: out.lines.map((line) => ({
        text: line.text,
        confidence: line.confidence,
        box: line.box.map(([x, y]) => [Math.trunc(x), Math.trunc(y)]),
      })),
      fields,
    });
  } catch (err) {
    self.postMessage({ type: "error", id, message: errorMessage(err) });
  } finally {
    if (src) src.delete();
    if (img) img.delete();
  }
}

// Pages are read one at a time, in order, even if the page sends several at once.
let chain = load().then(
  () => {
    self.postMessage({ type: "ready" });
  },
  (err) => {
    loadError = errorMessage(err);
    self.postMessage({ type: "error", id: null, message: loadError });
  },
);

self.onmessage = (event) => {
  const msg = event.data;
  if (!msg || msg.type !== "ocr") return;
  chain = chain.then(() => {
    if (loadError !== null) {
      self.postMessage({ type: "error", id: msg.id, message: loadError });
      return;
    }
    return readPage(msg);
  });
};
