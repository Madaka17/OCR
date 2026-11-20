const { test } = require("node:test");
const assert = require("node:assert/strict");
const { loadLibs, loadModels, loadFixture, MODEL_DIR } = require("./helpers.js");

test("loadLibs resolves opencv, onnxruntime and clipper", async () => {
  const { cv, ort, ClipperLib } = await loadLibs();
  assert.ok(cv && typeof cv.Mat === "function", "cv.Mat is available");
  assert.ok(ort && ort.InferenceSession, "ort.InferenceSession is available");
  assert.ok(ClipperLib && ClipperLib.ClipperOffset, "ClipperLib.ClipperOffset is available");
});

test("loadModels returns det/rec sessions that run on zero tensors", async () => {
  const { ort } = await loadLibs();
  const { det, rec, chars, userConfig } = await loadModels(ort);

  const detIn = det.inputNames[0];
  const detOut = await det.run({ [detIn]: new ort.Tensor("float32", new Float32Array(3 * 64 * 64), [1, 3, 64, 64]) });
  const detDims = detOut[det.outputNames[0]].dims;
  assert.deepEqual(detDims, [1, 1, 64, 64]);

  const recIn = rec.inputNames[0];
  const recOut = await rec.run({ [recIn]: new ort.Tensor("float32", new Float32Array(3 * 48 * 64), [1, 3, 48, 64]) });
  const recDims = recOut[rec.outputNames[0]].dims;
  assert.equal(recDims.length, 3);
  assert.equal(recDims[0], 1);
  assert.ok(recDims[1] > 0, "rec emits at least one CTC frame");
  assert.equal(recDims[2], chars.length);

  assert.equal(chars[0], "", "CTC blank is the empty string at index 0");
  assert.equal(chars[chars.length - 1], " ", "space is appended last");
  assert.equal(typeof userConfig, "object");
  assert.equal(userConfig.rec.color, "flatten");
  assert.ok(MODEL_DIR.endsWith("OpenThai2.1-OCR-Text"));
});

test("loadFixture(paragraph) exposes BGR pixels of the declared size", () => {
  const fx = loadFixture("paragraph");
  const [h, w] = fx.expected.shape;
  assert.equal(fx.img.width, w);
  assert.equal(fx.img.height, h);
  assert.ok(fx.img.data instanceof Uint8Array);
  assert.equal(fx.img.data.length, h * w * 3);
});
