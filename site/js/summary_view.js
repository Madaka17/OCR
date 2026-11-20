// Pure helpers for the AI summary card (labels, busy check, plain-text copy, progress text).
(function () {
  "use strict";

  const STATE_LABELS = {
    pending: "รอสรุป",
    running: "กำลังสรุป",
    done: "สรุปแล้ว",
    failed: "สรุปไม่สำเร็จ",
    short: "ข้อความสั้น ไม่ต้องสรุป",
  };

  const UNVERIFIED = " ⚠ ไม่พบในข้อความ OCR";

  function stateLabel(state) {
    if (state === null || state === undefined) return "";
    return STATE_LABELS[state] || "";
  }

  function isBusy(state) {
    return state === "pending" || state === "running";
  }

  function pageSuffix(page) {
    return page === null || page === undefined ? "" : " (หน้า " + page + ")";
  }

  // Plain text for the "คัดลอกสรุป" button: title, summary, key points, parties, dates, amounts.
  function summaryToText(result) {
    const lines = [];
    lines.push(result.title);
    lines.push(result.summary);
    lines.push("");
    lines.push("ประเด็นสำคัญ");
    for (const kp of result.key_points) lines.push("- " + kp.text + pageSuffix(kp.page));
    lines.push("");
    lines.push("ผู้เกี่ยวข้อง");
    for (const p of result.parties) lines.push("- " + p);
    lines.push("");
    lines.push("วันที่");
    for (const d of result.dates) lines.push(valueLine(d));
    lines.push("");
    lines.push("จำนวนเงิน");
    for (const a of result.amounts) lines.push(valueLine(a));
    return lines.join("\n");
  }

  function valueLine(item) {
    const warn = item.check === "none" ? UNVERIFIED : "";
    return "- " + item.label + ": " + item.value + pageSuffix(item.page) + warn;
  }

  function progressText(progress) {
    if (progress === null || progress === undefined) return "";
    return progress[0] + "/" + progress[1] + " ช่วง";
  }

  const api = { STATE_LABELS, stateLabel, isBusy, summaryToText, progressText };

  if (typeof module !== "undefined") module.exports = api;
  else self.SummaryView = api;
})();
