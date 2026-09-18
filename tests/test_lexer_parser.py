"""Lexer and parser tests, driven by constructs that appear in real PPCL."""

import pytest

from ppcl import parser, spec
from ppcl.ast_nodes import (
    Assignment,
    BinOp,
    CommandCall,
    Comment,
    Gosub,
    Goto,
    If,
    Num,
    ParameterDecl,
    Ref,
    Return,
    Sampled,
    TimeLit,
)
from ppcl.lexer import LexError, Tok, tokenize


# -- lexer -----------------------------------------------------------------


def test_dotted_operators_do_not_eat_decimals():
    toks = tokenize("RMTEMP.GT.80.0")
    kinds = [t.kind for t in toks[:-1]]
    assert kinds == [Tok.IDENT, Tok.DOTOP, Tok.NUMBER]
    assert toks[1].text == ".GT."
    assert toks[2].text == "80.0"


def test_time_literal_beats_number():
    toks = tokenize("TIME.EQ.23:58")
    assert toks[2].kind is Tok.TIME
    assert toks[2].text == "23:58"


def test_quoted_names_keep_dots():
    toks = tokenize('"BUILDING1.AHU01.SFAN"')
    assert toks[0].kind is Tok.QUOTED
    assert toks[0].text == "BUILDING1.AHU01.SFAN"


def test_priority_versus_at_name():
    toks = tokenize("ON(@EMER,@1FAN)")
    assert toks[2].kind is Tok.PRIORITY
    assert toks[4].kind is Tok.ATNAME


def test_macro_reference():
    toks = tokenize("%AHU%")
    assert toks[0].kind is Tok.MACRO


def test_unterminated_quote_is_an_error():
    with pytest.raises(LexError):
        tokenize('ON("UNCLOSED)')


# -- line splitting --------------------------------------------------------


@pytest.mark.parametrize(
    "text,number,body",
    [
        ("00050\tC comment", 50, "C comment"),
        ("  100  ON(FAN)", 100, "ON(FAN)"),
        ("100 ON(FAN)", 100, "ON(FAN)"),
        ("32767\tRETURN", 32767, "RETURN"),
        ("PARAMETER X = 5", None, "PARAMETER X = 5"),
    ],
)
def test_split_line_number(text, number, body):
    assert parser.split_line_number(text) == (number, body)


def test_comment_detection_does_not_catch_crtime():
    prog = parser.parse("10\tCRTIME = 5\n20\tC a comment\n30\tC\n")
    assert isinstance(prog.lines[0].stmt, Assignment)
    assert isinstance(prog.lines[1].stmt, Comment)
    assert isinstance(prog.lines[2].stmt, Comment)


def test_continuation_lines_are_joined():
    text = "00010\tON(A,B,&\n00020\tC,D)\n"
    prog = parser.parse(text)
    assert len(prog.lines) == 1
    assert prog.lines[0].continued
    assert len(prog.lines[0].stmt.args) == 4


# -- statements ------------------------------------------------------------


def test_if_then_else_with_priorities():
    stmt = parser.parse_statement_text(
        "IF (TIME.GT.8:00.AND.TIME.LT.16:00) THEN ON(@NONE,SFAN) ELSE ON(@OPER,SFAN)"
    )
    assert isinstance(stmt, If)
    assert stmt.then_stmt.priority.name == "@NONE"
    assert stmt.else_stmt.priority.name == "@OPER"


def test_gosub_with_bare_arguments():
    stmt = parser.parse_statement_text("GOSUB 4020 $ARG1,$ARG2,PT3")
    assert isinstance(stmt, Gosub)
    assert stmt.target == 4020
    assert [a.name for a in stmt.args] == ["$ARG1", "$ARG2", "PT3"]


def test_gosub_with_parenthesised_arguments():
    stmt = parser.parse_statement_text("GOSUB 100 (A,B)")
    assert stmt.target == 100 and len(stmt.args) == 2


def test_sample_wraps_a_statement():
    stmt = parser.parse_statement_text("SAMPLE(600) ON(HALFAN)")
    assert isinstance(stmt, Sampled)
    assert isinstance(stmt.statement, CommandCall)
    assert stmt.seconds.value == 600


def test_parameter_declaration():
    stmt = parser.parse_statement_text("PARAMETER DELAY = 15")
    assert isinstance(stmt, ParameterDecl)
    assert stmt.name == "DELAY" and stmt.value.value == 15


