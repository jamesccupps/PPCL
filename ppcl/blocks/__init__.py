"""Graphical block programming: draw the control, get the PPCL.

The three pieces are deliberately separate. :mod:`~ppcl.blocks.model` is plain
data shared by the canvas, the JSON file and the compiler.
:mod:`~ppcl.blocks.catalog` is the language of blocks -- what exists, what it
means, and what it emits. :mod:`~ppcl.blocks.compiler` decides what becomes a
statement and what stays an expression, which is the part that determines
whether the generated program looks hand-written or machine-generated.
"""

from .catalog import CATALOG, CATEGORIES, BlockError, BlockType, Param, Pin, catalog_payload
from .compiler import (LOCALS_PER_STATEMENT, MAX_LOCALS, Value,
                       compile_and_lint, compile_diagram, order_blocks)
from .model import Block, Diagram, Wire, dumps, from_dict, loads, to_dict

__all__ = [
    "CATALOG", "CATEGORIES", "BlockError", "BlockType", "Param", "Pin",
    "catalog_payload", "LOCALS_PER_STATEMENT", "MAX_LOCALS", "Value", "compile_and_lint",
    "compile_diagram", "order_blocks", "Block", "Diagram", "Wire",
    "dumps", "from_dict", "loads", "to_dict",
]
