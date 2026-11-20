// Port of OpenThai2.0-OCR-Text v2.0.0 runtime/thai_norm.py (canonical) and the
// fix_scripts / _enum / invalid_share / _is_thai helpers of line_engine.py.
(function () {
  "use strict";

  // Legacy Thai glyph variants (shifted/lowered marks, tail-less ฐ ญ) -> standard code points.
  const PUA = {
    0xF700: 0x0E10, 0xF701: 0x0E34, 0xF702: 0x0E35, 0xF703: 0x0E36, 0xF704: 0x0E37,
    0xF705: 0x0E48, 0xF706: 0x0E49, 0xF707: 0x0E4A, 0xF708: 0x0E4B, 0xF709: 0x0E4C,
    0xF70A: 0x0E48, 0xF70B: 0x0E49, 0xF70C: 0x0E4A, 0xF70D: 0x0E4B, 0xF70E: 0x0E4C,
    0xF70F: 0x0E0D, 0xF710: 0x0E31, 0xF711: 0x0E4D, 0xF712: 0x0E47, 0xF713: 0x0E48,
    0xF714: 0x0E49, 0xF715: 0x0E4A, 0xF716: 0x0E4B, 0xF717: 0x0E4C, 0xF718: 0x0E38,
    0xF719: 0x0E39, 0xF71A: 0x0E3A,
  };
  // ZWSP, ZWNJ, ZWJ, word joiner, soft hyphen, BOM.
  const DROP = new Set([0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0xFEFF]);

  const VOWEL_MARKS = new Set([0x0E31, 0x0E34, 0x0E35, 0x0E36, 0x0E37, 0x0E3A, 0x0E38, 0x0E39, 0x0E47]);
  const TONE_MARKS = new Set([0x0E48, 0x0E49, 0x0E4A, 0x0E4B, 0x0E4C, 0x0E4E]);
  // vowel | tone | nikhahit (U+0E4D)
  const MARK_RUN = /[ัิีึืฺุู็่้๊๋์ํ๎]{2,}/g;
  // (tone?) nikhahit (tone?) sara aa  ->  tone + sara am
  const SARA_AM = /([่-๋]?)ํ([่-๋]?)า/g;
  const SPACES = /[ \t\f\v]+/g;

  // Python str.isspace() set, used for str.strip() and regex \s.
  const PY_SPACE_SRC =
    "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
  const PY_STRIP = new RegExp("^[" + PY_SPACE_SRC + "]+|[" + PY_SPACE_SRC + "]+$", "g");

  // Python: _LONE_W / _ZERO_IN_THAI in line_engine.py.
  const LONE_W = /(?<![A-Za-z])[Ww](?=\.?[ก-ฮเ-ไ])/g;
  const ZERO_IN_THAI = /(?<=[๐-๙])[Oo0]|[Oo0](?=[๐-๙])/g;

  const ENUM = new RegExp(
    "^[" + PY_SPACE_SRC + "]*\\(?([0-9\\u0E50-\\u0E59]{1,4})[" + PY_SPACE_SRC + "]*[.)]",
  );

  const DIGITS = new Set("0123456789๐๑๒๓๔๕๖๗๘๙");

  // _MARKS: vowel, tone, nikhahit marks used by invalid_share.
  const MARKS = new Set([
    0x0E31, 0x0E34, 0x0E35, 0x0E36, 0x0E37, 0x0E38, 0x0E39, 0x0E3A,
    0x0E47, 0x0E48, 0x0E49, 0x0E4A, 0x0E4B, 0x0E4C, 0x0E4D, 0x0E4E,
  ]);
  // _LEADING: เ แ โ ใ ไ.
  const LEADING = new Set([0x0E40, 0x0E41, 0x0E42, 0x0E43, 0x0E44]);

  function isThai(ch) {
    const cp = ch.codePointAt(0);
    return cp !== undefined && cp >= 0x0E00 && cp <= 0x0E7F;
  }

  function isCons(ch) {
    // undefined (no neighbour) is not a consonant.
    if (ch === undefined) return false;
    const cp = ch.codePointAt(0);
    return cp >= 0x0E01 && cp <= 0x0E2E;
  }

  // Python str.strip() with no arguments.
  function pyStrip(s) {
    return s.replace(PY_STRIP, "");
  }

  // Python str.translate(TRANSLATE) with TRANSLATE = {**PUA, **DROP, 0xA0: 0x20}.
  function translate(text) {
    let out = "";
    for (const ch of text) {
      const cp = ch.codePointAt(0);
      if (DROP.has(cp)) continue;
      if (cp === 0x00A0) out += " ";
      else if (PUA[cp] !== undefined) out += String.fromCodePoint(PUA[cp]);
      else out += ch;
    }
    return out;
  }

  // Python _order: stable sort of a mark run by group, then drop a char equal to the last kept one.
  function order(run) {
    const group = (ch) => {
      const cp = ch.codePointAt(0);
      return VOWEL_MARKS.has(cp) ? 0 : TONE_MARKS.has(cp) ? 1 : 2;
    };
    const sorted = Array.from(run).sort((a, b) => group(a) - group(b));
    let out = "";
    let last = null;
    for (const ch of sorted) {
      if (ch !== last) {
        out += ch;
        last = ch;
      }
    }
    return out;
  }

  function canonical(text) {
    text = (text === null || text === undefined ? "" : String(text));
    text = translate(text).normalize("NFC");
    text = text.replace(MARK_RUN, order);
    text = text.replace(SARA_AM, (_m, a, b) => a + b + "ำ");
    text = text.replaceAll("เเ", "แ").replaceAll("ำำ", "ำ");
    text = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
    return pyStrip(
      text
        .split("\n")
        .map((line) => pyStrip(line.replace(SPACES, " ")))
        .join("\n"),
    );
  }

  function fixScripts(text) {
    text = text.replace(LONE_W, "พ");
    return text.replace(ZERO_IN_THAI, "๐");
  }

  // Python _enum: leading enumerator ("3.", "(๑๒)") as an int, else null.
  function enumNumber(text) {
    const m = ENUM.exec(text);
    if (!m) return null;
    const ascii = Array.from(m[1], (ch) => {
      const cp = ch.codePointAt(0);
      return cp >= 0x0E50 && cp <= 0x0E59 ? String.fromCharCode(0x30 + cp - 0x0E50) : ch;
    }).join("");
    return parseInt(ascii, 10);
  }

  // Python invalid_share: share of Thai code points that break Thai spelling.
  function invalidShare(text) {
    const chars = Array.from(text);
    let thai = 0;
    let bad = 0;
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i];
      if (!isThai(ch)) continue;
      thai++;
      const prev = i ? chars[i - 1] : " ";
      const cp = ch.codePointAt(0);
      if (
        MARKS.has(cp) &&
        !(isCons(prev) || (MARKS.has(prev.codePointAt(0)) && i >= 2 && isCons(chars[i - 2])))
      ) {
        bad++;
      } else if (LEADING.has(cp) && !(i + 1 < chars.length && isCons(chars[i + 1]))) {
        bad++;
      }
    }
    return thai ? bad / thai : 0.0;
  }

  const api = { canonical, fixScripts, invalidShare, enumNumber, isThai, DIGITS };

  if (typeof module !== "undefined") module.exports = api;
  else self.ThaiText = api;
})();
