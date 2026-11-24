"use strict";
// Port of the pure box-geometry and reading-order helpers in
// OpenThai2.0-OCR-Text v2.0.0 runtime/line_engine.py (not yet ported: v2.1 changes; no OpenCV, no ONNX).
var Layout = (function () {
  function getThaiText() {
    if (typeof module !== "undefined" && module.exports) return require("./thai_text.js");
    return self.ThaiText;
  }

  function dist(p, q) {
    return Math.hypot(p[0] - q[0], p[1] - q[1]);
  }

  // np.median: even count is the mean of the two middle values.
  function median(xs) {
    const s = xs.slice().sort((a, b) => a - b);
    const n = s.length;
    if (n === 0) return NaN;
    const mid = n >> 1;
    return n % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  // Typical text-line height: median over line-shaped boxes (>= 2x wider than tall).
  function lineHeight(boxes) {
    if (!boxes.length) return 1.0;
    const hs = boxes.map((b) => Math.max(dist(b[0], b[3]), 1.0));
    const lines = [];
    boxes.forEach((b, i) => {
      if (dist(b[0], b[1]) >= 2 * hs[i]) lines.push(hs[i]);
    });
    return median(lines.length >= 3 ? lines : hs);
  }

  // Median text-line angle in degrees, from boxes at least 3x wider than tall.
  function pageSkew(boxes) {
    const angles = [];
    for (const b of boxes) {
      const dx = b[1][0] - b[0][0];
      const dy = b[1][1] - b[0][1];
      const h = dist(b[0], b[3]);
      if (Math.hypot(dx, dy) >= 3 * Math.max(h, 1.0)) {
        angles.push((Math.atan2(dy, dx) * 180) / Math.PI);
      }
    }
    return angles.length ? median(angles) : 0.0;
  }

  // +1 for a line at least `ratio` times taller than wide, -1 for one that
  // much wider than tall, 0 otherwise.
  function vertical(box, ratio) {
    const w = dist(box[0], box[1]);
    const h = dist(box[0], box[3]);
    if (h >= ratio * Math.max(w, 1.0)) return 1;
    if (w >= ratio * Math.max(h, 1.0)) return -1;
    return 0;
  }

  // Empty bands between intervals, at least minGap wide.
  function gaps(iv, minGap) {
    const sorted = iv.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const out = [];
    let reach = sorted[0][1];
    for (let k = 1; k < sorted.length; k++) {
      const lo = sorted[k][0];
      const hi = sorted[k][1];
      if (lo - reach >= minGap) out.push([reach, lo]);
      reach = Math.max(reach, hi);
    }
    return out;
  }

  // First maximal element by key (Python max() semantics).
  function firstMax(items, key) {
    let best = items[0];
    let bestKey = key(best);
    for (let k = 1; k < items.length; k++) {
      const v = key(items[k]);
      if (v > bestKey) {
        best = items[k];
        bestKey = v;
      }
    }
    return best;
  }

  // The column of `side` that touches the gutter (a side can hold several).
  function nextTo(side, ext, hmed, gutterOnRight) {
    if (!side.length) return side;
    const g = gaps(side.map((i) => [ext[i][0], ext[i][1]]), 0.8 * hmed);
    if (!g.length) return side;
    if (gutterOnRight) {
      const edge = g[g.length - 1][1];
      return side.filter((i) => ext[i][0] >= edge);
    }
    const edge = g[0][0];
    return side.filter((i) => ext[i][1] <= edge);
  }

  function enumber(text) {
    return getThaiText().enumNumber(text);
  }

  function numberedAcross(lcol, rcol, ext, hmed, texts) {
    if (texts === null || texts === undefined) return false;
    let pairs = 0;
    for (const i of lcol) {
      const a = enumber(texts[i]);
      if (a === null) continue;
      const cy = (ext[i][2] + ext[i][3]) / 2;
      const hit = rcol.some(
        (j) => enumber(texts[j]) === a + 1 && Math.abs((ext[j][2] + ext[j][3]) / 2 - cy) <= 0.5 * hmed,
      );
      if (hit) pairs++;
    }
    return pairs >= 0.5 * Math.min(lcol.length, rcol.length);
  }

  function prose(col, ext, hmed, texts) {
    if (col.length < 12) return false;
    const widths = col.map((i) => ext[i][1] - ext[i][0]);
    const span = Math.max(...col.map((i) => ext[i][1])) - Math.min(...col.map((i) => ext[i][0]));
    if (median(widths) < 7 * hmed) return false;
    if (widths.filter((w) => w >= 0.75 * span).length / widths.length < 0.5) return false;
    if (texts !== null && texts !== undefined) {
      const digits = getThaiText().DIGITS;
      let total = 0;
      let dig = 0;
      for (const i of col) {
        for (const c of Array.from(texts[i])) {
          if (/\s/.test(c)) continue;
          total++;
          if (digits.has(c)) dig++;
        }
      }
      if (total && dig > 0.3 * total) return false;
    }
    return true;
  }

  // Recursive XY-cut into reading-order blocks (see line_engine._blocks).
  function blocks(idx, ext, hmed, texts, depth) {
    depth = depth || 0;
    if (idx.length < 6 || depth > 12) return [idx];
    const cuts = [];
    for (const [g0, g1] of gaps(idx.map((i) => [ext[i][0], ext[i][1]]), 0.8 * hmed)) {
      const left = idx.filter((i) => ext[i][1] <= g0);
      const right = idx.filter((i) => ext[i][0] >= g1);
      const lcol = nextTo(left, ext, hmed, true);
      const rcol = nextTo(right, ext, hmed, false);
      if (
        prose(lcol, ext, hmed, texts) &&
        prose(rcol, ext, hmed, texts) &&
        !numberedAcross(lcol, rcol, ext, hmed, texts)
      ) {
        cuts.push([g1 - g0, left, right]);
      }
    }
    if (cuts.length) {
      const best = firstMax(cuts, (c) => c[0]);
      return blocks(best[1], ext, hmed, texts, depth + 1).concat(
        blocks(best[2], ext, hmed, texts, depth + 1),
      );
    }
    const hg = gaps(idx.map((i) => [ext[i][2], ext[i][3]]), 0.8 * hmed);
    if (!hg.length) return [idx];
    const [g0, g1] = firstMax(hg, (g) => g[1] - g[0]);
    const top = idx.filter((i) => ext[i][3] <= g0);
    const bottom = idx.filter((i) => ext[i][2] >= g1);
    return blocks(top, ext, hmed, texts, depth + 1).concat(blocks(bottom, ext, hmed, texts, depth + 1));
  }

  // Float32 rotation by -angle (degrees), as np.float32 in the Python source.
  function readingOrder(boxes, angle, texts) {
    if (!boxes.length) return [];
    const a = (-(angle === null || angle === undefined ? pageSkew(boxes) : angle) * Math.PI) / 180;
    const c = Math.cos(a);
    const s = Math.sin(a);
    const pts = boxes.map((b) => b.map((p) => [p[0] * c - p[1] * s, p[0] * s + p[1] * c]));
    const ext = pts.map((p) => {
      const xs = p.map((q) => q[0]);
      const ys = p.map((q) => q[1]);
      return [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    });
    const hmed = lineHeight(boxes);
    const idx = boxes.map((_, i) => i);
    const blks = blocks(idx, ext, hmed, texts === undefined ? null : texts);
    const cy = ext.map((e) => (e[2] + e[3]) / 2);
    const rx = ext.map((e) => (e[0] + e[1]) / 2);
    const hs = boxes.map((b) => Math.max(dist(b[0], b[3]), 1.0));
    const tol = 0.5 * hmed;
    const rows = [];
    for (const blk of blks) {
      // Rows from full-height lines only; small boxes join the nearest row after.
      let full = blk.filter((i) => hs[i] >= 0.6 * hmed);
      if (!full.length) full = blk.slice();
      const fullSet = new Set(full);
      const small = blk.filter((i) => !fullSet.has(i));
      const brows = [];
      full.slice().sort((x, y) => cy[x] - cy[y]).forEach((i) => {
        const last = brows[brows.length - 1];
        if (last && Math.abs(cy[i] - mean(last.map((j) => cy[j]))) <= tol) last.push(i);
        else brows.push([i]);
      });
      const centre = brows.map((r) => mean(r.map((j) => cy[j])));
      for (const i of small) {
        const d = centre.map((c) => Math.abs(cy[i] - c));
        if (d.length) {
          let k = 0;
          for (let n = 1; n < d.length; n++) if (d[n] < d[k]) k = n;
          if (d[k] <= tol + 0.5 * hs[i]) {
            brows[k].push(i);
            continue;
          }
        }
        brows.push([i]);
        centre.push(cy[i]);
      }
      const order = brows.map((_, r) => r).sort((x, y) => centre[x] - centre[y]);
      for (const r of order) rows.push(brows[r].slice().sort((x, y) => rx[x] - rx[y]));
    }
    return rows;
  }

  function mean(xs) {
    return xs.reduce((s, x) => s + x, 0) / xs.length;
  }

  // Boxes that are pieces of a neighbouring line (see line_engine._fragments).
  // rec: Map<int, [text, conf]>; a missing key counts as "".
  function fragments(boxes, rec) {
    const out = new Set();
    if (boxes.length < 3) return out;
    const hs = boxes.map((b) => dist(b[0], b[3]));
    const hmed = lineHeight(boxes);
    boxes.forEach((b, i) => {
      const entry = rec.get(i);
      const text = entry === undefined ? "" : entry[0];
      if (hs[i] >= 0.35 * hmed || Array.from(text.split(" ").join("")).length > 3) return;
      const x0 = Math.min(...b.map((p) => p[0]));
      const x1 = Math.max(...b.map((p) => p[0]));
      const y0 = Math.min(...b.map((p) => p[1]));
      const y1 = Math.max(...b.map((p) => p[1]));
      for (let j = 0; j < boxes.length; j++) {
        const c = boxes[j];
        if (j === i || hs[j] < 0.6 * hmed) continue;
        const cx0 = Math.min(...c.map((p) => p[0]));
        const cx1 = Math.max(...c.map((p) => p[0]));
        const cy0 = Math.min(...c.map((p) => p[1]));
        const cy1 = Math.max(...c.map((p) => p[1]));
        if (cx0 <= x1 && x0 <= cx1 && Math.min(Math.abs(y0 - cy1), Math.abs(cy0 - y1)) <= 0.5 * hmed) {
          out.add(i);
          break;
        }
      }
    });
    return out;
  }

  // Map [x, y] points through a 3x3 affine matrix (no homogeneous divide).
  function applyMatrix(m, box) {
    return box.map(([x, y]) => [
      m[0][0] * x + m[0][1] * y + m[0][2],
      m[1][0] * x + m[1][1] * y + m[1][2],
    ]);
  }

  function invert3(m) {
    const [[a, b, c], [d, e, f], [g, h, i]] = m;
    const A = e * i - f * h;
    const B = -(d * i - f * g);
    const C = d * h - e * g;
    const det = a * A + b * B + c * C;
    return [
      [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
      [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
      [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
    ];
  }

  // 3x3 map from an image of shape [H, W] to np.rot90(image, k) coordinates.
  function rot90Matrix(k, shape) {
    const H = shape[0];
    const W = shape[1];
    if (k === 1) return [[0, 1, 0], [-1, 0, W - 1], [0, 0, 1]];
    if (k === -1) return [[0, -1, H - 1], [1, 0, 0], [0, 0, 1]];
    return [[-1, 0, W - 1], [0, -1, H - 1], [0, 0, 1]];
  }

  const api = {
    median,
    lineHeight,
    pageSkew,
    vertical,
    readingOrder,
    fragments,
    applyMatrix,
    invert3,
    rot90Matrix,
  };
  return api;
})();

if (typeof module !== "undefined") module.exports = Layout;
else self.Layout = Layout;
