"use strict";
// Port of the detector, crop and line-splitting half of
// OpenThai2.0-OCR-Text/runtime/line_engine.py: LineOCR.detect, DEFAULTS,
// _mini_box, _box_score, _unclip, crop_line (turn=0), _flatten, split_one.
// Recognition, page reading and read() (orientation, deskew, the line filter).
// Python's word positions, dropped list and timing are not ported.
//
// cv (OpenCV.js), ort (onnxruntime-web) and ClipperLib are always passed in,
// never required here. Every cv.Mat created here is deleted before returning.
var Engine = (function () {
  const f32 = Math.fround;
  const DET_MEAN = [0.485, 0.456, 0.406]; // RGB-ordered, applied to BGR on purpose (Python quirk)
  const DET_STD = [0.229, 0.224, 0.225];

  // Python DEFAULTS, with the det.yml PostProcess values (thresh 0.2,
  // box_thresh 0.4, unclip_ratio 1.4) in place of the DEFAULTS det values.
  const DEFAULT_CONFIG = {
    det: {
      limit: 1280, thresh: 0.2, box_thresh: 0.4, unclip: 1.4, min_size: 3,
      max_candidates: 3000, v_pad: 0.0, split_gap: 1.2,
    },
    rec: {
      height: 48, max_w: 3200, batch: 16, drop_below: 0.0, min_chars: 1,
      junk_below: 0.0, junk_invalid: 1.0, color: "flatten",
    },
    ori: {
      enabled: true, sample: 8, flip_below: 0.8, flip_margin: 0.15, deskew_above: 2.5,
    },
    join: { thai_no_space: false },
    // Python's runtime block (OpenVINO). The browser port runs on onnxruntime-web.
    runtime: { backend: "openvino", precision: "bf16" },
  };

  function deepCopy(o) {
    return JSON.parse(JSON.stringify(o));
  }

  // Python: for each section of the user config, update that section in place.
  function mergeConfig(user) {
    const cfg = deepCopy(DEFAULT_CONFIG);
    const sections = user || {};
    for (const section of Object.keys(sections)) {
      cfg[section] = Object.assign({}, cfg[section] || {}, sections[section]);
    }
    return cfg;
  }

  // Python round(): halves go to the even neighbour.
  function pyRound(x) {
    const f = Math.floor(x);
    const d = x - f;
    if (d > 0.5) return f + 1;
    if (d < 0.5) return f;
    return f % 2 === 0 ? f : f + 1;
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  // np.median: even count is the mean of the two middle values.
  function median(xs) {
    const s = xs.slice().sort((a, b) => a - b);
    const n = s.length;
    const mid = n >> 1;
    return n % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function dist(p, q) {
    return Math.hypot(p[0] - q[0], p[1] - q[1]);
  }

  // 4 points -> top-left, top-right, bottom-right, bottom-left (float32).
  // Array.prototype.sort is stable, matching numpy's insertion sort on 4 items.
  function orderPoints(pts) {
    const byX = pts.slice().sort((a, b) => a[0] - b[0]);
    const left = byX.slice(0, 2).sort((a, b) => a[1] - b[1]);
    const right = byX.slice(2).sort((a, b) => a[1] - b[1]);
    return [left[0], right[0], right[1], left[1]].map((p) => [f32(p[0]), f32(p[1])]);
  }

  // Python _mini_box: (order_points(boxPoints(minAreaRect)), min(rect.size)).
  function _miniBox(cv, contour) {
    const rect = cv.minAreaRect(contour);
    const pts = cv.RotatedRect.points(rect).map((p) => [p.x, p.y]);
    return { box: orderPoints(pts), side: Math.min(rect.size.width, rect.size.height) };
  }

  function floatMat(cv, pts) {
    const flat = new Float32Array(pts.length * 2);
    pts.forEach((p, i) => {
      flat[2 * i] = p[0];
      flat[2 * i + 1] = p[1];
    });
    return cv.matFromArray(pts.length, 1, cv.CV_32FC2, flat);
  }

  // Python _box_score: mean probability inside the box's polygon.
  function _boxScore(cv, prob, probH, probW, box) {
    const xs = box.map((p) => p[0]);
    const ys = box.map((p) => p[1]);
    const x0 = clamp(Math.floor(Math.min(...xs)), 0, probW - 1);
    const x1 = clamp(Math.ceil(Math.max(...xs)), 0, probW - 1);
    const y0 = clamp(Math.floor(Math.min(...ys)), 0, probH - 1);
    const y1 = clamp(Math.ceil(Math.max(...ys)), 0, probH - 1);
    const mw = x1 - x0 + 1;
    const mh = y1 - y0 + 1;
    let roi = null;
    let mask = null;
    let poly = null;
    let polys = null;
    try {
      roi = prob.roi(new cv.Rect(x0, y0, mw, mh));
      mask = cv.Mat.zeros(mh, mw, cv.CV_8UC1);
      // astype(np.int32) truncates toward zero.
      const ip = new Int32Array(8);
      box.forEach((p, i) => {
        ip[2 * i] = Math.trunc(p[0] - x0);
        ip[2 * i + 1] = Math.trunc(p[1] - y0);
      });
      poly = cv.matFromArray(4, 1, cv.CV_32SC2, ip);
      polys = new cv.MatVector();
      polys.push_back(poly);
      cv.fillPoly(mask, polys, new cv.Scalar(1));
      return cv.mean(roi, mask)[0];
    } finally {
      if (polys) polys.delete();
      if (poly) poly.delete();
      if (mask) mask.delete();
      if (roi) roi.delete();
    }
  }

  // Python _unclip: offset the box outward by area * ratio / perimeter.
  // Returns the first Clipper path as float points, or null.
  function _unclip(cv, ClipperLib, box, ratio) {
    let poly = null;
    try {
      poly = floatMat(cv, box);
      const area = cv.contourArea(poly);
      const length = cv.arcLength(poly, true);
      if (length === 0) return null;
      // pyclipper truncates float points to integers.
      const path = box.map((p) => ({ X: Math.trunc(p[0]), Y: Math.trunc(p[1]) }));
      const co = new ClipperLib.ClipperOffset(2, 0.25);
      co.AddPath(path, ClipperLib.JoinType.jtRound, ClipperLib.EndType.etClosedPolygon);
      const out = new ClipperLib.Paths();
      co.Execute(out, (area * ratio) / length);
      if (!out.length) return null;
      return out[0].map((p) => [f32(p.X), f32(p.Y)]);
    } finally {
      if (poly) poly.delete();
    }
  }

  // Python crop_line with turn=0: perspective-rectify one quad to an upright
  // line image. Caller deletes the returned Mat.
  function cropLine(cv, img, box) {
    const w = Math.max(Math.trunc(Math.max(dist(box[0], box[1]), dist(box[2], box[3]))), 1);
    const h = Math.max(Math.trunc(Math.max(dist(box[0], box[3]), dist(box[1], box[2]))), 1);
    let src = null;
    let dst = null;
    let M = null;
    let out = null;
    try {
      src = floatMat(cv, box);
      dst = floatMat(cv, [[0, 0], [w, 0], [w, h], [0, h]]);
      M = cv.getPerspectiveTransform(src, dst);
      out = new cv.Mat();
      cv.warpPerspective(img, out, M, new cv.Size(w, h), cv.INTER_CUBIC,
        cv.BORDER_REPLICATE, new cv.Scalar());
      return out;
    } catch (e) {
      if (out) out.delete();
      throw e;
    } finally {
      if (src) src.delete();
      if (dst) dst.delete();
      if (M) M.delete();
    }
  }

  // Python _flatten: dark text on white from any grey line crop. Each column's
  // background is the median of the top and bottom margin rows, smoothed along
  // the line; text is whatever differs from it. Caller deletes the result.
  function flatten(cv, g) {
    const gc = g.isContinuous() ? g : g.clone();
    try {
      const h = gc.rows;
      const w = gc.cols;
      const gd = gc.data;
      const k = Math.max(2, Math.floor(h / 6));
      const topRows = Math.min(k, h);
      const bottomStart = Math.max(0, h - k);

      const bgRow = new Uint8Array(w);
      for (let col = 0; col < w; col++) {
        const vals = [];
        for (let r = 0; r < topRows; r++) vals.push(gd[r * w + col]);
        for (let r = bottomStart; r < h; r++) vals.push(gd[r * w + col]);
        bgRow[col] = Math.trunc(median(vals));
      }

      // np.repeat(bg, 3 rows) -> medianBlur -> row 1.
      const win = Math.max(3, Math.min(h | 1, 255));
      const rep = new Uint8Array(3 * w);
      for (let r = 0; r < 3; r++) rep.set(bgRow, r * w);
      let repMat = null;
      let blurred = null;
      let bg = null;
      try {
        repMat = cv.matFromArray(3, w, cv.CV_8UC1, rep);
        blurred = new cv.Mat();
        cv.medianBlur(repMat, blurred, win);
        bg = blurred.data.slice(w, 2 * w);
      } finally {
        if (repMat) repMat.delete();
        if (blurred) blurred.delete();
      }

      const d = new Float32Array(h * w);
      const hist = new Array(256).fill(0);
      for (let r = 0; r < h; r++) {
        for (let col = 0; col < w; col++) {
          const i = r * w + col;
          d[i] = Math.abs(gd[i] - bg[col]);
          hist[Math.trunc(d[i])] += 1;
        }
      }
      let total = 0;
      const cum = hist.map((n) => (total += n));
      // np.searchsorted(hist, 0.98 * hist[-1]): left side, first index >= target.
      const target = 0.98 * cum[255];
      let idx = 0;
      while (idx < 256 && cum[idx] < target) idx++;
      const hi = Math.max(idx, 24.0);

      const scale = f32(255.0 / hi);
      const out = new Uint8Array(h * w);
      for (let i = 0; i < h * w; i++) {
        const v = clamp(f32(d[i] * scale), 0, 255);
        out[i] = Math.trunc(f32(255 - v));
      }
      return cv.matFromArray(h, w, cv.CV_8UC1, out);
    } finally {
      if (gc !== g) gc.delete();
    }
  }

  // Python split_one: cut one line box at blank stretches >= ratio x its
  // height. Returns [b] (the same array object) when nothing is cut.
  function splitOne(cv, img, b, ratio) {
    if (!ratio) return [b];
    const c = cropLine(cv, img, b);
    let gray = null;
    let flat = null;
    try {
      const h = c.rows;
      const w = c.cols;
      if (w < 4 * h || h < 4) return [b];
      gray = new cv.Mat();
      cv.cvtColor(c, gray, cv.COLOR_BGR2GRAY);
      flat = flatten(cv, gray);
      const fd = flat.data;

      const ink = new Array(w).fill(false);
      for (let x = 0; x < w; x++) {
        for (let r = 0; r < h; r++) {
          if (fd[r * w + x] < 128) {
            ink[x] = true;
            break;
          }
        }
      }

      const segs = [];
      let start = null;
      let blank = 0;
      for (let x = 0; x < w; x++) {
        if (ink[x]) {
          if (start === null) {
            start = x;
          } else if (blank >= ratio * h) {
            segs.push([start, x - blank]);
            start = x;
          }
          blank = 0;
        } else if (start !== null) {
          blank += 1;
        }
      }
      if (start !== null) segs.push([start, w - blank]);

      if (!segs.length || (segs.length === 1 && segs[0][0] < ratio * h && w - segs[0][1] < ratio * h)) {
        return [b];
      }

      const top = [b[1][0] - b[0][0], b[1][1] - b[0][1]];
      const bot = [b[2][0] - b[3][0], b[2][1] - b[3][1]];
      const pad = 0.15 * h;
      const out = [];
      for (const [x0, x1] of segs) {
        const t0 = Math.max(0, (x0 - pad) / w);
        const t1 = Math.min(1, (x1 + pad) / w);
        const pt = (base, dir, t) => [f32(base[0] + f32(t * dir[0])), f32(base[1] + f32(t * dir[1]))];
        out.push([
          pt(b[0], top, t0),
          pt(b[0], top, t1),
          pt(b[3], bot, t1),
          pt(b[3], bot, t0),
        ]);
      }
      return out;
    } finally {
      if (flat) flat.delete();
      if (gray) gray.delete();
      c.delete();
    }
  }

  // Layout and ThaiText: require() in Node, the self.* globals in the browser.
  function getLayout() {
    if (typeof module !== "undefined" && module.exports) return require("./layout.js");
    return self.Layout;
  }
  function getThaiText() {
    if (typeof module !== "undefined" && module.exports) return require("./thai_text.js");
    return self.ThaiText;
  }

  const IDENT = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

  function matMul3(a, b) {
    const out = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (let i = 0; i < 3; i++) {
      for (let j = 0; j < 3; j++) {
        let s = 0;
        for (let k = 0; k < 3; k++) s += a[i][k] * b[k][j];
        out[i][j] = s;
      }
    }
    return out;
  }

  // Python len() of a str counts code points.
  function cpLen(s) {
    return Array.from(s).length;
  }

  function mean(xs) {
    let s = 0;
    for (const x of xs) s += x;
    return s / xs.length;
  }

  // np.rot90 on an image: k=1 counter-clockwise, k=-1 clockwise, k=2 turned 180.
  // Caller deletes the returned Mat.
  function rot90(cv, src, k) {
    const dst = new cv.Mat();
    try {
      const code = k === 1 ? cv.ROTATE_90_COUNTERCLOCKWISE
        : k === -1 ? cv.ROTATE_90_CLOCKWISE : cv.ROTATE_180;
      cv.rotate(src, dst, code);
      return dst;
    } catch (e) {
      dst.delete();
      throw e;
    }
  }

  // Python _flatten for a BGR crop: gray -> flatten -> BGR. Caller deletes the result.
  function flattenBgr(cv, bgr) {
    let gray = null;
    let flat = null;
    try {
      gray = new cv.Mat();
      cv.cvtColor(bgr, gray, cv.COLOR_BGR2GRAY);
      flat = flatten(cv, gray);
      const out = new cv.Mat();
      cv.cvtColor(flat, out, cv.COLOR_GRAY2BGR);
      return out;
    } finally {
      if (flat) flat.delete();
      if (gray) gray.delete();
    }
  }

  // Python _deskew: rotate by `angle` degrees, canvas grown to fit, the new area
  // filled with the per-channel median of the page's four edges. Returns the
  // rotated Mat (caller deletes) and the 3x3 map of the transform.
  function deskew(cv, img, angle) {
    const H = img.rows;
    const W = img.cols;
    let rm = null;
    let M = null;
    let out = null;
    try {
      rm = cv.getRotationMatrix2D(new cv.Point(W / 2, H / 2), angle, 1.0);
      const r = Array.from(rm.data64F);
      const m = [[r[0], r[1], r[2]], [r[3], r[4], r[5]]];
      const c = Math.abs(m[0][0]);
      const s = Math.abs(m[0][1]);
      const nW = pyRound(H * s + W * c);
      const nH = pyRound(H * c + W * s);
      m[0][2] += nW / 2 - W / 2;
      m[1][2] += nH / 2 - H / 2;
      const d = img.data;
      const fill = [0, 1, 2].map((ch) => {
        const vals = [];
        for (let x = 0; x < W; x++) {
          vals.push(d[x * 3 + ch], d[((H - 1) * W + x) * 3 + ch]);
        }
        for (let y = 0; y < H; y++) {
          vals.push(d[(y * W) * 3 + ch], d[(y * W + W - 1) * 3 + ch]);
        }
        return Math.trunc(median(vals));
      });
      M = cv.matFromArray(2, 3, cv.CV_64FC1,
        Float64Array.from([m[0][0], m[0][1], m[0][2], m[1][0], m[1][1], m[1][2]]));
      out = new cv.Mat();
      cv.warpAffine(img, out, M, new cv.Size(nW, nH), cv.INTER_CUBIC, cv.BORDER_CONSTANT,
        new cv.Scalar(fill[0], fill[1], fill[2], 0));
      return {
        img: out,
        m: [[m[0][0], m[0][1], m[0][2]], [m[1][0], m[1][1], m[1][2]], [0, 0, 1]],
      };
    } catch (e) {
      if (out) out.delete();
      throw e;
    } finally {
      if (M) M.delete();
      if (rm) rm.delete();
    }
  }

  class LineOCR {
    constructor({ cv, ort, ClipperLib, det, rec, chars, cfg }) {
      this.cv = cv;
      this.ort = ort;
      this.ClipperLib = ClipperLib;
      this.det = det;
      this.rec = rec;
      this.chars = chars;
      this.cfg = cfg || mergeConfig({});
    }

    // Python LineOCR.detect: image (CV_8UC3 BGR Mat) -> list of 4-point boxes.
    async detect(img) {
      const cv = this.cv;
      const c = this.cfg.det;
      const owned = [];
      const own = (m) => {
        owned.push(m);
        return m;
      };
      try {
        const H0 = img.rows;
        const W0 = img.cols;
        // White border so edge text still closes into a region, then the long
        // side scaled to the detector's working size in both directions.
        const pad = Math.max(8, Math.trunc(0.05 * Math.min(H0, W0)));
        const padded = own(new cv.Mat());
        cv.copyMakeBorder(img, padded, pad, pad, pad, pad, cv.BORDER_CONSTANT,
          new cv.Scalar(255, 255, 255, 255));
        const h = padded.rows;
        const w = padded.cols;
        const s = c.limit ? Math.min(c.limit / Math.max(h, w), 4.0) : 1.0;
        const rh = Math.max(32, pyRound((h * s) / 32) * 32);
        const rw = Math.max(32, pyRound((w * s) / 32) * 32);

        const resized = own(new cv.Mat());
        cv.resize(padded, resized, new cv.Size(rw, rh), 0, 0, cv.INTER_LINEAR);
        const px = resized.data;
        const plane = rh * rw;
        const input = new Float32Array(3 * plane);
        for (let i = 0; i < plane; i++) {
          for (let ch = 0; ch < 3; ch++) {
            let v = f32(px[i * 3 + ch] / 255);
            v = f32(v - f32(DET_MEAN[ch]));
            v = f32(v / f32(DET_STD[ch]));
            input[ch * plane + i] = v;
          }
        }
        const tensor = new this.ort.Tensor("float32", input, [1, 3, rh, rw]);
        const result = await this.det.run({ [this.det.inputNames[0]]: tensor });
        const out = result[this.det.outputNames[0]];
        const dims = out.dims;
        const ph = dims[dims.length - 2];
        const pw = dims[dims.length - 1];
        const pd = out.data;

        const prob = own(cv.matFromArray(ph, pw, cv.CV_32FC1, Float32Array.from(pd)));
        const bits = new Uint8Array(ph * pw);
        for (let i = 0; i < bits.length; i++) bits[i] = pd[i] > c.thresh ? 1 : 0;
        const bitmap = own(cv.matFromArray(ph, pw, cv.CV_8UC1, bits));
        const contours = own(new cv.MatVector());
        const hierarchy = own(new cv.Mat());
        cv.findContours(bitmap, contours, hierarchy, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE);

        const sx = w / pw;
        const sy = h / ph;
        const n = Math.min(contours.size(), Math.trunc(c.max_candidates));
        const boxes = [];
        for (let i = 0; i < n; i++) {
          const cnt = contours.get(i);
          try {
            let { box, side } = _miniBox(cv, cnt);
            if (side < c.min_size || _boxScore(cv, prob, ph, pw, box) < c.box_thresh) continue;
            const big = _unclip(cv, this.ClipperLib, box, c.unclip);
            if (big === null) continue;
            const bigMat = own(floatMat(cv, big));
            ({ box, side } = _miniBox(cv, bigMat));
            if (side < c.min_size + 2) continue;
            for (const p of box) {
              p[0] = clamp(f32(f32(p[0] * sx) - pad), 0, W0 - 1);
              p[1] = clamp(f32(f32(p[1] * sy) - pad), 0, H0 - 1);
            }
            if (c.v_pad) {
              // Thai marks above/below the core band: grow the height only.
              const hh = (Math.max(dist(box[0], box[3]), 1.0) * c.v_pad) / 2;
              for (const k of [0, 1]) box[k][1] = clamp(box[k][1] - hh, 0, H0 - 1);
              for (const k of [2, 3]) box[k][1] = clamp(box[k][1] + hh, 0, H0 - 1);
            }
            const xs = box.map((p) => p[0]);
            const ys = box.map((p) => p[1]);
            if (Math.max(...xs) - Math.min(...xs) >= 2 && Math.max(...ys) - Math.min(...ys) >= 2) {
              boxes.push(box);
            }
          } finally {
            cnt.delete();
          }
        }
        return boxes;
      } finally {
        for (const m of owned) m.delete();
      }
    }

    // Python recognize_detail (flatten mode, no char positions). The caller keeps
    // ownership of `crops`. Returns [text, conf] per crop, in input order.
    async recognizeDetail(crops) {
      const cv = this.cv;
      const c = this.cfg.rec;
      const H = c.height;
      const maxW = c.max_w;
      const bs = c.batch;
      const prepped = [];
      const ratios = [];
      try {
        for (const cr of crops) {
          const rw = Math.max(1, Math.ceil((H * cr.cols) / Math.max(cr.rows, 1)));
          let resized = null;
          try {
            resized = new cv.Mat();
            cv.resize(cr, resized, new cv.Size(rw, H), 0, 0, cv.INTER_LINEAR);
            prepped.push(flattenBgr(cv, resized));
          } finally {
            if (resized) resized.delete();
          }
          const p = prepped[prepped.length - 1];
          ratios.push(p.cols / Math.max(p.rows, 1));
        }

        const out = new Array(crops.length);
        const order = prepped.map((_, i) => i).sort((a, b) => ratios[a] - ratios[b]);
        for (let b = 0; b < order.length; b += bs) {
          const idx = order.slice(b, b + bs);
          let maxRatio = -Infinity;
          for (const i of idx) maxRatio = Math.max(maxRatio, ratios[i]);
          const W = Math.min(maxW, Math.max(16, Math.ceil(H * maxRatio)));
          // 0 after normalising is mid-grey: the zero padding to the right of each line.
          const batch = new Float32Array(idx.length * 3 * H * W);
          idx.forEach((i, n) => {
            const rw = Math.min(W, Math.max(1, Math.ceil(H * ratios[i])));
            let im = null;
            try {
              im = new cv.Mat();
              cv.resize(prepped[i], im, new cv.Size(rw, H), 0, 0, cv.INTER_LINEAR);
              const px = im.data;
              for (let r = 0; r < H; r++) {
                for (let col = 0; col < rw; col++) {
                  for (let ch = 0; ch < 3; ch++) {
                    const v = f32(f32(f32(px[(r * rw + col) * 3 + ch] / 255) - 0.5) / 0.5);
                    batch[((n * 3 + ch) * H + r) * W + col] = v;
                  }
                }
              }
            } finally {
              if (im) im.delete();
            }
          });
          const tensor = new this.ort.Tensor("float32", batch, [idx.length, 3, H, W]);
          const result = await this.rec.run({ [this.rec.inputNames[0]]: tensor });
          const res = result[this.rec.outputNames[0]];
          const T = res.dims[1];
          const C = res.dims[2];
          idx.forEach((i, n) => {
            out[i] = this._ctcDecode(res.data, n * T * C, T, C);
          });
        }
        return out;
      } finally {
        for (const p of prepped) p.delete();
      }
    }

    // Python _ctc_detail without positions: greedy CTC, collapse repeats, drop
    // blanks (id 0). Confidence is the mean of each kept run's first-frame prob.
    _ctcDecode(data, base, T, C) {
      const ids = new Array(T);
      const confs = new Array(T);
      for (let t = 0; t < T; t++) {
        const off = base + t * C;
        let bi = 0;
        let bv = data[off];
        for (let k = 1; k < C; k++) {
          if (data[off + k] > bv) {
            bv = data[off + k];
            bi = k;
          }
        }
        ids[t] = bi;
        confs[t] = bv;
      }
      let text = "";
      let sum = 0;
      let n = 0;
      let t = 0;
      while (t < T) {
        let end = t + 1;
        while (end < T && ids[end] === ids[t]) end++;
        if (ids[t] !== 0) {
          text += ids[t] < this.chars.length ? this.chars[ids[t]] : "";
          sum += confs[t];
          n++;
        }
        t = end;
      }
      return [text, n ? sum / n : 0];
    }

    // Python _read_boxes: read every box as cut. A box at least 1.5x taller than
    // wide is also read turned both ways, and the most confident reading kept.
    async _readBoxes(img, boxes) {
      const cv = this.cv;
      const raw = [];
      const turned = [];
      try {
        for (const b of boxes) raw.push(cropLine(cv, img, b));
        const tall = [];
        raw.forEach((cr, n) => {
          if (cr.rows >= 1.5 * cr.cols) tall.push(n);
        });
        for (const k of [1, -1]) {
          for (const n of tall) turned.push(rot90(cv, raw[n], k));
        }
        const res = await this.recognizeDetail(raw.concat(turned));
        const out = res.slice(0, raw.length).map(([text, conf]) => ({ text, conf }));
        tall.forEach((n, j) => {
          for (const alt of [res[raw.length + j], res[raw.length + tall.length + j]]) {
            if (alt[1] > out[n].conf) out[n] = { text: alt[0], conf: alt[1] };
          }
        });
        return out;
      } finally {
        for (const m of raw) m.delete();
        for (const m of turned) m.delete();
      }
    }

    // Read arbitrary quad regions of `img` (not deleted here) with the same
    // cleanup as read(). Returns [{text, conf}] in the order of `boxes`.
    async readRegions(img, boxes) {
      const { fixScripts, canonical } = getThaiText();
      const out = await this._readBoxes(img, boxes);
      return out.map((r) => ({ text: fixScripts(canonical(r.text)), conf: r.conf }));
    }

    // Python _page: split each line at blank stretches, keep the pieces only when
    // they read well, then reading order. Returns rows, page angle, rec (one
    // {text, conf} per final box) and the final boxes.
    async _page(img, boxes) {
      const L = getLayout();
      const cfg = this.cfg;
      const groups = boxes.map((b) => splitOne(this.cv, img, b, cfg.det.split_gap));
      const changed = boxes.map((b, i) => groups[i].length > 1 || groups[i][0] !== b);
      const pieces = groups.flat();
      const whole = boxes.filter((_, i) => changed[i]);
      const res = await this._readBoxes(img, pieces.concat(whole));
      const final = [];
      const out = [];
      let k = 0;
      let e = pieces.length;
      for (let i = 0; i < boxes.length; i++) {
        const b = boxes[i];
        const g = groups[i];
        const r = res.slice(k, k + g.length);
        k += g.length;
        if (changed[i]) {
          const alt = res[e];
          e += 1;
          const nPieces = r.reduce((s, x) => s + cpLen(x.text.replace(/ /g, "")), 0);
          const minConf = Math.min(...r.map((x) => x.conf));
          if (minConf < cfg.rec.drop_below || nPieces < 0.9 * cpLen(alt.text.replace(/ /g, ""))) {
            final.push(b);
            out.push(alt);
            continue;
          }
        }
        final.push(...g);
        out.push(...r);
      }
      const angle = L.pageSkew(final);
      const rows = L.readingOrder(final, angle, out.map((x) => x.text));
      return { rows, angle, rec: out, boxes: final };
    }

    // Python _sideways: np.rot90 k that turns a sideways page upright, 0 if it is
    // not sideways. The direction is whichever way the longest vertical lines
    // read with more confidence.
    async _sideways(img, boxes) {
      const L = getLayout();
      const votes = boxes.map((b) => L.vertical(b, 2.0));
      const vert = boxes.filter((_, i) => votes[i] === 1);
      if (vert.length < 3 || vert.length <= votes.filter((v) => v === -1).length) return 0;
      vert.sort((a, b) => dist(b[0], b[3]) - dist(a[0], a[3]));
      const raw = [];
      const turned = [];
      try {
        for (const b of vert.slice(0, this.cfg.ori.sample)) raw.push(cropLine(this.cv, img, b));
        for (const k of [1, -1]) {
          for (const cr of raw) turned.push(rot90(this.cv, cr, k));
        }
        const res = await this.recognizeDetail(turned);
        let ccw = 0;
        let cw = 0;
        res.forEach(([, conf], i) => {
          if (i < raw.length) ccw += conf;
          else cw += conf;
        });
        return ccw > cw ? 1 : -1;
      } finally {
        for (const m of raw) m.delete();
        for (const m of turned) m.delete();
      }
    }

    // Python _upside_down: only for a page that reads badly, do its longest
    // lines read better turned 180 degrees?
    async _upsideDown(img, boxes, page) {
      const L = getLayout();
      const oc = this.cfg.ori;
      const confs = page.rec.filter((r) => cpLen(r.text) >= 2).map((r) => r.conf);
      if (confs.length < 3 || median(confs) >= oc.flip_below) return false;
      const idx = [];
      for (let i = 0; i < page.rec.length; i++) {
        if (L.vertical(boxes[i], 2.0) === -1) idx.push(i);
      }
      idx.sort((a, b) => dist(boxes[b][0], boxes[b][1]) - dist(boxes[a][0], boxes[a][1]));
      const sample = idx.slice(0, oc.sample);
      if (sample.length < 3) return false;
      const up = mean(sample.map((i) => page.rec[i].conf));
      const raw = [];
      const flipped = [];
      try {
        for (const i of sample) raw.push(cropLine(this.cv, img, boxes[i]));
        for (const cr of raw) flipped.push(rot90(this.cv, cr, 2));
        const res = await this.recognizeDetail(flipped);
        return mean(res.map((r) => r[1])) > up + oc.flip_margin;
      } finally {
        for (const m of raw) m.delete();
        for (const m of flipped) m.delete();
      }
    }

    // Python LineOCR.read. `img` is a CV_8UC3 BGR Mat, not deleted here.
    async read(img) {
      const cv = this.cv;
      const L = getLayout();
      const { fixScripts, canonical, invalidShare } = getThaiText();
      const cfg = this.cfg;
      const owned = [];
      const own = (m) => {
        owned.push(m);
        return m;
      };
      try {
        const H0 = img.rows;
        const W0 = img.cols;
        let work = img;
        let boxes = await this.detect(work);
        if (!boxes.length && H0 <= 200 && W0 >= 1.5 * H0) {
          // A small, line-shaped image with no detections: read it as one line.
          boxes = [[[0, 0], [W0 - 1, 0], [W0 - 1, H0 - 1], [0, H0 - 1]]];
        }
        const oc = cfg.ori;
        let rotation = 0;
        let skew = 0;
        let tf = IDENT;
        if (oc.enabled) {
          const k = await this._sideways(work, boxes);
          if (k) {
            tf = matMul3(L.rot90Matrix(k, [work.rows, work.cols]), tf);
            work = own(rot90(cv, work, k));
            boxes = await this.detect(work);
            rotation = k === -1 ? 90 : 270;
          }
          skew = L.pageSkew(boxes);
          if (Math.abs(skew) >= oc.deskew_above) {
            const d = deskew(cv, work, skew);
            work = own(d.img);
            tf = matMul3(d.m, tf);
            boxes = await this.detect(work);
          }
        }
        let page = await this._page(work, boxes);
        if (oc.enabled && !rotation && (await this._upsideDown(work, page.boxes, page))) {
          tf = matMul3(L.rot90Matrix(2, [work.rows, work.cols]), tf);
          work = own(rot90(cv, work, 2));
          boxes = await this.detect(work);
          rotation = 180;
          page = await this._page(work, boxes);
        }

        const back = L.invert3(tf);
        const rc = cfg.rec;
        const recMap = new Map(page.rec.map((r, i) => [i, [r.text, r.conf]]));
        const frag = L.fragments(page.boxes, recMap);
        const lines = [];
        const rowTexts = [];
        for (const row of page.rows) {
          const parts = [];
          for (const i of row) {
            if (frag.has(i)) continue;
            const raw = page.rec[i];
            const text = fixScripts(canonical(raw.text));
            const conf = raw.conf;
            let dropped = false;
            if (!text) dropped = true;
            else if (conf < rc.drop_below) dropped = true;
            else if (cpLen(text.replace(/ /g, "")) < rc.min_chars) dropped = true;
            else if (conf < rc.junk_below && invalidShare(text) > rc.junk_invalid) dropped = true;
            if (dropped) continue;
            const box = L.applyMatrix(back, page.boxes[i]);
            lines.push({
              text,
              confidence: pyRound(conf * 1e4) / 1e4,
              box: box.map(([x, y]) => [pyRound(x * 10) / 10, pyRound(y * 10) / 10]),
            });
            parts.push(text);
          }
          if (parts.length) rowTexts.push(parts.join(" "));
        }
        const straightened = Math.abs(skew) >= oc.deskew_above ? skew : 0;
        return {
          text: rowTexts.join("\n"),
          lines,
          rotation,
          text_angle: pyRound((page.angle + straightened) * 100) / 100,
        };
      } finally {
        for (const m of owned) m.delete();
      }
    }
  }

  const api = {
    DEFAULT_CONFIG,
    mergeConfig,
    orderPoints,
    _miniBox,
    _boxScore,
    _unclip,
    cropLine,
    flatten,
    splitOne,
    LineOCR,
  };
  return api;
})();

if (typeof module !== "undefined" && module.exports) module.exports = Engine;
else self.Engine = Engine;
