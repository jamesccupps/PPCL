"""docs/EDITOR-INTEGRATION.md is a copy of spec.py, so it must be checked.

That document exists to be read *instead of* this repository -- someone
implementing PPCL support in an editor should not have to clone anything. The
whole value of it is that its token lists are right, and a hand-maintained
copy of a list is the exact shape of defect this project keeps finding: the
prose stops being true and nothing fails.

So the lists are compared against spec, not trusted. If a command is added to
spec.ALL and not to the document, this test says so.
"""

import io
import pathlib
import re

from ppcl import spec

DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "EDITOR-INTEGRATION.md"


def _fenced_words(heading):
    """The whitespace-separated words of the first code fence under a heading."""
    text = io.open(DOC, encoding="utf-8").read()
    match = re.search(r"### " + heading + r"[^\n]*\n.*?```\n(.*?)```", text, re.S)
    assert match, "no fenced block under heading %r" % heading
    return set(match.group(1).split())


def test_the_editor_document_lists_every_command_and_no_others():
    assert _fenced_words("Commands") == set(spec.ALL)


def test_the_editor_document_lists_every_function_and_no_others():
    assert _fenced_words("Functions") == set(spec.FUNCTIONS)


def test_the_editor_document_lists_every_operator_and_no_others():
    """The closed-set claim is the load-bearing one.

    The document tells the reader this list is exact -- no .NOT., no .EQUAL.,
    no .LESS. -- so it had better be.
    """
    assert _fenced_words("Operators") == set(spec.DOTTED_OPS)


def test_the_editor_document_lists_every_priority_and_no_others():
    assert _fenced_words("Priorities") == set(spec.PRIORITY_ORDER)


def test_the_editor_document_lists_every_status_indicator():
    assert _fenced_words("Status indicators") == set(spec.STATUS_INDICATORS)


def test_the_editor_document_lists_every_resident_point():
    assert _fenced_words("Resident points") == set(spec.RESIDENT_POINTS)


def test_the_editor_document_states_the_limits_spec_holds():
    """Numbers quoted in prose, checked against the values they came from."""
    text = io.open(DOC, encoding="utf-8").read()

    assert "%d characters" % spec.UNQUOTED_NAME_MAX in text
    assert "**%d**" % spec.MAX_OPERATORS in text
    assert "%d-character limit" % spec.OIP_SEQUENCE_MAX in text
    assert "`NODE0` … `NODE%d`" % spec.RESIDENT_RANGES[0][2] in text
    assert "`$ARG1` … `$ARG%d`" % spec.LOCAL_ARG_COUNT in text
    assert "`$LOC1` … `$LOC%d`" % spec.LOCAL_LOC_COUNT in text

    apogee = spec.MMI_LINE_LIMIT[spec.Firmware.APOGEE]
    pxc_a = spec.MMI_LINE_LIMIT[spec.Firmware.PXC_A]
    assert "**%d** characters on APOGEE" % apogee in text
    assert "**%d** on PXC.A" % pxc_a in text

    assert "**%d** per statement" % spec.MAX_OPERANDS[spec.Firmware.APOGEE] in text
    assert "**%d** on the physical" % spec.MAX_OPERANDS[spec.Firmware.LOGICAL] in text


def test_the_editor_document_names_the_firmware_only_tokens_it_warns_about():
    """It tells the reader not to report these as unknown commands."""
    text = io.open(DOC, encoding="utf-8").read()
    for name in spec.FIRMWARE_STATEMENT_TOKENS:
        assert "`%s`" % name in text, name
    # And it must not have picked up a name that is a real command.
    for name in spec.FIRMWARE_STATEMENT_TOKENS:
        assert name not in spec.ALL, name


def test_every_version_string_agrees():
    """The version lived in three files and all three went stale together.

    The package reported 0.2.0 while the published tag said v1.0.0, so a
    download of the release got a tool that misreported itself -- and the
    only way to notice was to compare a git tag against a Python attribute,
    which nothing did. pyproject now reads the attribute. The plugin
    manifest is static JSON and cannot, so it is checked here instead.
    """
    import json

    import ppcl

    root = pathlib.Path(__file__).resolve().parent.parent

    manifest = json.loads(
        io.open(root / ".claude-plugin" / "plugin.json", encoding="utf-8").read()
    )
    assert manifest["version"] == ppcl.__version__

    # pyproject must not carry a literal version at all -- it declares the
    # attribute as dynamic, which is what keeps it from going stale.
    pyproject = io.open(root / "pyproject.toml", encoding="utf-8").read()
    assert 'dynamic = ["version"]' in pyproject
    assert 'attr = "ppcl.__version__"' in pyproject
