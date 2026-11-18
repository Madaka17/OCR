"use strict";
// Blank-field (dotted leader line) detection. Port of the segment and chain
// logic in .superpowers/sdd/blank-fields-phase1/prototype_fields.py.
// There is no bridging across gaps: runs are never merged.
var Fields = (function () {
  function getLayout() {
    if (typeof module !== "undefined" && module.exports) return require("./layout.js");
    return self.Layout;
  }

  const INK_LIMIT = 229; // ink pixel: grey <= 229, i.e. grey < 230

  // Typical text-line height: from OCR line boxes when there are enough of
  // them, otherwise from the page height.
  function pageLineHeight(lines, H) {
    if (lines.length >= 3) return getLayout().lineHeight(lines.map((l) => l.box));
    return Math.max(10, Math.round(H / 45));
  }

  // Pure: chain thin segments into leader runs.
  // segs: [{x0, x1, cy}] -> Run[] of {y, x0, x1}, sorted by y then x0.
  function chainSegments(segs, lh, W) {
    const sorted = segs
      .slice()
      .sort((a, b) => a.x0 - b.x0 || a.x1 - b.x1 || a.cy - b.cy);
    const used = new Array(sorted.length).fill(false);
    const runs = [];
    for (let i = 0; i < sorted.length; i++) {
      if (used[i]) continue;
      used[i] = true;
      const chain = [sorted[i]];
      for (let j = i + 1; j < sorted.length; j++) {
        if (used[j]) continue;
        const t = sorted[j];
        const last = chain[chain.length - 1];
        if (t.x0 - last.x1 > 0.5 * lh) {
          if (t.x0 - last.x1 > W) break;
          continue;
        }
        if (Math.abs(t.cy - last.cy) <= Math.max(1.5, 0.1 * lh) && t.x0 >= last.x0) {
          chain.push(t);
          used[j] = true;
        }
      }
      const x0 = chain[0].x0;
      const x1 = Math.max(...chain.map((c) => c.x1));
      const ink = chain.reduce((s, c) => s + (c.x1 - c.x0), 0);
      if (x1 - x0 >= 1.5 * lh && ink / (x1 - x0) >= 0.5) {
        const ys = chain.map((c) => c.cy);
        runs.push({ y: Math.round(getLayout().median(ys)), x0, x1 });
      }
    }
    return runs.sort((a, b) => a.y - b.y || a.x0 - b.x0);
  }

  // Pure: reading order. Runs are clustered into rows (a run joins the row of
  // the run before it, after sorting by y, when |dy| <= 3), then each row is
  // ordered by x0. Returns a new array.
  function orderRuns(runs) {
    const byY = runs.slice().sort((a, b) => a.y - b.y || a.x0 - b.x0);
    const rows = [];
    for (const r of byY) {
      const row = rows[rows.length - 1];
      if (row && Math.abs(r.y - row[row.length - 1].y) <= 3) row.push(r);
      else rows.push([r]);
    }
    return rows.flatMap((row) => row.sort((a, b) => a.x0 - b.x0));
  }

  // Pure: right edge of the furthest run that ends at or left of runs[i].x0 on
  // its row (|dy| <= 3), or 0 when there is none.
  function leftBoundOf(runs, i) {
    const r = runs[i];
    let bound = 0;
    runs.forEach((o, j) => {
      if (j !== i && Math.abs(o.y - r.y) <= 3 && o.x1 <= r.x0) bound = Math.max(bound, o.x1);
    });
    return bound;
  }


  // Grey and binary ink mask (ink = grey <= 229). Caller deletes both Mats.
  function inkMask(cv, img) {
    const grey = new cv.Mat();
    const mask = new cv.Mat();
    try {
      cv.cvtColor(img, grey, cv.COLOR_BGR2GRAY);
      cv.threshold(grey, mask, INK_LIMIT, 255, cv.THRESH_BINARY_INV);
    } catch (e) {
      mask.delete();
      throw e;
    } finally {
      grey.delete();
    }
    return mask;
  }

  // Thin 8-connected components as plain {x0, x1, cy} segments.
  function thinSegments(cv, img, lh) {
    const W = img.cols;
    const mask = inkMask(cv, img);
    const labels = new cv.Mat();
    const stats = new cv.Mat();
    const cents = new cv.Mat();
    try {
      const n = cv.connectedComponentsWithStats(mask, labels, stats, cents, 8, cv.CV_32S);
      const st = Array.from(stats.data32S);
      const ce = Array.from(cents.data64F);
      const maxH = Math.max(3, Math.round(0.2 * lh));
      const segs = [];
      for (let i = 1; i < n; i++) {
        const left = st[i * 5 + 0];
        const width = st[i * 5 + 2];
        const height = st[i * 5 + 3];
        if (height <= maxH && width <= 0.8 * W) {
          segs.push({ x0: left, x1: left + width, cy: ce[i * 2 + 1] });
        }
      }
      return segs;
    } finally {
      mask.delete();
      labels.delete();
      stats.delete();
      cents.delete();
    }
  }

  // img: BGR cv.Mat. Returns Run[] sorted by y, then x0.
  function findLeaders(cv, img, lh) {
    const segs = thinSegments(cv, img, lh);
    return orderRuns(chainSegments(segs, lh, img.cols));
  }

  // Share of columns in cols [x0, x1) that hold ink in rows [y - lh, y - 2).
  // Optional `precomputed` is an inkMask(cv, img) Mat that the caller owns;
  // it is used as is and not deleted here.
  function filledShare(cv, img, run, lh, precomputed) {
    const W = img.cols;
    const H = img.rows;
    const mask = precomputed || inkMask(cv, img);
    try {
      const top = Math.max(0, Math.floor(run.y - lh));
      const bottom = Math.min(H, run.y - 2);
      const left = Math.max(0, run.x0);
      const right = Math.min(W, run.x1);
      const cols = right - left;
      if (bottom <= top || cols <= 0) return 0;
      const data = mask.data;
      let inked = 0;
      for (let c = left; c < right; c++) {
        for (let r = top; r < bottom; r++) {
          if (data[r * W + c] !== 0) {
            inked++;
            break;
          }
        }
      }
      return inked / cols;
    } finally {
      if (!precomputed) mask.delete();
    }
  }

  // Tuned crop geometry (Fix round 1 experiment, see task-3-report.md).
  // Label and value crops reach CROP_TOP*lh above the leader row.
  // Label crops start CROP_CAP*lh left of the leader (never left of leftBound).
  const CROP_TOP = 1.3;
  const CROP_CAP = 6;
  const CROP_PAD = 0.3;

  // Drops dotted, ellipsis, underscore and dash leaders (2+ chars), then joins
  // whitespace into single spaces. A single dot is kept.
  const LEADER_RUN = /[.…_-]{2,}/g;
  function cleanText(s) {
    return s.replace(LEADER_RUN, " ").replace(/\s+/g, " ").trim();
  }

  function lineGeom(l) {
    const xs = l.box.map((p) => p[0]);
    const ys = l.box.map((p) => p[1]);
    return {
      left: Math.min(...xs),
      right: Math.max(...xs),
      cy: (Math.min(...ys) + Math.max(...ys)) / 2,
      text: l.text,
    };
  }

  // Quad clockwise from top-left.
  function quad(x0, y0, x1, y1) {
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  }

  // Integer quad clipped to the image, or null when it is empty.
  function cropQuad(x0, y0, x1, y1, W, H) {
    const a = Math.max(0, Math.round(x0));
    const b = Math.min(W, Math.round(x1));
    const c = Math.max(0, Math.round(y0));
    const d = Math.min(H, Math.round(y1));
    return b - a >= 1 && d - c >= 1 ? quad(a, c, b, d) : null;
  }

  // img: BGR cv.Mat (not deleted). lines: read() output lines [{text, box}].
  // ocr: LineOCR with readRegions. Returns Field[] in reading order.
  async function detectFields({ cv, img, lines, ocr }) {
    const lh = pageLineHeight(lines, img.rows);
    const runs = findLeaders(cv, img, lh);
    if (!runs.length) return [];
    const mask = inkMask(cv, img);
    try {
      return await labelAndValue(cv, img, lines, ocr, lh, runs, mask);
    } finally {
      mask.delete();
    }
  }

  // Label crop box for a run: from x0 - CROP_CAP*lh (never left of leftBound),
  // padded by CROP_PAD*lh on both sides, clamped to the page and to leftBound.
  // Rows span CROP_TOP*lh above the leader to one px below it.
  function labelCropBox(run, leftBound, lh, W, H) {
    const { y, x0 } = run;
    const top = y - CROP_TOP * lh;
    const bottom = y + 1;
    let left = Math.max(leftBound, x0 - CROP_CAP * lh);
    left = Math.max(leftBound, left - CROP_PAD * lh);
    return cropQuad(left, top, x0 + CROP_PAD * lh, bottom, W, H);
  }

  // Labels and values for the runs. `mask` is the page ink mask, owned by the caller.
  async function labelAndValue(cv, img, lines, ocr, lh, runs, mask) {
    const W = img.cols;
    const H = img.rows;
    const geo = lines.map(lineGeom);
    const jobs = [];

    const fields = runs.map((run, i) => {
      const { y, x0, x1 } = run;
      const top = y - CROP_TOP * lh;
      const bottom = y + 1;
      const leftBound = leftBoundOf(runs, i);

      const labelParts = geo
        .filter(
          (g) =>
            g.cy >= top &&
            g.cy <= bottom &&
            (g.left + g.right) / 2 > leftBound &&
            g.right <= x0 + 0.5 * lh,
        )
        .sort((a, b) => a.left - b.left);
      let label = cleanText(labelParts.map((g) => g.text).join(" "));
      if (!label) {
        const box = labelCropBox(run, leftBound, lh, W, H);
        if (box) jobs.push({ box, field: i, kind: "label" });
      }

      // A line that starts left of the field (x0 - 0.5*lh) covers the label
      // text, so it is not part of the value.
      const valueParts = geo
        .filter((g) => {
          const mid = (g.left + g.right) / 2;
          return mid >= x0 && mid <= x1 && g.cy >= top && g.cy <= bottom && g.left >= x0 - 0.5 * lh;
        })
        .sort((a, b) => a.left - b.left);
      let value = cleanText(valueParts.map((g) => g.text).join(" "));
      const filled = filledShare(cv, img, run, lh, mask) > 0.15;
      if (filled && !value) {
        const box = cropQuad(x0 - CROP_PAD * lh, top, x1 + CROP_PAD * lh, bottom, W, H);
        if (box) jobs.push({ box, field: i, kind: "value" });
      }
      return {
        label,
        value,
        filled,
        box: quad(x0, Math.round(y - lh), x1, y + 2),
      };
    });

    if (jobs.length) {
      const res = await ocr.readRegions(img, jobs.map((j) => j.box));
      jobs.forEach((j, k) => {
        const text = cleanText(res[k].text);
        if (j.kind === "label") fields[j.field].label = text;
        else fields[j.field].value = text;
      });
    }
    return fields.map((f, i) => ({ ...f, label: f.label || `ช่องที่ ${i + 1}` }));
  }

  return {
    pageLineHeight,
    chainSegments,
    orderRuns,
    leftBoundOf,
    labelCropBox,
    findLeaders,
    filledShare,
    cleanText,
    detectFields,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = Fields;
else self.Fields = Fields;
