from __future__ import annotations

import ast

import pytest

from modernize.models import TransformKind
from modernize.transforms import (
    TRANSFORMS,
    add_future_annotations,
    apply_edits,
    bare_except_to_exception,
    deprecated_api_aliases,
    dict_iter_to_view,
    drop_object_base,
    eval_to_ast_literal,
    get_transform,
    mutable_default_to_none,
    percent_format_to_fstring,
    py2_print_to_function,
    unicode_to_str,
    xrange_to_range,
)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('print "hi"\n', 'print("hi")\n'),
        ("print x, y\n", "print(x, y)\n"),
        ("print x,\n", 'print(x, end=" ")\n'),
        ("print >>sys.stderr, 'err', x\n", "print('err', x, file=sys.stderr)\n"),
        ("print\n", "print()\n"),
        ("if a:\n    print a  # note\n", "if a:\n    print(a)  # note\n"),
        ("print('already')\n", "print('already')\n"),
        ('print "a" % (b, c); x = 1\n', 'print("a" % (b, c)); x = 1\n'),
    ],
)
def test_py2_print_to_function(before: str, after: str) -> None:
    assert py2_print_to_function(before) == after


def test_token_renames() -> None:
    assert xrange_to_range("for i in xrange(3): pass\n") == "for i in range(3): pass\n"
    assert unicode_to_str("isinstance(x, basestring) or unicode(x) or unichr(65)\n") == (
        "isinstance(x, str) or str(x) or chr(65)\n"
    )
    assert dict_iter_to_view("d.iteritems(); d.itervalues(); d.iterkeys()\n") == (
        "d.items(); d.values(); d.keys()\n"
    )
    # strings, comments and attributes are untouched
    src = "s = 'xrange'  # xrange\nobj.xrange()\n"
    assert xrange_to_range(src) == src


def test_rename_refuses_when_module_defines_the_name() -> None:
    src = "def xrange(n):\n    return []\nxrange(3)\n"
    assert xrange_to_range(src) == src
    src2 = "unicode = str\nunicode(1)\n"
    assert unicode_to_str(src2) == src2


def test_deprecated_api_aliases() -> None:
    src = "t = time.clock()\nc = os.getcwdu()\nl = string.letters + string.lowercase\n"
    assert deprecated_api_aliases(src) == (
        "t = time.perf_counter()\nc = os.getcwd()\nl = string.ascii_letters + string.ascii_lowercase\n"
    )
    assert deprecated_api_aliases("x.time.clock()\n") == "x.time.clock()\n"


def test_bare_except() -> None:
    src = "try:\n    pass\nexcept:\n    pass\ntry:\n    pass\nexcept ValueError:\n    pass\n"
    out = bare_except_to_exception(src)
    assert "except Exception:" in out and "except ValueError:" in out
    ast.parse(out)


def test_mutable_default_to_none_with_docstring_and_kwonly() -> None:
    src = (
        "def f(a, items=[], *, opts={}):\n"
        '    """Doc."""\n'
        "    items.append(a)\n"
        "    return items, opts\n"
    )
    out = mutable_default_to_none(src)
    assert "def f(a, items=None, *, opts=None):" in out
    assert "    if items is None:\n        items = []\n" in out
    assert "    if opts is None:\n        opts = {}\n" in out
    namespace: dict[str, object] = {}
    exec(compile(out, "<t>", "exec"), namespace)
    f = namespace["f"]
    assert callable(f)
    assert f(1) == ([1], {})
    assert f(2) == ([2], {})


def test_mutable_default_skips_annotated_and_one_liners() -> None:
    src = "def f(x: list = []):\n    return x\ndef g(y=[]): return y\n"
    assert mutable_default_to_none(src) == src


