from __future__ import annotations

from pysilica.analyze.normalize import RULE_STRIP_COMMENTS, strip_comments


def test_strip_comments_slash() -> None:
    text = "ret // return to caller"
    res, mod = strip_comments(text)
    assert mod is True
    assert res == "ret"
    assert RULE_STRIP_COMMENTS == "strip_comments"


def test_strip_comments_semicolon() -> None:
    text = "nop ; no operation"
    res, mod = strip_comments(text)
    assert mod is True
    assert res == "nop"


def test_strip_comments_none() -> None:
    text = "add x0, x1, x2"
    res, mod = strip_comments(text)
    assert mod is False
    assert res == "add x0, x1, x2"
