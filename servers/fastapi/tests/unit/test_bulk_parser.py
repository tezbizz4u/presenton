from services.bulk_blueprint import parse_blueprint


def test_markdown_sections_and_slide_hint():
    text = "# Course\n## Module 1 - Rotterdam\nCriteria and phenotypes. 12 slides\n## Module 2\nHormones\n"
    rows = parse_blueprint(text, 25)
    assert [r["name"] for r in rows] == ["Module 1 - Rotterdam", "Module 2"]
    assert rows[0]["slides"] == 12 and rows[1]["slides"] == 25


def test_pipe_lines():
    rows = parse_blueprint("A | 10 | brief a\nB | x | brief b", 30)
    assert rows[0]["slides"] == 10 and rows[1]["slides"] == 30


def test_csv_and_json():
    rows = parse_blueprint("name,slides,brief\nU1,8,Intro to PCOS\n", 25)
    assert rows == [{"name": "U1", "brief": "Intro to PCOS", "slides": 8}]
    rows = parse_blueprint('[{"name":"U2","brief":"b","slides":5}]', 25)
    assert rows[0]["slides"] == 5


def test_nothing_found():
    assert parse_blueprint("just some text with no structure", 25) == []
