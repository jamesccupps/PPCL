"""The command line's file writers: fmt, renumber, transform, blocks, seq.

Before these, nothing tested a CLI writer, and every one of them could
damage the file it was pointed at: a Windows-1252 degree sign became U+FFFD
in the file and in its backup, a UTF-8 byte-order mark cost the first line,
a line with no number vanished, and a folder run of fmt -i rewrote a README
to an empty line -- all with exit code 0.
"""

import json
import os

import pytest

from ppcl import cli

#: How Desigo and Insight export: CRLF, and Windows-1252 in comments.
EXPORT = b"00010\tC SUPPLY 55\xb0F\r\n00020\tON(SFAN)\r\n00030\tGOTO 10\r\n"


def _file(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


@pytest.mark.parametrize("command", [["fmt", "-i"], ["renumber", "-i"]])
def test_a_desigo_export_keeps_its_bytes(tmp_path, command):
    path = _file(tmp_path, "ahu.pcl", EXPORT)
    assert cli.main(command + [path]) == 0
    out = _bytes(path)
    assert b"55\xb0F" in out
    assert b"\r\n" in out and b"\n0" not in out.replace(b"\r\n", b"")
    # The backup is on by default, and is the original byte for byte.
    assert _bytes(path + ".bak") == EXPORT


def test_no_backup_means_none(tmp_path):
    path = _file(tmp_path, "ahu.pcl", EXPORT)
    assert cli.main(["fmt", "-i", "--no-backup", path]) == 0
    assert not os.path.exists(path + ".bak")


def test_a_byte_order_mark_keeps_line_one_and_itself(tmp_path):
    data = b"\xef\xbb\xbf00010\tONPWRT(20)\r\n00020\tGOTO 10\r\n"
    path = _file(tmp_path, "bom.pcl", data)
    assert cli.main(["fmt", "-i", path]) == 0
    out = _bytes(path)
    assert out.startswith(b"\xef\xbb\xbf00010")
    assert b"ONPWRT(20)" in out


@pytest.mark.parametrize("command", [["fmt", "-i"], ["renumber", "-i"]])
def test_a_line_without_a_number_is_refused_not_dropped(tmp_path, command):
    data = b"PROGRAM NAME: AHU1\r\n00010\tON(SFAN)\r\n00020\tGOTO 10\r\n"
    path = _file(tmp_path, "header.pcl", data)
    assert cli.main(command + [path]) == 2
    assert _bytes(path) == data
    assert not os.path.exists(path + ".bak")

    assert cli.main(command + ["--force", path]) == 0
    assert b"PROGRAM NAME" not in _bytes(path)
    assert _bytes(path + ".bak") == data


def test_a_folder_run_rewrites_only_programs(tmp_path):
    (tmp_path / "sub").mkdir()
    readme = _file(tmp_path, "sub/README.txt", b"Notes about this panel.\r\n")
    report = _file(tmp_path, "sub/report.txt", b"PPCL DISPLAY REPORT\r\n")
    program = _file(tmp_path, "sub/ahu.pcl", EXPORT)
    assert cli.main(["fmt", "-i", str(tmp_path)]) == 0
    assert _bytes(readme) == b"Notes about this panel.\r\n"
    assert _bytes(report) == b"PPCL DISPLAY REPORT\r\n"
    assert os.path.exists(program + ".bak")


def test_a_character_the_encoding_cannot_hold_is_refused(tmp_path):
    """Clone with a rename that brings in a character cp1252 lacks."""
    path = _file(tmp_path, "ahu.pcl", EXPORT)
    rc = cli.main(["transform", "clone", path, "--first", "20", "--last",
                   "20", "--start", "100", "--rename",
                   "SFAN=\N{GREEK CAPITAL LETTER OMEGA}FAN", "-i"])
    assert rc == 2
    assert _bytes(path) == EXPORT


def test_transform_in_place_writes_and_backs_up(tmp_path):
    """It used to crash: its parser had no --backup for the writer to read."""
    path = _file(tmp_path, "ahu.pcl", EXPORT)
    assert cli.main(["transform", "comment", path, "--lines", "20",
                     "-i"]) == 0
    out = _bytes(path)
    assert b"55\xb0F" in out and b"\r\n" in out
    assert out != EXPORT
    assert _bytes(path + ".bak") == EXPORT


def test_blocks_in_place_writes_beside_the_diagram(tmp_path):
    """Writing to the path it was given would replace the diagram."""
    diagram = {
        "name": "t",
        "blocks": [
            {"id": "k", "type": "constant", "params": {"value": 1}},
            {"id": "c", "type": "command",
             "params": {"point": "SFAN", "action": "on"}},
        ],
        "wires": [{"src": "k", "src_pin": "out", "dst": "c",
                   "dst_pin": "when"}],
    }
    path = _file(tmp_path, "AHU1.blocks.json",
                 json.dumps(diagram).encode("utf-8"))
    before = _bytes(path)
    assert cli.main(["blocks", path, "-i"]) == 0
    assert _bytes(path) == before
    assert b"ON(SFAN)" in _bytes(str(tmp_path / "AHU1.ppcl"))


def test_seq_fmt_in_place_keeps_encoding_and_refuses_json(tmp_path):
    from ppcl import sequence

    text = sequence.render(sequence.parse(
        'sequence "t"\n  description "Supply 55\N{DEGREE SIGN}F"\n\n'
        "points\n  SFAN  LDO\n\nmodes\n  Normal  otherwise\n\n"
        "table\n          Normal\n  SFAN    on\n"))
    path = _file(tmp_path, "t.seq", text.replace("\n", "\r\n").encode("cp1252"))
    assert cli.main(["seq", "fmt", path, "-i"]) == 0
    assert b"55\xb0F" in _bytes(path) and b"\r\n" in _bytes(path)

    as_json = _file(tmp_path, "t.json",
                    sequence.dumps(sequence.parse(text)).encode("utf-8"))
    before = _bytes(as_json)
    assert cli.main(["seq", "fmt", as_json, "-i"]) == 2
    assert _bytes(as_json) == before
