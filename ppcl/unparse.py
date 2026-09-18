"""Render PPCL AST nodes back to source text.

Used by the generator and the ``--normalize`` mode of the formatter. The
renumberer deliberately does *not* use this: it rewrites line-number tokens in
place so that everything else in a working program survives byte for byte.
"""

from __future__ import annotations

from . import spec
from .ast_nodes import (
    Assignment,
    BinOp,
    Comment,
    CommandCall,
    FuncCall,
    Gosub,
    Goto,
    If,
    MacroRef,
    Num,
    ParameterDecl,
    PriorityRef,
    Ref,
    Return,
    Sampled,
    TimeLit,
    UnaryOp,
    Unparsed,
)


def expr_to_text(node, parent_prec: int = 99) -> str:
    """Render an expression, adding parentheses only where they are needed."""
    if node is None:
        return ""

    if isinstance(node, Num):
        return node.raw
    if isinstance(node, TimeLit):
        return node.raw
    if isinstance(node, Ref):
        return '"%s"' % node.name if node.quoted else node.name
    if isinstance(node, PriorityRef):
        return node.name
    if isinstance(node, MacroRef):
        return node.name
    if isinstance(node, FuncCall):
        return "%s(%s)" % (node.name, expr_to_text(node.arg))
    if isinstance(node, UnaryOp):
        return "%s%s" % (node.op, expr_to_text(node.operand, 2))
    if isinstance(node, BinOp):
        prec = spec.PRECEDENCE.get(node.op, 99)
        left = expr_to_text(node.left, prec)
        # The right operand of a left-associative operator needs parentheses
        # when it binds equally loosely, or a-(b-c) would render as a-b-c.
        right = expr_to_text(node.right, prec - 1)
        text = "%s%s%s" % (left, node.op, right)
        if prec > parent_prec:
            return "(%s)" % text
        return text

    return str(node)


def stmt_to_text(stmt) -> str:
    """Render a statement without its line number."""
    if stmt is None:
        return ""

    if isinstance(stmt, Unparsed):
        return stmt.raw
    if isinstance(stmt, Comment):
        return ("C %s" % stmt.text) if stmt.text else "C"
    if isinstance(stmt, Return):
        return "RETURN"
    if isinstance(stmt, Goto):
        return "GOTO %d" % stmt.target
    if isinstance(stmt, Gosub):
        if stmt.args:
            args = ",".join(expr_to_text(a) for a in stmt.args)
            return "GOSUB %d %s" % (stmt.target, args)
        return "GOSUB %d" % stmt.target
    if isinstance(stmt, ParameterDecl):
        return "PARAMETER %s = %s" % (stmt.name, expr_to_text(stmt.value))
    if isinstance(stmt, Assignment):
        return "%s = %s" % (expr_to_text(stmt.target), expr_to_text(stmt.expr))
    if isinstance(stmt, Sampled):
        inner = stmt_to_text(stmt.statement)
        return "SAMPLE(%s) %s" % (expr_to_text(stmt.seconds), inner) if inner \
            else "SAMPLE(%s)" % expr_to_text(stmt.seconds)
    if isinstance(stmt, If):
        text = "IF(%s) THEN" % expr_to_text(stmt.cond)
        if stmt.then_stmt is not None:
            text += " %s" % stmt_to_text(stmt.then_stmt)
        if stmt.else_stmt is not None:
            text += " ELSE %s" % stmt_to_text(stmt.else_stmt)
        return text
    if isinstance(stmt, CommandCall):
        parts = []
        if stmt.priority is not None:
            parts.append(stmt.priority.name)
        parts.extend(expr_to_text(a) for a in stmt.args)
        return "%s(%s)" % (stmt.name, ",".join(parts))

    return str(stmt)


def line_to_text(line, pad: int = 5, sep: str = "\t") -> str:
    """Render a full program line, zero-padded like a panel export."""
    return "%s%s%s" % (str(line.number).zfill(pad), sep, stmt_to_text(line.stmt))
