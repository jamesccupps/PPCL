"""The block diagram: blocks, wires, and the canvas they sit on.

This is the data model for graphical programming -- the "wire the blocks
together" style familiar from Trane's Tracer Graphical Programming and from
every other vendor's function-block editor. It is deliberately plain data so
that the canvas in the browser, the JSON on disk and the compiler all agree on
one shape.

Like the sequence document, compilation is **one way**. A diagram compiles to
PPCL; hand-written PPCL does not lift back into a diagram, because the GOTO
structure of a real program carries intent no block graph can represent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Block:
    """One block on the canvas.

    ``id`` is stable for the life of the diagram and is what wires reference.
    ``label`` is the engineer's name for this instance and, when set, becomes
    the name of the local variable holding the block's output, so a value can
    be found at the panel by the name it has on the drawing.
    """

    id: str
    type: str
    x: float = 0.0
    y: float = 0.0
    label: str = ""
    note: str = ""
    params: dict = field(default_factory=dict)

    def param(self, name, default=None):
        value = self.params.get(name)
        return default if value is None or value == "" else value


@dataclass
class Wire:
    """A connection from one block's output pin to another's input pin.

    An output may feed any number of inputs; an input accepts exactly one
    wire. That asymmetry is what makes the graph unambiguous.
    """

    src: str          # block id
    src_pin: str
    dst: str          # block id
    dst_pin: str

    @property
    def key(self):
        return (self.dst, self.dst_pin)


@dataclass
class Diagram:
    """A complete block program."""

    name: str = "New diagram"
    equipment: str = ""
    author: str = ""
    description: str = ""
    version: str = "1.0"

    blocks: list = field(default_factory=list)
    wires: list = field(default_factory=list)

    start_line: int = 10
    line_step: int = 10

    # -- lookup ------------------------------------------------------------

    def block(self, block_id: str):
        for b in self.blocks:
            if b.id == block_id:
                return b
        return None

    def inputs_of(self, block_id: str) -> dict:
        """``{pin: Wire}`` for every wire landing on this block."""
        return {w.dst_pin: w for w in self.wires if w.dst == block_id}

    def fanout(self, block_id: str, pin: str) -> int:
        return sum(
            1 for w in self.wires if w.src == block_id and w.src_pin == pin
        )

    def next_id(self, prefix: str = "b") -> str:
        n = 1
        used = {b.id for b in self.blocks}
        while "%s%d" % (prefix, n) in used:
            n += 1
        return "%s%d" % (prefix, n)

    # -- editing -----------------------------------------------------------

    def add(self, block: Block) -> Block:
        if self.block(block.id) is not None:
            raise ValueError("a block with id %r already exists" % block.id)
        self.blocks.append(block)
        return block

    def remove(self, block_id: str) -> None:
        self.blocks = [b for b in self.blocks if b.id != block_id]
        self.wires = [
            w for w in self.wires if w.src != block_id and w.dst != block_id
        ]

    def connect(self, src, src_pin, dst, dst_pin) -> Wire:
        """Wire an output to an input, replacing whatever fed that input."""
        self.wires = [
            w for w in self.wires
            if not (w.dst == dst and w.dst_pin == dst_pin)
        ]
        wire = Wire(src, src_pin, dst, dst_pin)
        self.wires.append(wire)
        return wire

    def disconnect(self, dst, dst_pin) -> None:
        self.wires = [
            w for w in self.wires
            if not (w.dst == dst and w.dst_pin == dst_pin)
        ]


# --------------------------------------------------------------------------
# JSON round trip
# --------------------------------------------------------------------------


def to_dict(dia: Diagram) -> dict:
    return {
        "name": dia.name,
        "equipment": dia.equipment,
        "author": dia.author,
        "description": dia.description,
        "version": dia.version,
        "start_line": dia.start_line,
        "line_step": dia.line_step,
        "blocks": [
            {
                "id": b.id, "type": b.type, "x": b.x, "y": b.y,
                "label": b.label, "note": b.note, "params": dict(b.params),
            }
            for b in dia.blocks
        ],
        "wires": [
            {"src": w.src, "src_pin": w.src_pin,
             "dst": w.dst, "dst_pin": w.dst_pin}
            for w in dia.wires
        ],
    }


def from_dict(data: dict) -> Diagram:
    if not isinstance(data, dict):
        raise ValueError("a diagram must be a JSON object")
    dia = Diagram(
        name=data.get("name", "New diagram"),
        equipment=data.get("equipment", ""),
        author=data.get("author", ""),
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        start_line=int(data.get("start_line", 10)),
        line_step=int(data.get("line_step", 10)),
    )
    for raw in data.get("blocks", []):
        dia.blocks.append(
            Block(
                id=str(raw["id"]),
                type=str(raw["type"]),
                x=float(raw.get("x", 0)),
                y=float(raw.get("y", 0)),
                label=str(raw.get("label", "")),
                note=str(raw.get("note", "")),
                params=dict(raw.get("params") or {}),
            )
        )
    for raw in data.get("wires", []):
        dia.wires.append(
            Wire(
                src=str(raw["src"]), src_pin=str(raw["src_pin"]),
                dst=str(raw["dst"]), dst_pin=str(raw["dst_pin"]),
            )
        )
    return dia


def dumps(dia: Diagram) -> str:
    return json.dumps(to_dict(dia), indent=2)


def loads(text: str) -> Diagram:
    return from_dict(json.loads(text))