def test_eval_to_literal_eval_adds_import() -> None:
    src = '"""Doc."""\nimport json\n\n\ndef p(raw):\n    return eval(raw)\n'
    out = eval_to_ast_literal(src)
    assert "import ast\nimport json" in out
    assert "ast.literal_eval(raw)" in out
    ns: dict[str, object] = {}
    exec(compile(out, "<t>", "exec"), ns)
    parse_fn = ns["p"]
    assert callable(parse_fn)
    assert parse_fn("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        parse_fn("__import__('os').getcwd()")


def test_eval_with_globals_or_shadowed_ast_is_left_alone() -> None:
    assert eval_to_ast_literal("eval(x, {})\n") == "eval(x, {})\n"
    src = "ast = 1\neval(x)\n"
    assert eval_to_ast_literal(src) == src


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('x = "Hello, %s!" % name\n', 'x = f"Hello, {name}!"\n'),
        ("x = '%d: %s' % (i + 1, row[0])\n", "x = f'{i + 1:d}: {row[0]}'\n"),
        (
            'x = "%-5s|%05.2f|%r|%d%%" % (a, b.c, d, n)\n',
            'x = f"{a:<5}|{b.c:05.2f}|{d!r}|{n:d}%"\n',
        ),
        ('x = "{literal} %s" % a\n', 'x = "{literal} %s" % a\n'),  # braces: skipped
        ('x = "%(a)s" % d\n', 'x = "%(a)s" % d\n'),  # mapping style: skipped
        ('x = "%s %s" % (a,)\n', 'x = "%s %s" % (a,)\n'),  # arity mismatch: skipped
        ('x = "%s" % d["k"]\n', 'x = "%s" % d["k"]\n'),  # quotes in expression: skipped
        ('x = "a\\n%s" % b\n', 'x = "a\\n%s" % b\n'),  # escapes: skipped
    ],
)
def test_percent_format_to_fstring(before: str, after: str) -> None:
    assert percent_format_to_fstring(before) == after


def test_percent_d_on_float_changes_behavior_which_the_gate_must_catch() -> None:
    out = percent_format_to_fstring('def p(r):\n    return "%d%%" % r\n')
    ns: dict[str, object] = {}
    exec(compile(out, "<t>", "exec"), ns)
    fn = ns["p"]
    assert callable(fn)
    with pytest.raises(ValueError):
        fn(33.3)


def test_drop_object_base_and_future_annotations() -> None:
    assert drop_object_base("class A(object):\n    pass\n") == "class A:\n    pass\n"
    assert drop_object_base("class B(Base):\n    pass\n") == "class B(Base):\n    pass\n"
    src = '#!/usr/bin/env python\n"""Doc."""\nimport os\n'
    out = add_future_annotations(src)
    assert out.splitlines()[2] == "from __future__ import annotations"
    assert add_future_annotations(out) == out
    assert add_future_annotations("import os\n").startswith("from __future__ import annotations\n")


def test_ast_transforms_noop_on_unparseable_source() -> None:
    src = 'print "py2"\ndef f(x=[]):\n    pass\n'
    for kind, t in TRANSFORMS.items():
        if t.needs_parse:
            assert t.apply(src) == src, kind


def test_transforms_are_idempotent_on_their_output() -> None:
    src = 'class A(object):\n    def f(self, x=[]):\n        try:\n            return "%s" % x\n        except:\n            return eval(x)\n'
    for t in TRANSFORMS.values():
        once = t.apply(src)
        assert t.apply(once) == once, t.id


def test_registry_is_the_allowlist() -> None:
    assert get_transform("xrange_to_range") is not None
    assert get_transform("rm_rf_everything") is None
    assert get_transform(TransformKind.MODEL_DIFF.value) is None
    assert set(TRANSFORMS) == set(TransformKind) - {TransformKind.MODEL_DIFF}


def test_apply_edits_drops_overlaps() -> None:
    assert apply_edits("abcdef", [(0, 3, "X"), (2, 4, "Y"), (5, 6, "Z")]) == "abYeZ"
