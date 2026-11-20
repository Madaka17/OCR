"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const SummaryView = require(path.resolve(__dirname, "..", "site", "js", "summary_view.js"));

const RESULT = {
  doc_type: "invoice",
  title: "ใบแจ้งหนี้ #123",
  summary: "บริษัท A ออกใบแจ้งหนี้ให้บริษัท B",
  key_points: [
    { text: "ยอดรวมค้างชำระ", page: 2 },
    { text: "กำหนดชำระภายในเดือนหน้า", page: null },
  ],
  parties: ["บริษัท A", "บริษัท B"],
  dates: [{ label: "วันที่ออก", value: "2026-11-01", page: 1, check: "ok" }],
  amounts: [
    { label: "ยอดรวม", value: "12,000 บาท", page: 2, check: "ok" },
    { label: "ค่าปรับ", value: "500 บาท", page: 3, check: "none" },
  ],
};

test("stateLabel: short has its Thai label", () => {
  assert.equal(SummaryView.stateLabel("short"), "ข้อความสั้น ไม่ต้องสรุป");
});

test("stateLabel: null and undefined give empty string", () => {
  assert.equal(SummaryView.stateLabel(null), "");
  assert.equal(SummaryView.stateLabel(undefined), "");
});

test("isBusy: pending and running are busy, done is not", () => {
  assert.ok(SummaryView.isBusy("pending") && SummaryView.isBusy("running"));
  assert.ok(!SummaryView.isBusy("done"));
});

test("summaryToText: unchecked amount includes the OCR warning", () => {
  assert.ok(SummaryView.summaryToText(RESULT).includes("⚠ ไม่พบในข้อความ OCR"));
});

test("summaryToText: null page is left out of its line", () => {
  const lines = SummaryView.summaryToText(RESULT).split("\n");
  const line = lines.find((l) => l.includes("กำหนดชำระภายในเดือนหน้า"));
  assert.ok(line, "key point line present");
  assert.ok(!line.includes("(หน้า"));
});

test("summaryToText: sections come in order title, key points, parties, dates, amounts", () => {
  const text = SummaryView.summaryToText(RESULT);
  const positions = [
    text.indexOf("ใบแจ้งหนี้ #123"),
    text.indexOf("ประเด็นสำคัญ"),
    text.indexOf("ผู้เกี่ยวข้อง"),
    text.indexOf("วันที่"),
    text.indexOf("จำนวนเงิน"),
  ];
  for (const p of positions) assert.ok(p >= 0, "every section present");
  assert.deepEqual([...positions].sort((a, b) => a - b), positions);
});

test("progressText: [3, 13] gives 3/13 ช่วง", () => {
  assert.equal(SummaryView.progressText([3, 13]), "3/13 ช่วง");
});

test("progressText: null gives empty string", () => {
  assert.equal(SummaryView.progressText(null), "");
});