def test_operator_precedence_matches_the_manual():
    # Multiplication (level 4) binds tighter than subtraction (level 5).
    stmt = parser.parse_statement_text("X = 10 - 5 + 2 * 3")
    top = stmt.expr
    assert isinstance(top, BinOp) and top.op == "+"
    assert top.right.op == "*"


def test_parentheses_override_precedence():
    stmt = parser.parse_statement_text("X = (10 - 5) * 2")
    assert stmt.expr.op == "*"
    assert stmt.expr.left.op == "-"


def test_relational_binds_looser_than_arithmetic():
    stmt = parser.parse_statement_text("IF(A + 1.GT.B) THEN ON(C)")
    assert stmt.cond.op == ".GT."
    assert stmt.cond.left.op == "+"


def test_logical_binds_loosest():
    stmt = parser.parse_statement_text("IF(A.GT.1.AND.B.LT.2) THEN ON(C)")
    assert stmt.cond.op == ".AND."
    assert stmt.cond.left.op == ".GT."


def test_unparseable_line_becomes_unparsed_and_is_recorded():
    prog = parser.parse("10\tON(A\n20\tRETURN\n")
    assert len(prog.errors) == 1
    assert isinstance(prog.lines[1].stmt, Return)


def test_real_samples_parse_without_error(sample_files):
    for path in sample_files:
        prog = parser.parse_file(path)
        assert prog.errors == [], "%s: %s" % (path, prog.errors)


# -- [NodeName]PointName, A6V10374898 Ch.1 ---------------------------------


def test_node_qualified_reference_lexes_as_one_point():
    from ppcl import lexer

    toks = [t for t in lexer.tokenize("[Room101]RoomTemp") if t.kind.name != "EOF"]
    assert len(toks) == 1
    assert toks[0].text == "[Room101]RoomTemp"


def test_node_qualified_reference_still_yields_to_a_dotted_operator():
    from ppcl import lexer

    kinds = [t.kind.name for t in
             lexer.tokenize("[AdminBldg1]ReturnWaterTemp.GT.80.0")
             if t.kind.name != "EOF"]
    assert kinds == ["IDENT", "DOTOP", "NUMBER"]


def test_a_node_name_may_contain_spaces_and_a_subpoint_may_follow():
    from ppcl import lexer

    for text in ("[Bldg 1]Ahu01.SFAN", "[Dev201]Pt:SUBPT"):
        toks = [t for t in lexer.tokenize(text) if t.kind.name != "EOF"]
        assert len(toks) == 1, text
        assert toks[0].text == text


def test_an_unclosed_bracket_is_rejected_with_a_reason():
    from ppcl import lexer

    with pytest.raises(lexer.LexError) as excinfo:
        lexer.tokenize("[unclosed")
    assert "NodeName" in str(excinfo.value)


def test_getval_and_setval_parse():
    prog = parser.parse(
        "10\tGETVAL(TheRoomTemp,[Room101]RoomTemp,@PrVal)\n"
        '20\tSETVAL(1,@OoServe,"Room101:ROOM TEMP")\n'
        "30\tGOTO 10\n"
    )
    assert prog.errors == []


# -- BACnet property table -------------------------------------------------


def test_property_numbers_resolve_by_name_number_and_at_form():
    assert spec.property_number("@PrVal") == 85
    assert spec.property_number("PrVal") == 85
    assert spec.property_number(85) == 85
    assert spec.property_number("85") == 85
    assert spec.property_number("@NotAProperty") is None
    assert spec.property_number(999999) is None


def test_property_name_round_trips():
    for number, (short, _desc) in spec.BACNET_PROPERTIES.items():
        assert spec.property_name(number) == short
        assert spec.property_number("@" + short) is not None


def test_every_dangerous_property_is_in_the_property_table():
    for number in spec.DANGEROUS_PROPERTIES:
        assert number in spec.BACNET_PROPERTIES


# -- PXC.A's own disable syntax --------------------------------------------


def test_a_hash_disabled_line_parses_as_a_disabled_statement():
    """PXC.A is the one generation where a disabled line lives in the text.

    "Type # and a space at the front of a line to disable a line."
    -- Desigo PXC.A Web Interface User Guide (A6V12893115).

    The statement is kept rather than flattened to prose, so the linter still
    sees defects that would bite the moment someone re-enables the line.
    """
    prog = parser.parse("00010\tON(FAN)\n00020\t# OFF(FAN)\n00030\tGOTO 10\n")
    assert prog.errors == []
    line = prog.by_number()[20]
    assert line.disabled
    assert not line.is_executable
    assert type(line.stmt).__name__ == "CommandCall"
    assert line.stmt.name == "OFF"


