"""kokoro 前處理：去除中文、切段。"""

from core.tts import _chunk_text, _english_only


def test_english_only_strips_cjk_and_keeps_english() -> None:
    text = "例如 comfortable 這個字，正確是 3 個音節：KUMF-ter-bul。"
    out = _english_only(text)
    assert "例" not in out and "，" not in out
    assert "comfortable" in out
    assert "KUMF-ter-bul" in out


def test_english_only_drops_ipa_and_empty_parens() -> None:
    out = _english_only("comfortable  ˈkʌm.fɚ.tə.bəl （音標）")
    assert "ˈ" not in out and "ʌ" not in out
    assert out.startswith("comfortable")


def test_english_only_returns_empty_for_pure_chinese() -> None:
    assert _english_only("我本身沒有可以直接呼叫的工具。\n不過有兩件事我可以做：") == ""


def test_english_only_keeps_pure_english_unchanged() -> None:
    assert _english_only("Hello there! How are you?") == "Hello there! How are you?"


def test_chunk_text_respects_limit() -> None:
    text = " ".join(["This is a fairly normal English sentence."] * 30)
    chunks = _chunk_text(text, max_chars=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_chunk_text_splits_long_sentence_without_punctuation() -> None:
    text = "word " * 100
    chunks = _chunk_text(text.strip(), max_chars=50)
    assert all(len(c) <= 50 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_english_only_keeps_leading_minus_and_number_lines() -> None:
    out = _english_only("It is -5 degrees today")
    assert out == "It is -5 degrees today."


def test_english_only_strips_bullets_and_ends_each_line() -> None:
    out = _english_only("- first item\n• second item!\n例如：third")
    assert out == "first item.\nsecond item!\nthird."


def test_chunk_text_splits_on_cjk_punctuation() -> None:
    text = "第一句話。" * 10 + "第二句話！" * 10
    chunks = _chunk_text(text, max_chars=30)
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text


def test_chunk_text_empty_input() -> None:
    assert _chunk_text("   ") == []
