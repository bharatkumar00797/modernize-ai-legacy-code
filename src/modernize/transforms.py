"""Allowlisted, pure source-to-source transforms.

Every transform is ``apply(source) -> new_source``: deterministic, side-effect free
and conservative. When a pattern is not a simple, provably mechanical case the
transform leaves it alone (returns the source unchanged) instead of guessing.
Behavior is then verified by the project's own tests before anything is kept.
"""

from __future__ import annotations

import ast
import re
import tokenize
from collections.abc import Callable
from dataclasses import dataclass

from .models import TransformKind
from .smells import (
    DEPRECATED_ATTRS,
    PY2_DICT_ITER,
    PY2_TEXT_NAMES,
    parse,
    py2_print_tokens,
    significant,
    tokens,
)

Edit = tuple[int, int, str]  # (start, end, replacement) as character offsets


class _Offsets:
    """Translate (line, column) positions from tokenize/ast into string offsets."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                self.starts.append(i + 1)

    def chars(self, line: int, col: int) -> int:
        return self.starts[line - 1] + col

    def bytes(self, line: int, col: int) -> int:
        """ast column offsets are UTF-8 byte offsets."""
        start = self.starts[line - 1]
        end = self.starts[line] if line < len(self.starts) else len(self.source)
        prefix = self.source[start:end].encode("utf-8")[:col]
        return start + len(prefix.decode("utf-8", "ignore"))

    def node(self, node: ast.expr | ast.stmt) -> tuple[int, int]:
        assert node.end_lineno is not None and node.end_col_offset is not None
        return (
            self.bytes(node.lineno, node.col_offset),
            self.bytes(node.end_lineno, node.end_col_offset),
        )

    def line_start(self, line: int) -> int:
        return self.starts[line - 1] if line <= len(self.starts) else len(self.source)


def apply_edits(source: str, edits: list[Edit]) -> str:
    """Splice non-overlapping edits into ``source`` (overlaps are dropped)."""
    out = source
    last_start = len(source) + 1
    for start, end, text in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if end > last_start:
            continue  # overlaps an edit that was already applied
        out = out[:start] + text + out[end:]
        last_start = start
    return out


# --------------------------------------------------------------------------- tokens


def _rename_tokens(source: str, mapping: dict[str, str], *, after_dot: bool) -> str:
    toks = tokens(source)
    if toks is None:
        return source
    sig = significant(toks)
    if not after_dot:
        # Refuse when the module defines/assigns one of the names itself.
        for i, tok in enumerate(sig):
            if tok.type == tokenize.NAME and tok.string in mapping:
                prev = sig[i - 1].string if i else ""
                nxt = sig[i + 1].string if i + 1 < len(sig) else ""
                if prev in {"def", "class", "import", "as"} or nxt == "=":
                    return source
    off = _Offsets(source)
    edits: list[Edit] = []
    for i, tok in enumerate(sig):
        if tok.type != tokenize.NAME or tok.string not in mapping:
            continue
        is_attr = i > 0 and sig[i - 1].type == tokenize.OP and sig[i - 1].string == "."
        if is_attr != after_dot:
            continue
        start, end = off.chars(*tok.start), off.chars(*tok.end)
        edits.append((start, end, mapping[tok.string]))
    return apply_edits(source, edits)


def xrange_to_range(source: str) -> str:
    return _rename_tokens(source, {"xrange": "range"}, after_dot=False)


def unicode_to_str(source: str) -> str:
    return _rename_tokens(source, PY2_TEXT_NAMES, after_dot=False)


def dict_iter_to_view(source: str) -> str:
    return _rename_tokens(source, PY2_DICT_ITER, after_dot=True)


def deprecated_api_aliases(source: str) -> str:
    toks = tokens(source)
    if toks is None:
        return source
    sig = significant(toks)
    off = _Offsets(source)
    edits: list[Edit] = []
    for i in range(len(sig) - 2):
        mod, dot, attr = sig[i], sig[i + 1], sig[i + 2]
        if i and sig[i - 1].string == ".":
            continue
        if dot.string == "." and (mod.string, attr.string) in DEPRECATED_ATTRS:
            new = DEPRECATED_ATTRS[(mod.string, attr.string)]
            edits.append((off.chars(*attr.start), off.chars(*attr.end), new))
    return apply_edits(source, edits)


def py2_print_to_function(source: str) -> str:
    toks = tokens(source)
    if toks is None:
        return source
    sig = significant(toks)
    off = _Offsets(source)
    edits: list[Edit] = []
    for i in py2_print_tokens(toks):
        # Collect the statement's tokens up to NEWLINE / ';' at depth 0.
        depth = 0
        body: list[tokenize.TokenInfo] = []
        for tok in sig[i + 1 :]:
            if tok.type in {tokenize.NEWLINE, tokenize.ENDMARKER}:
                break
            if tok.type == tokenize.OP:
                if tok.string in "([{":
                    depth += 1
                elif tok.string in ")]}":
                    depth -= 1
                elif tok.string == ";" and depth == 0:
                    break
            body.append(tok)
        if any(t.string == "`" for t in body):
            continue  # backtick repr: leave for a human
        start = off.chars(*sig[i].start)
        end = off.chars(*(body[-1].end if body else sig[i].end))
        if not body:
            edits.append((start, end, "print()"))
            continue
        file_target = ""
        if body[0].string == ">>":
            comma = _top_level_comma(body, 1)
            target_end = body[comma - 1].end if comma is not None else body[-1].end
            file_target = source[off.chars(*body[1].start) : off.chars(*target_end)].strip()
            body = body[comma + 1 :] if comma is not None else []
        trailing_comma = bool(body) and body[-1].string == "," and _depth_at_end_is_zero(body)
        if trailing_comma:
            body = body[:-1]
        args = source[off.chars(*body[0].start) : off.chars(*body[-1].end)].strip() if body else ""
        extra = []
        if trailing_comma:
            extra.append('end=" "')
        if file_target:
            extra.append(f"file={file_target}")
        call_args = ", ".join([a for a in [args, *extra] if a])
        edits.append((start, end, f"print({call_args})"))
    return apply_edits(source, edits)


def _top_level_comma(body: list[tokenize.TokenInfo], start: int) -> int | None:
    depth = 0
    for idx in range(start, len(body)):
        s = body[idx].string
        if body[idx].type == tokenize.OP:
            if s in "([{":
                depth += 1
            elif s in ")]}":
                depth -= 1
            elif s == "," and depth == 0:
                return idx
    return None


def _depth_at_end_is_zero(body: list[tokenize.TokenInfo]) -> bool:
    depth = 0
    for tok in body:
        if tok.type == tokenize.OP and tok.string in "([{":
            depth += 1
        elif tok.type == tokenize.OP and tok.string in ")]}":
            depth -= 1
    return depth == 0


# --------------------------------------------------------------------------- ast


def bare_except_to_exception(source: str) -> str:
    tree = parse(source)
    if tree is None:
        return source
    off = _Offsets(source)
    edits: list[Edit] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            pos = off.bytes(node.lineno, node.col_offset)
            if source.startswith("except", pos):
                edits.append((pos + 6, pos + 6, " Exception"))
    return apply_edits(source, edits)


def _indent_of(source: str, off: _Offsets, line: int) -> str:
    start = off.line_start(line)
    text = source[start:].split("\n", 1)[0]
    return text[: len(text) - len(text.lstrip())]


def mutable_default_to_none(source: str) -> str:
    tree = parse(source)
    if tree is None:
        return source
    off = _Offsets(source)
    edits: list[Edit] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        positional = [*node.args.posonlyargs, *node.args.args]
        pairs: list[tuple[ast.arg, ast.expr]] = list(
            zip(
                positional[len(positional) - len(node.args.defaults) :],
                node.args.defaults,
                strict=True,
            )
        )
        pairs += [
            (a, d) for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True) if d
        ]
        targets = [
            (arg, default)
            for arg, default in pairs
            if isinstance(default, ast.List | ast.Dict | ast.Set)
            and arg.annotation is None
            and default.lineno == default.end_lineno
        ]
        if not targets or node.body[0].lineno == node.lineno:
            continue  # nothing to do, or a one-line function body
        body = node.body
        has_doc = (
            isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        )
        anchor_stmt = body[1] if has_doc and len(body) > 1 else body[0]
        indent = _indent_of(source, off, body[0].lineno)
        if has_doc and len(body) == 1:
            assert body[0].end_lineno is not None
            insert_at = off.line_start(body[0].end_lineno + 1)
        else:
            if anchor_stmt.col_offset != len(indent.encode()):
                continue
            insert_at = off.line_start(anchor_stmt.lineno)
        guard = []
        for arg, default in targets:
            start, end = off.node(default)
            guard.append(
                f"{indent}if {arg.arg} is None:\n{indent}    {arg.arg} = {source[start:end]}\n"
            )
            edits.append((start, end, "None"))
        if insert_at == len(source) and not source.endswith("\n"):
            guard.insert(0, "\n")
        edits.append((insert_at, insert_at, "".join(guard)))
    return apply_edits(source, edits)


def _future_insert_point(tree: ast.Module, source: str, off: _Offsets) -> int:
    body = tree.body
    idx = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        idx = 1
    while (
        idx < len(body)
        and isinstance(body[idx], ast.ImportFrom)
        and getattr(body[idx], "module", None) == "__future__"
    ):
        idx += 1
    if idx == 0:
        # After any shebang / encoding / comment header lines.
        line = 1
        lines = source.split("\n")
        while line <= len(lines) and lines[line - 1].startswith("#"):
            line += 1
        return off.line_start(line)
    end_line = body[idx - 1].end_lineno or body[idx - 1].lineno
    return off.line_start(end_line + 1)


def add_future_annotations(source: str) -> str:
    tree = parse(source)
    if tree is None:
        return source
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(a.name == "annotations" for a in node.names)
        ):
            return source
    off = _Offsets(source)
    pos = _future_insert_point(tree, source, off)
    prefix = "" if pos == 0 or source[pos - 1] == "\n" else "\n"
    text = f"{prefix}from __future__ import annotations\n"
    if pos > 0 and tree.body and pos < len(source) and source[pos] != "\n":
        text += "\n"
    return source[:pos] + text + source[pos:]


def _binds_name(tree: ast.Module, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store):
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
            node.name == name
        ):
            return True
        if isinstance(node, ast.alias) and (node.asname or node.name) == name and node.name != name:
            return True
    return False


def eval_to_ast_literal(source: str) -> str:
    tree = parse(source)
    if tree is None or _binds_name(tree, "ast") or _binds_name(tree, "eval"):
        return source
    off = _Offsets(source)
    edits: list[Edit] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "eval"
            and len(node.args) == 1
            and not node.keywords
            and not isinstance(node.args[0], ast.Starred)
        ):
            start, end = off.node(node.func)
            edits.append((start, end, "ast.literal_eval"))
    if not edits:
        return source
    has_import = any(
        isinstance(n, ast.Import) and any(a.name == "ast" and a.asname is None for a in n.names)
        for n in tree.body
    )
    if not has_import:
        first_import = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.Import)
                or (isinstance(n, ast.ImportFrom) and n.module != "__future__")
            ),
            None,
        )
        if first_import is not None and first_import.col_offset == 0:
            pos = off.line_start(first_import.lineno)
            edits.append((pos, pos, "import ast\n"))
        else:
            pos = _future_insert_point(tree, source, off)
            edits.append((pos, pos, "import ast\n\n"))
    return apply_edits(source, edits)


def drop_object_base(source: str) -> str:
    tree = parse(source)
    if tree is None:
        return source
    off = _Offsets(source)
    edits: list[Edit] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ClassDef)
            and len(node.bases) == 1
            and isinstance(node.bases[0], ast.Name)
            and node.bases[0].id == "object"
            and not node.keywords
        ):
            start, end = off.node(node.bases[0])
            open_paren = source.rfind("(", 0, start)
            close_paren = source.find(")", end)
            if (
                open_paren != -1
                and close_paren != -1
                and source[open_paren + 1 : start].strip() == ""
                and source[end:close_paren].strip(" ,") == ""
            ):
                edits.append((open_paren, close_paren + 1, ""))
    return apply_edits(source, edits)


_SIMPLE_LITERAL = re.compile(r"""^[uU]?(?P<q>['"])(?P<body>(?:(?!(?P=q))[^\\\n{}])*)(?P=q)$""")
_SPEC = re.compile(r"%(?P<flags>[-+ 0]*)(?P<width>\d*)(?:\.(?P<prec>\d+))?(?P<conv>[sdifr%])")
_UNSAFE_EXPR = re.compile(r"""['"\\{}#:!=\n]|\blambda\b|\bawait\b|\byield\b""")


def _format_field(expr: str, flags: str, width: str, prec: str | None, conv: str) -> str | None:
    align = "<" if "-" in flags else ""
    sign = "+" if "+" in flags else (" " if " " in flags else "")
    zero = "0" if "0" in flags and not align else ""
    if conv == "s":
        if sign or zero:
            return None
        spec = (align or (">" if width else "")) + width + (f".{prec}" if prec else "")
        return f"{{{expr}:{spec}}}" if spec else f"{{{expr}}}"
    if conv == "r":
        if sign or zero or prec:
            return None
        spec = (align or (">" if width else "")) + width
        return f"{{{expr}!r:{spec}}}" if spec else f"{{{expr}!r}}"
    if conv in {"d", "i"}:
        if prec:
            return None
        return f"{{{expr}:{align}{sign}{zero}{width}d}}"
    # conv == "f"
    return f"{{{expr}:{align}{sign}{zero}{width}.{prec or '6'}f}}"


def _fstring_for(fmt: str, quote: str, exprs: list[str]) -> str | None:
    out: list[str] = []
    pos = 0
    values = iter(exprs)
    used = 0
    while pos < len(fmt):
        ch = fmt[pos]
        if ch != "%":
            out.append(ch)
            pos += 1
            continue
        match = _SPEC.match(fmt, pos)
        if match is None:
            return None  # %(name)s, %*d, %x, ... are left for a human
        pos = match.end()
        if match["conv"] == "%":
            if match.group(0) != "%%":
                return None
            out.append("%")
            continue
        expr = next(values, None)
        if expr is None:
            return None
        used += 1
        field = _format_field(expr, match["flags"], match["width"], match["prec"], match["conv"])
        if field is None:
            return None
        out.append(field)
    if used != len(exprs):
        return None
    return f"f{quote}{''.join(out)}{quote}"


def percent_format_to_fstring(source: str) -> str:
    tree = parse(source)
    if tree is None:
        return source
    off = _Offsets(source)
    edits: list[Edit] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
        ):
            continue
        lstart, lend = off.node(node.left)
        literal = _SIMPLE_LITERAL.match(source[lstart:lend])
        if literal is None:
            continue
        values = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        if isinstance(node.right, ast.Dict) or any(
            isinstance(v, ast.Starred | ast.Lambda | ast.NamedExpr) for v in values
        ):
            continue
        exprs: list[str] = []
        for value in values:
            vs, ve = off.node(value)
            text = source[vs:ve]
            if not text or _UNSAFE_EXPR.search(text):
                break
            exprs.append(text)
        else:
            fstring = _fstring_for(literal["body"], literal["q"], exprs)
            if fstring is not None:
                start, end = off.node(node)
                edits.append((start, end, fstring))
    return apply_edits(source, edits)


# --------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Transform:
    id: TransformKind
    title: str
    description: str
    risk: str  # what could change behavior - the reason tests gate every step
    apply: Callable[[str], str]
    needs_parse: bool


TRANSFORMS: dict[TransformKind, Transform] = {
    t.id: t
    for t in [
        Transform(
            TransformKind.PY2_PRINT_TO_FUNCTION,
            "print statement -> print()",
            "Rewrites Python 2 print statements (incl. >>file and trailing comma) as calls.",
            "Trailing-comma softspace semantics are approximated with end=' '.",
            py2_print_to_function,
            False,
        ),
        Transform(
            TransformKind.XRANGE_TO_RANGE,
            "xrange() -> range()",
            "Renames the removed builtin; skipped when the module defines xrange itself.",
            "range() is lazy in Python 3, so behavior is identical for iteration.",
            xrange_to_range,
            False,
        ),
        Transform(
            TransformKind.UNICODE_TO_STR,
            "unicode/basestring/unichr -> str/chr",
            "Renames Python 2 text builtins to their Python 3 equivalents.",
            "Code that distinguished bytes from text via str vs unicode may change.",
            unicode_to_str,
            False,
        ),
        Transform(
            TransformKind.DICT_ITER_TO_VIEW,
            ".iteritems() -> .items()",
            "Uses dict views instead of the removed iterator methods.",
            "Views are live; mutating the dict while iterating now raises.",
            dict_iter_to_view,
            False,
        ),
        Transform(
            TransformKind.BARE_EXCEPT_TO_EXCEPTION,
            "except: -> except Exception:",
            "Stops swallowing KeyboardInterrupt/SystemExit/GeneratorExit.",
            "Code that relied on catching BaseException will now propagate it.",
            bare_except_to_exception,
            True,
        ),
        Transform(
            TransformKind.MUTABLE_DEFAULT_TO_NONE,
            "mutable default -> None sentinel",
            "Replaces [] / {} / set() literal defaults with None plus an explicit guard.",
            "Callers relying on state shared between calls will see fresh objects.",
            mutable_default_to_none,
            True,
        ),
        Transform(
            TransformKind.EVAL_TO_AST_LITERAL,
            "eval() -> ast.literal_eval()",
            "Only literals (numbers, strings, lists, dicts, ...) are accepted, never code.",
            "Inputs that were expressions (e.g. '1 + x') now raise ValueError.",
            eval_to_ast_literal,
            True,
        ),
        Transform(
            TransformKind.PERCENT_FORMAT_TO_FSTRING,
            "'%s' % x -> f'{x}'",
            "Converts simple printf-style formatting with plain expressions to f-strings.",
            "%d truncates floats while {x:d} raises; a single tuple operand is unpacked by %.",
            percent_format_to_fstring,
            True,
        ),
        Transform(
            TransformKind.DEPRECATED_API_ALIASES,
            "removed stdlib aliases -> replacements",
            "time.clock -> time.perf_counter, os.getcwdu -> os.getcwd, string.letters -> ...",
            "perf_counter measures wall time rather than CPU time on POSIX.",
            deprecated_api_aliases,
            False,
        ),
        Transform(
            TransformKind.DROP_OBJECT_BASE,
            "class X(object) -> class X",
            "Drops the redundant explicit object base class.",
            "None in Python 3 (all classes are new-style).",
            drop_object_base,
            True,
        ),
        Transform(
            TransformKind.ADD_FUTURE_ANNOTATIONS,
            "add from __future__ import annotations",
            "Enables postponed evaluation of annotations (prerequisite for typing work).",
            "Code inspecting __annotations__ at runtime sees strings.",
            add_future_annotations,
            True,
        ),
    ]
}


def get_transform(transform_id: str) -> Transform | None:
    """Look up an allowlisted transform; unknown or non-rule ids return None."""
    try:
        kind = TransformKind(transform_id)
    except ValueError:
        return None
    return TRANSFORMS.get(kind)
