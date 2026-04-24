from NYSE_DATA import telegram_bot as tb


def test_sanitize_basic():
    s = "Hello <span style='color:red'>X</span> OK <div>NO</div>"
    out = tb.sanitize_for_telegram(s)
    assert '<span' not in out
    assert 'style=' not in out
    assert '<div' not in out
    assert '' in out


def test_strip_script():
    s = "<script>alert(1)</script>Safe"
    out = tb.sanitize_for_telegram(s)
    assert 'script' not in out


def test_allow_a_tag():
    s = "link"
    out = tb.sanitize_for_telegram(s)
    assert '<a' in out and 'href' in out
