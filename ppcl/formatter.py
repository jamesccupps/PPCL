"""Formatting and renumbering.

Renumbering a PPCL program by hand is the classic way to break a working
sequence: every GOTO, GOSUB, ACT, DEACT, ENABLE, DISABL and ONPWRT that names a
line has to move with it. This module rewrites those references at the token
level, so everything else in the line -- spacing, quoting, comment text --
survives unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spec
from .analyzer import LINE_REFERENCING
from .ast_nodes import Line, Program
from .lexer import Tok, tokenize
from .unparse import line_to_text, stmt_to_text


@dataclass
class RenumberResult:
    """Outcome of a renumber operation."""

    text: str
    mapping: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    rewritten_references: int = 0


# --------------------------------------------------------------------------
# Finding line-number references inside a statement's token stream
# --------------------------------------------------------------------------


def _argument_tokens(tokens, open_paren: int):
    """Split a call's arguments at depth one.

    Returns ``(args, close_index)`` where ``args`` is a list of token lists,
    one per comma-separated argument, and ``close_index`` is the position of
    the matching ``)``. Returns ``(None, open_paren)`` if it never closes.

    Commands like PDL interleave line numbers with values that happen to be
    small integers, so knowing *which argument* a number sits in is the only
    way to rewrite the right ones.
    """
    n = len(tokens)
    depth = 0
    args = [[]]
    k = open_paren
    while k < n:
        kind = tokens[k].kind
        if kind is Tok.LPAREN:
            depth += 1
            if depth == 1:
                k += 1
                continue
        elif kind is Tok.RPAREN:
            depth -= 1
            if depth == 0:
                return args, k
        elif kind is Tok.COMMA and depth == 1:
            args.append([])
            k += 1
            continue
        args[-1].append(tokens[k])
        k += 1
    return None, open_paren


def _lone_number(arg):
    """The token, if this argument is a single numeric literal."""
    if len(arg) == 1 and arg[0].kind is Tok.NUMBER:
        return arg[0]
    return None


def _pdl_group_bounds(args):
    """The group start/end tokens in a PDL call.

    ``PDL`` delimits each of its four priority groups by the *line numbers* of
    the PDLDAT statements that define it, so renumbering has to rewrite them.
    The trailing shed-mode flag in each group is 0 or 1, and 1 is a legal line
    number, so they cannot simply be rewritten along with everything else.

    Two forms exist and the first argument tells them apart -- the logical
    form leads with an integer meter area number, the physical form leads with
    the Total kW point:

        logical  PDL(area, totkw, target, g1s, g1e, sh1, ... g4s, g4e, sh4)
        physical PDL(totkw, target, g1s, g1e, ... g4s, g4e)
    """
    if len(args) < 4:
        return []
    if _lone_number(args[0]) is not None:
        first, stride = 3, 3          # logical: area, totkw, target, then triples
    else:
        first, stride = 2, 2          # physical: totkw, target, then pairs
    out = []
    for start in range(first, len(args) - 1, stride):
        for arg in (args[start], args[start + 1]):
            tok = _lone_number(arg)
            if tok is not None:
                out.append(tok)
    return out


def line_reference_tokens(body: str):
    """Return the tokens in ``body`` that denote PPCL line numbers.

    Scanning tokens rather than the AST means references nested inside an
    IF/THEN/ELSE or behind a SAMPLE are found with no special cases.
    """
    try:
        tokens = tokenize(body)
    except Exception:
        return []

    refs = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        if t.kind is Tok.IDENT:
            word = t.upper
            if word in ("GOTO", "GOSUB"):
                j = i + 1
                if j < n and tokens[j].kind is Tok.LPAREN:
                    j += 1
                if j < n and tokens[j].kind is Tok.NUMBER:
                    refs.append(tokens[j])
                    i = j + 1
                    continue
            elif word in LINE_REFERENCING or word in ("LSQ2", "PDL"):
                j = i + 1
                if j < n and tokens[j].kind is Tok.LPAREN:
                    args, k = _argument_tokens(tokens, j)
                    if args is None:
                        i += 1
                        continue
                    if word == "LSQ2":
                        # LSQ2(execution,pt1,..,pt6,startline#,endline#): only
                        # the final two arguments are line references. The
                        # leading execution parameter is a time in minutes.
                        for arg in args[-2:]:
                            tok = _lone_number(arg)
                            if tok is not None:
                                refs.append(tok)
                    elif word == "PDL":
                        refs.extend(_pdl_group_bounds(args))
                    else:
                        for arg in args:
                            refs.extend(tok for tok in arg
                                        if tok.kind is Tok.NUMBER)
                    i = k + 1
                    continue
        i += 1
    return refs


def rewrite_references(body: str, mapping: dict):
    """Apply ``mapping`` (old line -> new line) to a statement body.

    Returns ``(new_body, count, unresolved)``.
    """
    refs = line_reference_tokens(body)
    if not refs:
        return body, 0, []

    unresolved = []
    edits = []
    for tok in refs:
        try:
            old = int(float(tok.text))
        except ValueError:
            continue
        if old in mapping:
            edits.append((tok.col, len(tok.text), str(mapping[old])))
        else:
            unresolved.append(old)

    # Apply right to left so earlier column offsets stay valid.
    out = body
    for col, length, replacement in sorted(edits, reverse=True):
        out = out[:col] + replacement + out[col + length :]
    return out, len(edits), unresolved


# --------------------------------------------------------------------------
# Renumbering
# --------------------------------------------------------------------------


def find_duplicates(program: Program) -> dict:
    """Map line number -> the Line objects sharing it, for duplicates only."""
    groups = {}
    for ln in program.lines:
        groups.setdefault(ln.number, []).append(ln)
    return {num: g for num, g in groups.items() if len(g) > 1}


def plan_renumber(program: Program, start: int = 10, step: int = 10,
                  preserve_blocks: bool = False, block_size: int = 1000,
                  split_duplicates: bool = False):
    """Build the old-to-new line-number plan.

    Returns ``(assignments, mapping)`` where ``assignments`` is a list of
    ``(Line, new_number)`` in output order and ``mapping`` resolves an old line
    number to the new number a reference to it should use.

    With ``preserve_blocks`` the thousands-block layout PPCL programmers use
    for subroutines (main loop at 1000, first subroutine at 2000, and so on) is
    kept: lines are renumbered within their existing block rather than
    collapsed into one run.
    """
    assignments = []
    mapping = {}
    if not program.lines:
        return assignments, mapping

    def assign(lines, first):
        n = first
        for ln in lines:
            assignments.append((ln, n))
            # A reference to an old number resolves to the FIRST line that
            # carried it, which is the one the panel would have kept.
            mapping.setdefault(ln.number, n)
            n += step

    ordered = list(program.lines)
    if not split_duplicates:
        seen = set()
        deduped = []
        for ln in ordered:
            if ln.number in seen:
                continue
            seen.add(ln.number)
            deduped.append(ln)
        ordered = deduped

    if not preserve_blocks:
        assign(ordered, start)
        return assignments, mapping

    per_block = {}
    for ln in ordered:
        per_block.setdefault(ln.number // block_size, []).append(ln)
    for block in sorted(per_block):
        assign(per_block[block], block * block_size + (start if block == 0 else step))
    return assignments, mapping


def renumber(program: Program, start: int = 10, step: int = 10,
             preserve_blocks: bool = False, block_size: int = 1000,
             pad: int = 5, sep: str = "\t",
             split_duplicates: bool = False,
             allow_duplicates: bool = False) -> RenumberResult:
    """Renumber a program and rewrite every line reference to match.

    Duplicate line numbers make renumbering ambiguous: the panel keeps only one
    of them, but the file contains both, and there is no way to tell which the
    author meant. Rather than guess, this refuses by default. Pass
    ``split_duplicates`` to give every copy its own number (which preserves all
    the code but changes what the panel executes) or ``allow_duplicates`` to
    keep only the first of each (which matches the panel and discards the rest).
    """
    result = RenumberResult(text="")

    duplicates = find_duplicates(program)
    if duplicates and not (split_duplicates or allow_duplicates):
        detail = "; ".join(
            "line %d appears %d times" % (num, len(g))
            for num, g in sorted(duplicates.items())
        )
        result.warnings.append(
            "refusing to renumber: %s. Renumbering would have to either drop "
            "code or change what the panel runs. Resolve the duplicates first, "
            "or choose --split-duplicates (keep every line, each gets its own "
            "number) or --allow-duplicates (keep only the first of each, "
            "matching what the panel actually executes)." % detail
        )
        return result

    assignments, mapping = plan_renumber(
        program, start=start, step=step,
        preserve_blocks=preserve_blocks, block_size=block_size,
        split_duplicates=split_duplicates,
    )
    result.mapping = mapping

    over = [new for _, new in assignments if new > spec.LINE_MAX]
    if over:
        result.warnings.append(
            "renumbering would push %d line(s) past the %d maximum; "
            "use a smaller step or --preserve-blocks"
            % (len(over), spec.LINE_MAX)
        )
        return result

    if duplicates and split_duplicates:
        result.warnings.append(
            "%d duplicated line number(s) were split so every line survives. "
            "The panel previously executed only one copy of each, so this "
            "program now does MORE than the one in the field. Review lines: %s"
            % (len(duplicates), ", ".join(str(n) for n in sorted(duplicates)))
        )
    if duplicates and allow_duplicates:
        dropped = sum(len(g) - 1 for g in duplicates.values())
        result.warnings.append(
            "%d duplicate line(s) were discarded, keeping the first of each. "
            "This matches what the panel runs today." % dropped
        )

    out = [stmt_to_text(d) for d in program.directives]
    for ln, new_number in assignments:
        body, count, unresolved = rewrite_references(ln.body, mapping)
        result.rewritten_references += count
        for target in unresolved:
            result.warnings.append(
                "line %d references line %d, which does not exist; the "
                "reference was left unchanged" % (ln.number, target)
            )
        out.append("%s%s%s" % (str(new_number).zfill(pad), sep, body))

    result.text = "\n".join(out) + "\n"
    return result


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def format_text(program: Program, pad: int = 5, sep: str = "\t",
                sort: bool = True, normalize: bool = False) -> str:
    """Render a program as canonical text.

    ``normalize`` re-renders each statement from the AST, which regularises
    spacing and quoting. Without it the original body text is preserved and
    only the line number is reformatted.
    """
    lines = list(program.lines)
    if sort:
        lines.sort(key=lambda ln: (ln.number, ln.source_line))

    out = [stmt_to_text(d) for d in program.directives]
    for ln in lines:
        body = stmt_to_text(ln.stmt) if normalize else ln.body
        out.append("%s%s%s" % (str(ln.number).zfill(pad), sep, body))
    return "\n".join(out) + "\n"


def insert_line(program: Program, number: int, body: str):
    """Return the text of ``program`` with a new line inserted.

    Raises ValueError if the line number is already taken, since silently
    overwriting a line is exactly the failure mode PPCL editing tools should
    not have.
    """
    if any(ln.number == number for ln in program.lines):
        raise ValueError("line %d already exists" % number)
    if not (spec.LINE_MIN <= number <= spec.LINE_MAX):
        raise ValueError(
            "line %d is outside the legal range %d-%d"
            % (number, spec.LINE_MIN, spec.LINE_MAX)
        )

    from .parser import parse_statement_text

    stmt = parse_statement_text(body) if body.strip() else None
    new = Line(number, stmt, "%d\t%s" % (number, body), 0, body=body)
    lines = sorted(program.lines + [new], key=lambda ln: ln.number)
    clone = Program(lines=lines, directives=program.directives, name=program.name)
    return format_text(clone)


def next_free_number(program: Program, after: int, step: int = 10):
    """Find a free line number after ``after``, respecting the existing gaps."""
    taken = {ln.number for ln in program.lines}
    candidate = after + step
    following = sorted(n for n in taken if n > after)
    ceiling = following[0] if following else spec.LINE_MAX
    if candidate < ceiling and candidate not in taken:
        return candidate
    # Fall back to the midpoint of the gap.
    candidate = after + max(1, (ceiling - after) // 2)
    while candidate in taken and candidate < ceiling:
        candidate += 1
    return candidate if candidate < ceiling else None
