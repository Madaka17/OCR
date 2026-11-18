// Pure helpers for the server job queue UI (mode choice, chunking, ETA text).
(function () {
  "use strict";

  const BROWSER_MAX_BYTES = 50 * 1024 * 1024;
  const BROWSER_MAX_PAGES = 30;

  // "browser" when the file is small and short enough to read locally, else "server".
  // pages === null means the page count was not taken (only done for files <= 50 MB).
  function decideMode(sizeBytes, pages) {
    if (sizeBytes > BROWSER_MAX_BYTES) return "server";
    if (pages === null || pages === undefined) return "server";
    return pages <= BROWSER_MAX_PAGES ? "browser" : "server";
  }

  // [start, end) byte ranges covering size bytes in pieces of at most chunk bytes.
  function chunkRanges(size, chunk) {
    const out = [];
    for (let start = 0; start < size; start += chunk) {
      out.push([start, Math.min(start + chunk, size)]);
    }
    return out;
  }

  function formatEta(seconds) {
    if (seconds === null || seconds === undefined) return "กำลังประเมิน…";
    if (seconds < 60) return "ไม่ถึง 1 นาที";
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return "ประมาณ " + minutes + " นาที";
    return "ประมาณ " + Math.floor(minutes / 60) + " ชม. " + (minutes % 60) + " นาที";
  }

  function formatBytes(n) {
    const units = ["B", "KB", "MB", "GB"];
    let value = n;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit++;
    }
    if (unit === 0) return value + " B";
    return value.toFixed(1) + " " + units[unit];
  }

  // Destination after login: only a same-site path. Tab, CR, LF and backslash are rejected:
  // browsers strip tab/CR/LF from URLs, so "/<TAB>/x" would become "//x", and some treat
  // a backslash as a slash.
  function safeNext(value, origin) {
    if (typeof value !== "string" || value.charAt(0) !== "/") return "/";
    if (/[\x5c\x09\x0a\x0d]/.test(value)) return "/";
    let u;
    try {
      u = new URL(value, origin);
    } catch (err) {
      return "/";
    }
    if (u.origin !== origin) return "/";
    return u.pathname + u.search + u.hash;
  }

  // A chunk upload is retried only for network trouble and for 408, 429 and 5xx.
  function isRetryableStatus(status) {
    return status === 408 || status === 429 || (status >= 500 && status <= 599);
  }

  // Wait before retry number attempt+1 (attempt counts from 0): 1 s, 2 s, 4 s.
  function chunkRetryDelayMs(attempt) {
    return 1000 * Math.pow(2, attempt);
  }

  const api = { decideMode, chunkRanges, formatEta, formatBytes, safeNext, isRetryableStatus, chunkRetryDelayMs };

  if (typeof module !== "undefined") module.exports = api;
  else self.JobsClient = api;
})();