def test_a_hash_disabled_line_that_is_not_valid_ppcl_is_not_an_error():
    """Whatever someone commented out, it is not running and must not fail."""
    prog = parser.parse("00010\tON(F)\n00020\t# half a thought &&&\n"
                        "00030\tGOTO 10\n")
    assert prog.errors == []
    assert prog.by_number()[20].disabled


def test_a_disabled_line_drops_out_of_the_executable_set():
    prog = parser.parse("00010\tON(F)\n00020\t# OFF(F)\n00030\tGOTO 10\n")
    numbers = {ln.number for ln in prog.lines if ln.is_executable}
    assert numbers == {10, 30}


def test_the_space_after_the_hash_is_required():
    """`#` against the statement is not the documented form.

    It is left to fail exactly as it would on the panel, rather than being
    quietly accepted here and rejected there.
    """
    prog = parser.parse("00010\t#OFF(FAN)\n00020\tGOTO 10\n")
    assert prog.errors
    assert not prog.by_number()[10].disabled


def test_a_C_comment_is_still_a_comment_not_a_disabled_line():
    prog = parser.parse("00010\tC just prose\n00020\tON(F)\n00030\tGOTO 20\n")
    line = prog.by_number()[10]
    assert not line.disabled
    assert type(line.stmt).__name__ == "Comment"


# -- the dot problem, in the places it was still unguarded ------------------
#
# All three found by parsing Siemens' own 84-program application library, where
# 146 of 15,726 lines failed. Ten still do, and those are real syntax errors in
# the library itself.


def test_an_unquoted_DEFINE_substitution_is_one_name():
    """%X%AAA is a name prefix plus a name, not two tokens.

    The documented example quotes it -- ON("%A01%.RAF") -- and quoted always
    worked. Siemens' shipped library writes it bare throughout, which is 116
    of the 146 failures.
    """
    prog = parser.parse("00010\tON(%X%AAA)\n00020\tGOTO 10\n")
    assert prog.errors == []
    assert prog.lines[0].stmt.name == "ON"

    both = parser.parse('00010\tON("%X%AAA")\n00020\tON(%X%AAA)\n'
                        "00030\tGOTO 10\n")
    assert both.errors == []


def test_a_substitution_tail_may_start_with_a_digit():
    """%X%1AA is real; _IDENT_RE requires a leading letter and would refuse it."""
    prog = parser.parse("00010\t%X%SUM = %X%1AA + %X%2AA\n00020\tGOTO 10\n")
    assert prog.errors == []


def test_a_substitution_does_not_swallow_a_dotted_operator():
    """%X%AAA.GT.%X%BBB is name, operator, name."""
    prog = parser.parse("00010\tIF (%X%AAA.GT.%X%BBB) THEN ON(%X%CCC)\n"
                        "00020\tGOTO 10\n")
    assert prog.errors == []
    assert type(prog.lines[0].stmt).__name__ == "If"


def test_an_at_name_does_not_swallow_a_dotted_operator():
    """@NONE.AND.$ARG3 lexed as one @-name plus $ARG3.

    Every condition written without spaces around a dotted operator after an
    @priority failed to parse. Siemens' optimum-start-stop programs are
    written that way throughout.
    """
    tokens = [t.text for t in tokenize("IF (C.EQ.@NONE.AND.D.EQ.OFF) THEN X=1.0")]
    assert "@NONE" in tokens
    assert ".AND." in tokens
    assert not [t for t in tokens if t.upper().startswith("@NONE.")]

    prog = parser.parse("00010\tIF (A.GT.B.AND.C.EQ.@NONE.AND.D.EQ.OFF) "
                        "THEN X = 1.0\n00020\tGOTO 10\n")
    assert prog.errors == []


def test_ordinary_at_names_still_lex_whole():
    assert parser.parse("00010\tON(@EMER,FAN)\n00020\tGOTO 10\n").errors == []
    assert parser.parse("00010\tON(@1FAN)\n00020\tGOTO 10\n").errors == []
    assert parser.parse("00010\tIF(FAN .EQ. @OPER) THEN ON(H)\n"
                        "00020\tGOTO 10\n").errors == []
