from __future__ import annotations

from modernize.models import SmellKind, TransformKind
from modernize.smells import candidate_transforms, detect

PY2 = """
def f(items):
    print "total", len(items)
    for i in xrange(3):
        print >>sys.stderr, i
    if isinstance(items, basestring):
        return unicode(items)
    return items.iteritems()
"""

PY3 = """
import pickle, os, subprocess, imp

class A(object):
    pass

def g(x, acc=[], opts={}):
    assert x, "x required"
    try:
        return eval(x) + "%s" % x
    except:
        os.system("ls " + x)
        subprocess.run(x, shell=True)
        exec(x)
        return pickle.loads(x)
"""


def kinds(source: str, path: str = "mod.py") -> set[SmellKind]:
    return {s.kind for s in detect(source, path)}


def test_py2_smells_detected_without_parsing() -> None:
    found = kinds(PY2)
    assert {
        SmellKind.PY2_PRINT,
        SmellKind.PY2_XRANGE,
        SmellKind.PY2_UNICODE,
        SmellKind.PY2_DICT_ITER,
        SmellKind.SYNTAX_ERROR,
    } <= found
    prints = [s for s in detect(PY2, "m.py") if s.kind == SmellKind.PY2_PRINT]
    assert [s.line for s in prints] == [3, 5]


def test_ast_smells_detected() -> None:
    found = kinds(PY3)
    assert {
        SmellKind.OBJECT_BASE,
        SmellKind.MUTABLE_DEFAULT,
        SmellKind.ASSERT_VALIDATION,
        SmellKind.EVAL_USAGE,
        SmellKind.PERCENT_FORMAT,
        SmellKind.BARE_EXCEPT,
        SmellKind.SHELL_INJECTION,
        SmellKind.EXEC_USAGE,
        SmellKind.INSECURE_DESERIALIZATION,
        SmellKind.DEPRECATED_MODULE,
        SmellKind.MISSING_TYPE_HINTS,
        SmellKind.MISSING_FUTURE_ANNOTATIONS,
    } <= found
    assert SmellKind.SYNTAX_ERROR not in found
    assert len([s for s in detect(PY3) if s.kind == SmellKind.MUTABLE_DEFAULT]) == 2


def test_print_function_and_attribute_access_are_not_smells() -> None:
    src = "print('x')\nprint(1, end='')\nobj.print = 1\nlogger.xrange\nx = {}.items()\n"
    found = kinds(src)
    assert SmellKind.PY2_PRINT not in found
    assert SmellKind.PY2_XRANGE not in found
    assert SmellKind.PY2_DICT_ITER not in found


def test_asserts_in_test_files_are_fine() -> None:
    assert SmellKind.ASSERT_VALIDATION not in kinds("assert 1\n", "tests/test_x.py")


def test_deprecated_attributes_and_has_key() -> None:
    src = "import time, string\nt = time.clock()\ns = string.letters\nd.has_key(1)\n"
    found = [s for s in detect(src) if s.kind == SmellKind.DEPRECATED_API]
    assert len(found) == 3
    has_key = [s for s in found if "has_key" in s.message]
    assert has_key and not has_key[0].fixable


def test_fixable_flag_and_candidate_order() -> None:
    smells = detect(PY2)
    assert all(s.fixable for s in smells if s.kind == SmellKind.PY2_PRINT)
    cands = candidate_transforms(smells)
    assert cands[0] == TransformKind.PY2_PRINT_TO_FUNCTION
    assert len(cands) == len(set(cands))


def test_snippets_are_capped_and_sorted() -> None:
    src = "def f(x=[]):\n    return eval('" + "a" * 1000 + "')\n"
    smells = detect(src)
    assert all(len(s.snippet) <= 300 for s in smells)
    assert [s.line for s in smells] == sorted(s.line for s in smells)


def test_oversized_source_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        detect("x = 1\n" * 200_000)
