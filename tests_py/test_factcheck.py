from server.factcheck import normalize, find_value, check_items

PAGES = {
    5: "มูลค่า 136,531,093.13 บาท",
    33: "อาคาร 34,358,800 บาท",
    81: "วันที่ 8 เดือน พฤษภาคม พ.ศ. 2568",
    85: "สัญญาจำนองฉบับลงวันที่ 16 มาราคม 2567",
    113: "ใช้ได้จนถึงวันที่๒๗ พ.. อแัจลี",
    1: "ประเมิน ณ วันที่ 21 เม.ย. 2569",
    10: "ราคา 12,000,000 บาท",
}

def test_date_with_filler_words_found():
    assert find_value("8 พฤษภาคม 2568", PAGES) == [81]

def test_abbreviated_month_found():
    assert find_value("21 เมษายน 2569", PAGES) == [1]

def test_wrong_month_not_found():
    assert find_value("16 เมษายน 2567", PAGES) == []

def test_invented_date_not_found():
    assert find_value("27 เมษายน 2569", PAGES) == []

def test_date_not_matched_inside_longer_day():
    assert find_value("8 พฤษภาคม 2568", {1: "วันที่ 18 พฤษภาคม 2568"}) == []

def test_amount_with_decimals_and_unit():
    assert find_value("136,531,093.13 บาท", PAGES) == [5]

def test_amount_trailing_zero_decimals_match_integer():
    assert find_value("34,358,800.00 บาท", PAGES) == [33]

def test_amount_not_matched_inside_bigger_number():
    assert find_value("2,000,000", PAGES) == []

def test_thai_digits_normalized():
    assert find_value("27", {1: "หน้า ๒๗"}) == [1]

def test_check_items_corrects_page_and_flags_missing():
    items = [{"label": "a", "value": "136,531,093.13", "page": 9},
             {"label": "b", "value": "27 เมษายน 2569", "page": 113},
             {"label": "c", "value": "ไม่มีตัวเลข", "page": 1}]
    out = check_items(items, PAGES)
    assert [(i["page"], i["check"]) for i in out] == [(5, "ok"), (113, "none"), (1, "none")]
    assert items[0]["page"] == 9  # input not mutated
