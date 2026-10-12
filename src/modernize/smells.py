"""Deterministic smell detectors.

Token-level detectors run on any source - including Python 2 files that the
Python 3 parser rejects - while AST detectors run once the module parses.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Iterator

from .models import Severity, Smell, SmellKind, TransformKind

#: Which allowlisted transform addresses which smell. Smells without an entry are
#: reported as residual risks: the agent never "fixes" them with freeform edits.
FIXES: dict[SmellKind, TransformKind] = {
    SmellKind.PY2_PRINT: TransformKind.PY2_PRINT_TO_FUNCTION,
    SmellKind.PY2_XRANGE: TransformKind.XRANGE_TO_RANGE,
    SmellKind.PY2_UNICODE: TransformKind.UNICODE_TO_STR,
    SmellKind.PY2_DICT_ITER: TransformKind.DICT_ITER_TO_VIEW,
    SmellKind.BARE_EXCEPT: TransformKind.BARE_EXCEPT_TO_EXCEPTION,
    SmellKind.MUTABLE_DEFAULT: TransformKind.MUTABLE_DEFAULT_TO_NONE,
    SmellKind.EVAL_USAGE: TransformKind.EVAL_TO_AST_LITERAL,
    SmellKind.PERCENT_FORMAT: TransformKind.PERCENT_FORMAT_TO_FSTRING,
    SmellKind.DEPRECATED_API: TransformKind.DEPRECATED_API_ALIASES,
    SmellKind.OBJECT_BASE: TransformKind.DROP_OBJECT_BASE,
    SmellKind.MISSING_FUTURE_ANNOTATIONS: TransformKind.ADD_FUTURE_ANNOTATIONS,
}

PY2_TEXT_NAMES = {"unicode": "str", "basestring": "str", "unichr": "chr"}
PY2_DICT_ITER = {"iteritems": "items", "itervalues": "values", "iterkeys": "keys"}
DEPRECATED_ATTRS: dict[tuple[str, str], str] = {
    ("time", "clock"): "perf_counter",
    ("os", "getcwdu"): "getcwd",
    ("string", "letters"): "ascii_letters",
    ("string", "lowercase"): "ascii_lowercase",
    ("string", "uppercase"): "ascii_uppercase",
}
DEPRECATED_MODULES = {
    "imp": "importlib",
    "optparse": "argparse",
    "distutils": "setuptools / sysconfig",
    "asyncore": "asyncio",
    "asynchat": "asyncio",
    "cgi": "email / a web framework",
    "pipes": "shlex",
    "telnetlib": "a maintained client library",
    "cPickle": "pickle",
    "urllib2": "urllib.request",
    "ConfigParser": "configparser",
}
MAX_SOURCE_BYTES = 512 * 1024
_STMT_START = {tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING}
_SKIP = {tokenize.COMMENT, tokenize.NL}


def tokens(source: str) -> list[tokenize.TokenInfo] | None:
    """Tokenize ``source`` or return None when even the tokenizer gives up."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return None


def parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def significant(toks: list[tokenize.TokenInfo]) -> list[tokenize.TokenInfo]:
    return [t for t in toks if t.type not in _SKIP]


def py2_print_tokens(toks: list[tokenize.TokenInfo]) -> Iterator[int]:
    """Indexes (into ``significant(toks)``) of Python 2 ``print`` statements."""
    sig = significant(toks)
    for i, tok in enumerate(sig):
        if tok.type != tokenize.NAME or tok.string != "print":
            continue
        prev = sig[i - 1] if i else None
        if prev is not None and not (
            prev.type in _STMT_START or (prev.type == tokenize.OP and prev.string in {";", ":"})
        ):
            continue
        nxt = sig[i + 1] if i + 1 < len(sig) else None
        if nxt is None or nxt.type in {tokenize.NEWLINE, tokenize.ENDMARKER}:
            yield i  # bare ``print``
        elif nxt.type == tokenize.OP and nxt.string in {"(", "=", ".", "[", ","}:
            continue  # already a call (or an unrelated use of the name)
        elif (
            (nxt.type == tokenize.OP and nxt.string == ">>")
            or nxt.type in {tokenize.STRING, tokenize.NAME, tokenize.NUMBER}
            or (nxt.type == tokenize.OP and nxt.string in {"-", "[", "{", "`"})
        ):
            yield i


