"""Check that dates and amounts extracted by an LLM appear in the OCR text."""

import re

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

_ABBREV_MONTHS = {
    "ม.ค.": "มกราคม",
    "ก.พ.": "กุมภาพันธ์",
    "มี.ค.": "มีนาคม",
    "เม.ย.": "เมษายน",
    "พ.ค.": "พฤษภาคม",
    "มิ.ย.": "มิถุนายน",
    "ก.ค.": "กรกฎาคม",
    "ส.ค.": "สิงหาคม",
    "ก.ย.": "กันยายน",
    "ต.ค.": "ตุลาคม",
    "พ.ย.": "พฤศจิกายน",
    "ธ.ค.": "ธันวาคม",
}

_FULL_MONTHS = tuple(_ABBREV_MONTHS.values())

# Longer forms first so "พ.ศ." is removed before the bare "พ.ศ".
_FILLERS = ("วันที่", "เดือน", "พ.ศ.", "พ.ศ")

_NUMBER = re.compile(r"\d[\d,./-]*\d|\d")


def normalize(text: str) -> str:
    """Convert Thai digits, expand abbreviated months and drop filler words and whitespace."""
    text = text.translate(_THAI_DIGITS)
    for short, full in _ABBREV_MONTHS.items():
        text = text.replace(short, full)
    for filler in _FILLERS:
        text = text.replace(filler, "")
    return re.sub(r"\s+", "", text)


def _numbers(normalized: str) -> list[str]:
    return [re.sub(r"\.0+$", "", n) for n in _NUMBER.findall(normalized)]


def find_value(value: str, pages: dict[int, str]) -> list[int]:
    """Return sorted page numbers whose normalized text contains the value."""
    nv = normalize(value)
    if any(month in nv for month in _FULL_MONTHS):
        # Digit guards only where the value itself starts or ends with a digit, so
        # "พฤษภาคม 2568" still matches after a day and "256" never matches "2567".
        before = r"(?<!\d)" if nv[:1].isdigit() else ""
        after = r"(?!\d)" if nv[-1:].isdigit() else ""
        pattern = re.compile(before + re.escape(nv) + after)
        return sorted(p for p, text in pages.items() if pattern.search(normalize(text)))

    numbers = _numbers(nv)
    if not numbers:
        return []
    patterns = [
        re.compile(r"(?<![\d,.])" + re.escape(n) + r"(?:\.0+)?(?![\d,]|\.\d)") for n in numbers
    ]
    return sorted(
        p for p, text in pages.items()
        if all(pat.search(normalize(text)) for pat in patterns)
    )


def check_items(items: list[dict], pages: dict[int, str]) -> list[dict]:
    """Return copies of items with a check result and a corrected page where possible."""
    out = []
    for item in items:
        found = find_value(item["value"], pages)
        new = dict(item)
        if found:
            new["check"] = "ok"
            if item["page"] not in found:
                new["page"] = found[0]
        else:
            new["check"] = "none"
        out.append(new)
    return out
