from services import bulk_quality_gate as g


def rec(i, layout="a", notes="n" * 700, urls=(), text="Real content", title="T"):
    return {"index": i, "layout": layout, "notes": notes, "urls": list(urls), "text": text, "title": title}


def test_clean_deck_passes():
    slides = [rec(0, "a"), rec(1, "b", urls=["u1"]), rec(2, "c", urls=["u2"])]
    assert g.check_slides(slides) == {}
    assert g.score(3, {}) == 100


def test_each_rule_flags_only_its_slide():
    slides = [
        rec(0, "a"),
        rec(1, "b", notes="short"),
        rec(2, "c", urls=["u1"]),
        rec(3, "d", urls=["u1"]),
        rec(4, "e", text="Lorem ipsum dolor"),
        rec(5, "f", title=""),
        rec(6, "x"), rec(7, "x"), rec(8, "x"),
    ]
    issues = g.check_slides(slides)
    assert issues[1] == ["notes_short"]
    assert issues[3] == ["dup_image"]
    assert issues[4] == ["placeholder"]
    assert issues[5] == ["placeholder"]
    assert issues[8] == ["layout_run"]
    assert 0 not in issues and 2 not in issues and 6 not in issues and 7 not in issues
    assert g.score(9, issues) == round(100 * (1 - 5 / 9))


def test_fix_prompt_only_for_given_codes():
    p = g.fix_prompt(["notes_short"])
    assert "speaker note" in p and "image" in p  # says keep images
    assert "layout" not in g.fix_prompt(["dup_image"]).lower().split("keep the slide text and layout")[0]
