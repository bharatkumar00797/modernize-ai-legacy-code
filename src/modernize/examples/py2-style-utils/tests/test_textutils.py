import textutils


def test_banner_prints_and_returns(capsys):
    result = textutils.banner("hi", width=6)
    assert result == "======\n  hi  \n======"
    assert capsys.readouterr().out == "======\n  hi  \n======\n"


def test_number_lines():
    assert textutils.number_lines("a\nb") == ["1: a", "2: b"]


def test_to_text():
    assert textutils.to_text(42) == "42"
    assert textutils.to_text("x") == "x"


def test_word_counts():
    assert textutils.word_counts("b a b") == "a=1, b=2"


def test_greeter():
    assert textutils.Greeter("Ada").greet() == "Hello, Ada!"
