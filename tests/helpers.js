"use strict";
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");

const ROOT = path.resolve(__dirname, "..");
const MODEL_DIR = path.resolve(ROOT, "OpenThai2.0-OCR-Text");
const FIXTURE_DIR = path.resolve(__dirname, "fixtures");

// OpenCV.js is either a Promise of the module or the module itself, which
// exposes onRuntimeInitialized until its WASM runtime is ready.
async function initCv(mod) {
  if (mod && typeof mod.then === "function") return await mod;
  if (mod && mod.Mat) return mod;
  await new Promise((resolve) => {
    mod.onRuntimeInitialized = resolve;
  });
  return mod;
}

let libsPromise = null;

async function loadLibs() {
  if (!libsPromise) {
    libsPromise = (async () => {
      const cv = await initCv(require("@techstark/opencv-js"));
      const ort = require("onnxruntime-web");
      const ClipperLib = require("clipper-lib");
      return { cv, ort, ClipperLib };
    })();
  }
  return libsPromise;
}

function readBytes(file) {
  const buf = fs.readFileSync(path.join(MODEL_DIR, file));
  return new Uint8Array(buf.buffer, buf.byteOffset, buf.byteLength);
}

async function loadModels(ort) {
  const det = await ort.InferenceSession.create(readBytes("det.onnx"));
  const rec = await ort.InferenceSession.create(readBytes("rec.onnx"));
  // Python: [c for c in read_text().split("\n") if c], after universal newlines.
  const dictText = fs.readFileSync(path.join(MODEL_DIR, "rec_dict.txt"), "utf8");
  const dict = dictText.replace(/\r\n?/g, "\n").split("\n").filter((c) => c);
  const chars = ["", ...dict, " "];
  const userConfig = JSON.parse(fs.readFileSync(path.join(MODEL_DIR, "config.json"), "utf8"));
  return { det, rec, chars, userConfig };
}

function loadFixture(name) {
  const expected = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, `${name}.json`), "utf8"));
  const gz = fs.readFileSync(path.join(FIXTURE_DIR, `${name}.bgr.gz`));
  const raw = zlib.gunzipSync(gz);
  const [height, width] = expected.shape;
  const data = new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength);
  return { img: { width, height, data }, expected };
}

module.exports = { loadLibs, loadModels, loadFixture, MODEL_DIR, FIXTURE_DIR };
