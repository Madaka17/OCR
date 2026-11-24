// Copies the browser runtime and models into site/ for the static Space.
// Run with `npm run build`. Sources come from node_modules and OpenThai2.1-OCR-Text/.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SITE = join(ROOT, "site");
const NM = join(ROOT, "node_modules");
const MODELS = join(ROOT, "OpenThai2.1-OCR-Text");

const VENDOR = [
  [join(NM, "@techstark", "opencv-js", "dist", "opencv.js"), "opencv.js"],
  [join(NM, "onnxruntime-web", "dist", "ort.wasm.min.js"), "ort.wasm.min.js"],
  // ort.wasm.min.js dynamic-imports this .mjs, which fetches the .wasm beside it.
  [join(NM, "onnxruntime-web", "dist", "ort-wasm-simd-threaded.mjs"), "ort-wasm-simd-threaded.mjs"],
  [join(NM, "onnxruntime-web", "dist", "ort-wasm-simd-threaded.wasm"), "ort-wasm-simd-threaded.wasm"],
  [join(NM, "clipper-lib", "clipper.js"), "clipper.js"],
  [join(NM, "pdfjs-dist", "build", "pdf.min.mjs"), "pdf.min.mjs"],
  [join(NM, "pdfjs-dist", "build", "pdf.worker.min.mjs"), "pdf.worker.min.mjs"],
];

// NOTICE is the model's Apache-2.0 attribution (PaddlePaddle); it ships with the weights.
const MODEL_FILES = ["det.onnx", "rec.onnx", "rec_dict.txt", "config.json", "NOTICE"];

function copyAll(dstDir, pairs) {
  mkdirSync(dstDir, { recursive: true });
  for (const [src, name] of pairs) {
    copyFileSync(src, join(dstDir, name));
    console.log(`copied ${name}`);
  }
}

copyAll(join(SITE, "vendor"), VENDOR);
copyAll(join(SITE, "models"), MODEL_FILES.map((f) => [join(MODELS, f), f]));
