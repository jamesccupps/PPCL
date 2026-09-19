"""Source transforms: the edits that are tedious by hand and exact by machine.

These are the operations the existing PPCL tooling offers -- the Sublime Text
package's DEFINE toggling, separator swapping and block duplication, and the
Desigo CC editor's enable/disable -- implemented against a real parse rather
than against regular expressions, so a point name that happens to appear
inside a comment or a string is treated correctly.

Everything here works at the token level and rebuilds line bodies verbatim
where it is not changing them, for the same reason
:func:`ppcl.formatter.rewrite_references` does: an engineer comparing before
and after must see only the change they asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lexer
from . import parser as ppcl_parser
from .lexer import Tok

#: Text a disabled line carries so the state survives a save. The panel has a
#: real disable flag; a text file has nowhere to put one, so the workbench
#: marks it in a comment that the parser already ignores and that Desigo will
#: import as an ordinary comment rather than as running code.
DISABLED_PREFIX = "C [DISABLED] "


@dataclass
class TransformResult:
    """What a transform did, so the UI can report it honestly."""

    text: str
    changed: int = 0
    notes: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# --------------------------------------------------------------------------
# DEFINE abbreviations
# --------------------------------------------------------------------------


def find_defines(text):
    """``{abbrev: expansion}`` for every DEFINE in the program."""
    out = {}
    program = ppcl_parser.parse(text)
    for line in program.lines:
        if line.is_comment or not line.body:
            continue
        match = re.match(
            r"\s*DEFINE\s*\(\s*([^,\s]+)\s*,\s*(.+?)\s*\)\s*$",
            line.body, re.IGNORECASE,
        )
        if match:
            out[match.group(1).strip().strip('"')] = (
                match.group(2).strip().strip('"')
            )
    return out


def expand_defines(text):
    """Replace every ``%ABBREV%`` with the text DEFINE gives it.

    Useful before searching for a point name, and before handing a program to
    someone without the DEFINE lines in front of them.
    """
    defines = find_defines(text)
    if not defines:
        return TransformResult(text, 0, warnings=[
            "there are no DEFINE statements in this program"
        ])
    count = 0
    lines = []
    for raw in text.split("\n"):
        replaced = raw
        for abbrev, expansion in defines.items():
            token = "%%%s%%" % abbrev
            if token in replaced:
                count += replaced.count(token)
                replaced = replaced.replace(token, expansion)
        lines.append(replaced)
    return TransformResult(
        "\n".join(lines), count,
        notes=["expanded %d reference(s) using %d DEFINE(s): %s"
               % (count, len(defines), ", ".join(sorted(defines)))],
    )


def collapse_defines(text):
    """The inverse: put the ``%ABBREV%`` shorthand back.

    Longest expansion first, so a DEFINE whose text contains another one's
    does not leave a half-substituted name behind.
    """
    defines = find_defines(text)
    if not defines:
        return TransformResult(text, 0, warnings=[
            "there are no DEFINE statements in this program"
        ])
    ordered = sorted(defines.items(), key=lambda kv: -len(kv[1]))
    count = 0
    lines = []
    for raw in text.split("\n"):
        if re.search(r"\bDEFINE\s*\(", raw, re.IGNORECASE):
            lines.append(raw)
            continue
        replaced = raw
        for abbrev, expansion in ordered:
            if expansion and expansion in replaced:
                count += replaced.count(expansion)
                replaced = replaced.replace(expansion, "%%%s%%" % abbrev)
        lines.append(replaced)
    return TransformResult(
        "\n".join(lines), count,
        notes=["collapsed %d reference(s) into %d DEFINE abbreviation(s)"
               % (count, len(defines))],
    )


# --------------------------------------------------------------------------
# Point-name separators
# --------------------------------------------------------------------------


def swap_separators(text, to="."):
    """Swap ``.`` and ``_`` inside point names, and nowhere else.

    Insight and Desigo CC disagree about which separator a hierarchical point
    name uses, so a program moved between them needs this. It runs on tokens,
    so a decimal point in ``80.0`` and a dotted operator like ``.AND.`` are
    untouched -- which is exactly the case a search-and-replace gets wrong.
    """
    if to not in (".", "_"):
        raise ValueError("separator must be '.' or '_'")
    frm = "_" if to == "." else "."
    out_lines = []
    count = 0
    for raw in text.split("\n"):
        head, body = _split_line(raw)
        if body is None or _is_comment(body):
            out_lines.append(raw)
            continue
        edits = []
        for token in lexer.tokenize(body):
            if token.kind not in (Tok.IDENT, Tok.QUOTED):
                continue
            swapped = token.text.replace(frm, to)
            if swapped == token.text:
                continue
            count += 1
            edits.append((token.col, _span(token), _requote(token, swapped)))
        out_lines.append((head + _apply_edits(body, edits)) if edits else raw)
    return TransformResult(
        "\n".join(out_lines), count,
        notes=["changed %d point name(s) to use '%s'" % (count, to)],
    )


def _split_line(raw):
    match = re.match(r"^(\s*\d+[ \t]+)(.*)$", raw)
    if not match:
        return raw, None
    return match.group(1), match.group(2)


def _is_comment(body):
    return bool(re.match(r"^[Cc](\s|$)", body))


def _span(token):
    """How many source characters a token occupies.

    A quoted token's ``text`` is the name without its quotes, so its span is
    two characters wider. Getting this wrong eats the opening quote and
    duplicates the last character -- exactly the kind of silent corruption a
    source transform must never produce.
    """
    return len(token.text) + (2 if token.kind is Tok.QUOTED else 0)


def _requote(token, replacement):
    """Put a replacement back in the form the original token had."""
    return '"%s"' % replacement if token.kind is Tok.QUOTED else replacement


def _apply_edits(body, edits):
    """Apply ``(col, length, replacement)`` edits, leaving the rest verbatim."""
    out = []
    cursor = 0
    for col, length, replacement in sorted(edits):
        if col < cursor:
            continue  # overlapping edits cannot both be right; keep the first
        out.append(body[cursor:col])
        out.append(replacement)
        cursor = col + length
    out.append(body[cursor:])
    return "".join(out)


# --------------------------------------------------------------------------
# Enable / disable statements
# --------------------------------------------------------------------------


def set_disabled(text, line_numbers, disabled=True):
    """Comment out, or restore, the given program lines.

    The Desigo CC editor disables a statement at the panel, where there is a
    real flag for it. A text file has nowhere to keep that, so a disabled line
    becomes a comment carrying a marker. It is still a comment to every other
    tool, including the panel compiler, which is the safe direction to be
    wrong in.
    """
    wanted = {int(n) for n in line_numbers}
    if not wanted:
        return TransformResult(text, 0, warnings=["no lines were selected"])
    out = []
    count = 0
    for raw in text.split("\n"):
        head, body = _split_line(raw)
        if body is None:
            out.append(raw)
            continue
        number = int(head.strip())
        if number not in wanted:
            out.append(raw)
            continue
        already = body.startswith(DISABLED_PREFIX)
        if disabled and not already:
            out.append(head + DISABLED_PREFIX + body)
            count += 1
        elif not disabled and already:
            out.append(head + body[len(DISABLED_PREFIX):])
            count += 1
        else:
            out.append(raw)
    verb = "disabled" if disabled else "enabled"
    return TransformResult(
        "\n".join(out), count,
        notes=["%s %d line(s)" % (verb, count)],
        warnings=(
            ["a disabled line is written as a comment, so loading this "
             "program to a panel removes the statement rather than "
             "disabling it. Re-enable before loading."]
            if disabled and count else []
        ),
    )


def disabled_lines(text):
    """Line numbers currently marked disabled."""
    out = []
    for raw in text.split("\n"):
        head, body = _split_line(raw)
        if body is not None and body.startswith(DISABLED_PREFIX):
            out.append(int(head.strip()))
    return out


# --------------------------------------------------------------------------
# Cloning a block of code
# --------------------------------------------------------------------------


def clone_lines(text, first, last, renames, start=None, step=None):
    """Copy a range of lines, renaming points as it goes.

    This is the manual's own advice made mechanical: "When possible, reuse
    blocks of program code in other devices that require the same control...
    Since each point must have a unique point name, you must rename the points
    in the reused program code."

    Renaming is by whole token, so ``SFAN`` in ``AHU1.SFAN`` is not touched
    unless the rename names the whole reference. Line references inside the
    copied range are rewritten to point at the copies; references out of the
    range are left pointing where they did.
    """
    program = ppcl_parser.parse(text)
    numbers = [ln.number for ln in program.lines]
    if not numbers:
        return TransformResult(text, 0, warnings=["the program is empty"])
    first, last = int(first), int(last)
    if first > last:
        first, last = last, first
    block = [ln for ln in program.lines if first <= ln.number <= last]
    if not block:
        return TransformResult(
            text, 0,
            warnings=["no lines between %d and %d" % (first, last)],
        )

    step = int(step or _guess_step(numbers))
    start = int(start or (max(numbers) + step))
    mapping = {
        ln.number: start + i * step for i, ln in enumerate(block)
    }
    clash = sorted(set(mapping.values()) & set(numbers))
    if clash:
        return TransformResult(
            text, 0,
            warnings=[
                "the copy would land on existing line(s) %s. Choose a "
                "different starting line number."
                % ", ".join(str(n) for n in clash[:8])
            ],
        )

    lookup = {k.upper(): v for k, v in (renames or {}).items()}
    source_lines = text.split("\n")
    by_number = {}
    for raw in source_lines:
        head, body = _split_line(raw)
        if body is not None:
            by_number[int(head.strip())] = (head, body)

    copied = []
    renamed = 0
    oip_copied = False
    for ln in block:
        head, body = by_number.get(ln.number, (None, None))
        if body is None:
            continue
        if _is_comment(body):
            copied.append("%05d\t%s" % (mapping[ln.number], body))
            continue
        # A PXC.A disabled line carries its state in the text, as "# " at
        # the front. The lexer has no business seeing that -- it is not
        # PPCL -- so it is held aside and put back, and the statement behind
        # it is renamed like any other. Cloning a block containing one used
        # to raise LexError, which is the wrong answer to a line the panel
        # itself keeps and runs the moment somebody deletes two characters.
        prefix = ""
        if body.startswith("# "):
            prefix, body = "# ", body[2:]
        edits = []
        for token in lexer.tokenize(body):
            if (token.kind is Tok.QUOTED and "/" in token.text
                    and _OIP_BODY.search(body)):
                # A keystroke sequence: rename its components, not the token.
                oip_copied = True
                seq, hits = _rename_in_keystrokes(token.text, lookup)
                if hits:
                    renamed += hits
                    edits.append((token.col, _span(token),
                                  _requote(token, seq)))
            elif token.kind in (Tok.IDENT, Tok.QUOTED):
                replacement = lookup.get(token.text.upper())
                if replacement:
                    renamed += 1
                    edits.append((token.col, _span(token),
                                  _requote(token, replacement)))
            elif token.kind is Tok.NUMBER and _looks_like_reference(body):
                try:
                    target = int(float(token.text))
                except ValueError:
                    continue
                if target in mapping:
                    edits.append(
                        (token.col, _span(token), str(mapping[target]))
                    )
        copied.append(
            "%05d\t%s%s" % (mapping[ln.number], prefix,
                                _apply_edits(body, edits))
        )

    warnings = []
    if oip_copied:
        warnings.append(
            "an OIP statement was copied. Components of its keystroke "
            "sequence that matched a rename exactly have been renamed, but a "
            "sequence can name a point in a form no rename map will match -- "
            "typed across menu levels, or abbreviated. Read every copied OIP "
            "sequence by hand before loading this."
        )
    if renames and not renamed:
        warnings.append(
            "none of the renames matched anything in the copied lines, so the "
            "copy references the same points as the original"
        )
    elif not renames:
        warnings.append(
            "no renames were given, so the copy commands the same points as "
            "the original. Two statements commanding one point fight."
        )
    return TransformResult(
        "\n".join(source_lines + [""] + copied),
        len(copied),
        notes=["copied %d line(s) to %d-%d, renaming %d reference(s)"
               % (len(copied), min(mapping.values()), max(mapping.values()),
                  renamed)],
        warnings=warnings,
    )


_OIP_BODY = re.compile(r"\bOIP\s*\(", re.I)


def _rename_in_keystrokes(seq, lookup):
    """Apply renames to the components of an OIP keystroke sequence.

    An OIP sequence is one quoted token holding what an operator would type,
    with ``/`` per menu level -- so a point name inside it is a component, not
    the token, and a whole-token rename can never match it. Siemens hit the
    same wall and stopped: "Point names in comments or OIP statements are not
    modified and must be modified manually" (Insight Database Conversion help,
    PPCL Conversion Guidelines).

    Renaming an exact component is safe and is done. Anything else is left,
    and the caller warns -- a sequence can name a point in a form no rename
    map will match, typed across menu levels or abbreviated.
    """
    parts = seq.split("/")
    hits = 0
    for i, part in enumerate(parts):
        replacement = lookup.get(part.upper())
        if replacement:
            parts[i] = replacement
            hits += 1
    return "/".join(parts), hits


def _looks_like_reference(body):
    return bool(
        re.search(r"\b(GOTO|GOSUB|ACT|DEACT|ENABLE|DISABL|ONPWRT)\b",
                  body, re.IGNORECASE)
    )


def _guess_step(numbers):
    gaps = [b - a for a, b in zip(numbers, numbers[1:]) if b > a]
    return min(gaps) if gaps else 10


# --------------------------------------------------------------------------
# Comment toggling
# --------------------------------------------------------------------------


def toggle_comments(text, line_numbers):
    """Comment or uncomment whole statements, deciding by majority."""
    wanted = {int(n) for n in line_numbers}
    if not wanted:
        return TransformResult(text, 0, warnings=["no lines were selected"])
    rows = []
    for raw in text.split("\n"):
        head, body = _split_line(raw)
        rows.append((raw, head, body))
    selected = [b for _, h, b in rows if b is not None
                and int(h.strip()) in wanted]
    if not selected:
        return TransformResult(text, 0, warnings=["no program lines selected"])
    commenting = not all(_is_comment(b) for b in selected)

    out = []
    count = 0
    for raw, head, body in rows:
        if body is None or int(head.strip()) not in wanted:
            out.append(raw)
            continue
        if commenting and not _is_comment(body):
            out.append(head + "C " + body)
            count += 1
        elif not commenting and _is_comment(body):
            out.append(head + re.sub(r"^[Cc]\s?", "", body))
            count += 1
        else:
            out.append(raw)
    return TransformResult(
        "\n".join(out), count,
        notes=["%s %d line(s)"
               % ("commented" if commenting else "uncommented", count)],
    )