def _line(source_lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(source_lines):
        return source_lines[lineno - 1].strip()[:300]
    return ""


def _smell(
    kind: SmellKind,
    path: str,
    line: int,
    message: str,
    severity: Severity,
    lines: list[str],
) -> Smell:
    return Smell(
        kind=kind,
        path=path,
        line=max(1, line),
        message=message[:2000],
        severity=severity,
        snippet=_line(lines, line),
        fixable=kind in FIXES,
    )


def _token_smells(source: str, path: str, lines: list[str]) -> list[Smell]:
    toks = tokens(source)
    if toks is None:
        return []
    sig = significant(toks)
    out: list[Smell] = []
    for i in py2_print_tokens(toks):
        out.append(
            _smell(
                SmellKind.PY2_PRINT,
                path,
                sig[i].start[0],
                "Python 2 print statement; use the print() function",
                Severity.HIGH,
                lines,
            )
        )
    for i, tok in enumerate(sig):
        if tok.type != tokenize.NAME:
            continue
        prev = sig[i - 1] if i else None
        nxt = sig[i + 1] if i + 1 < len(sig) else None
        after_dot = prev is not None and prev.type == tokenize.OP and prev.string == "."
        line = tok.start[0]
        if tok.string == "xrange" and not after_dot:
            out.append(
                _smell(
                    SmellKind.PY2_XRANGE,
                    path,
                    line,
                    "xrange() was removed; use range()",
                    Severity.HIGH,
                    lines,
                )
            )
        elif tok.string in PY2_TEXT_NAMES and not after_dot:
            out.append(
                _smell(
                    SmellKind.PY2_UNICODE,
                    path,
                    line,
                    f"{tok.string} does not exist in Python 3; use {PY2_TEXT_NAMES[tok.string]}",
                    Severity.HIGH,
                    lines,
                )
            )
        elif tok.string in PY2_DICT_ITER and after_dot:
            out.append(
                _smell(
                    SmellKind.PY2_DICT_ITER,
                    path,
                    line,
                    f".{tok.string}() was removed; use .{PY2_DICT_ITER[tok.string]}()",
                    Severity.HIGH,
                    lines,
                )
            )
        elif tok.string == "has_key" and after_dot:
            out.append(
                _smell(
                    SmellKind.DEPRECATED_API,
                    path,
                    line,
                    ".has_key() was removed; use the 'in' operator (manual fix)",
                    Severity.HIGH,
                    lines,
                )
            )
        elif (
            not after_dot
            and nxt is not None
            and nxt.string == "."
            and i + 2 < len(sig)
            and (tok.string, sig[i + 2].string) in DEPRECATED_ATTRS
        ):
            new = DEPRECATED_ATTRS[(tok.string, sig[i + 2].string)]
            out.append(
                _smell(
                    SmellKind.DEPRECATED_API,
                    path,
                    line,
                    f"{tok.string}.{sig[i + 2].string} is removed/deprecated; use {tok.string}.{new}",
                    Severity.MEDIUM,
                    lines,
                )
            )
    return out


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or "/tests/" in f"/{path}"


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _ast_smells(tree: ast.Module, path: str, lines: list[str]) -> list[Smell]:
    out: list[Smell] = []

    def add(kind: SmellKind, node: ast.AST, message: str, severity: Severity) -> None:
        out.append(_smell(kind, path, getattr(node, "lineno", 1), message, severity, lines))

    has_future = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )
    has_functions = False
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
                add(
                    SmellKind.PERCENT_FORMAT,
                    node,
                    "printf-style % formatting; prefer f-strings",
                    Severity.LOW,
                )
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            add(
                SmellKind.BARE_EXCEPT,
                node,
                "bare except also swallows KeyboardInterrupt/SystemExit",
                Severity.MEDIUM,
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            has_functions = True
            defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d)]
            for default in defaults:
                if isinstance(default, ast.List | ast.Dict | ast.Set):
                    add(
                        SmellKind.MUTABLE_DEFAULT,
                        default,
                        f"mutable default argument in {node.name}() is shared between calls",
                        Severity.HIGH,
                    )
            params = [
                a
                for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                if a.arg not in {"self", "cls"}
            ]
            if node.returns is None and all(a.annotation is None for a in params):
                add(
                    SmellKind.MISSING_TYPE_HINTS,
                    node,
                    f"{node.name}() has no type hints",
                    Severity.INFO,
                )
        elif isinstance(node, ast.ClassDef):
            if any(isinstance(b, ast.Name) and b.id == "object" for b in node.bases):
                add(
                    SmellKind.OBJECT_BASE,
                    node,
                    f"class {node.name}(object): the explicit object base is redundant",
                    Severity.INFO,
                )
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name == "eval":
                add(
                    SmellKind.EVAL_USAGE,
                    node,
                    "eval() executes arbitrary code; use ast.literal_eval for data",
                    Severity.HIGH,
                )
            elif name == "exec":
                add(SmellKind.EXEC_USAGE, node, "exec() executes arbitrary code", Severity.HIGH)
            elif name in {
                "pickle.load",
                "pickle.loads",
                "cPickle.loads",
                "marshal.loads",
                "shelve.open",
            }:
                add(
                    SmellKind.INSECURE_DESERIALIZATION,
                    node,
                    f"{name}() on untrusted bytes can execute code; use JSON or sign the data",
                    Severity.HIGH,
                )
            elif name == "yaml.load" and not any(k.arg == "Loader" for k in node.keywords):
                add(
                    SmellKind.INSECURE_DESERIALIZATION,
                    node,
                    "yaml.load without a Loader; use yaml.safe_load",
                    Severity.HIGH,
                )
            elif name in {"os.system", "os.popen"} or (
                name.startswith("subprocess.")
                and any(
                    k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                    for k in node.keywords
                )
            ):
                add(
                    SmellKind.SHELL_INJECTION,
                    node,
                    f"{name}() runs a shell; pass an argv list without shell=True",
                    Severity.HIGH,
                )
        elif isinstance(node, ast.Assert) and not _is_test_path(path):
            add(
                SmellKind.ASSERT_VALIDATION,
                node,
                "assert is stripped under python -O; do not use it for validation",
                Severity.MEDIUM,
            )
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                root = name.split(".")[0]
                if root in DEPRECATED_MODULES:
                    add(
                        SmellKind.DEPRECATED_MODULE,
                        node,
                        f"module {root} is deprecated/removed; use {DEPRECATED_MODULES[root]}",
                        Severity.MEDIUM,
                    )
    if has_functions and not has_future:
        out.append(
            _smell(
                SmellKind.MISSING_FUTURE_ANNOTATIONS,
                path,
                1,
                "no 'from __future__ import annotations' (lazy annotations)",
                Severity.INFO,
                lines,
            )
        )
    return out


def detect(source: str, path: str = "module.py") -> list[Smell]:
    """Return every smell found in ``source`` sorted by line."""
    if len(source.encode("utf-8", "surrogatepass")) > MAX_SOURCE_BYTES:
        raise ValueError(f"{path}: source exceeds {MAX_SOURCE_BYTES} bytes")
    lines = source.splitlines()
    smells = _token_smells(source, path, lines)
    tree = parse(source)
    if tree is None:
        smells.append(
            _smell(
                SmellKind.SYNTAX_ERROR,
                path,
                1,
                "module does not parse as Python 3 (AST checks deferred)",
                Severity.HIGH,
                lines,
            )
        )
    else:
        smells.extend(_ast_smells(tree, path, lines))
    smells.sort(key=lambda s: (s.line, s.kind.value))
    return smells


def candidate_transforms(smells: list[Smell]) -> list[TransformKind]:
    """Distinct transforms that address at least one smell, in first-seen order."""
    seen: list[TransformKind] = []
    for smell in smells:
        fix = FIXES.get(smell.kind)
        if fix is not None and fix not in seen:
            seen.append(fix)
    return seen
