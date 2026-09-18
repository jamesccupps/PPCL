"""Lint rule tests.

Each rule gets a case that must fire and, where a false positive is plausible,
a case that must stay quiet.
"""

import pytest

from ppcl import linter, parser, spec


def codes(text, **kw):
    prog = parser.parse(text, name="t")
    return {d.code for d in linter.lint(prog, **kw)}


def severity_of(text, code, **kw):
    prog = parser.parse(text, name="t")
    for d in linter.lint(prog, **kw):
        if d.code == code:
            return d.severity.value
    return None


# -- line numbering --------------------------------------------------------


def test_line_number_out_of_range():
    assert "E101" in codes("40000\tRETURN\n")


def test_duplicate_line_numbers():
    assert "E102" in codes("10\tON(A)\n10\tOFF(A)\n")


def test_out_of_order_lines():
    assert "W103" in codes("20\tON(A)\n10\tOFF(A)\n")


def test_long_executable_line_warns_but_comments_only_style():
    long_stmt = "10\tON(" + ",".join('"LONGPOINT%d"' % i for i in range(1, 7)) + ")\n"
    assert severity_of(long_stmt, "W104") == "warning"
    long_comment = "10\tC " + "x" * 90 + "\n"
    assert severity_of(long_comment, "W104") == "style"


# -- naming ----------------------------------------------------------------


def test_unquoted_long_name_is_an_error():
    assert "E105" in codes("10\tON(SUPPLYFAN01)\n")


def test_short_simple_name_is_fine():
    assert "E105" not in codes("10\tON(SFAN)\n")


def test_quoted_long_name_is_fine():
    assert "E105" not in codes('10\tON("BUILDING1.AHU01.SFAN")\n')


def test_reserved_word_assignment():
    assert "W107" in codes("10\tMONTH = 5\n")


def test_writing_to_a_builtin_local_is_not_a_reserved_word_violation():
    """$ARGn and $LOCn exist to be written to -- that is the whole mechanism.

    Siemens' own published MEC100K program writes a TABLE result into $ARG2 ten
    times over. Flagging that made the linter unusable on real subroutines.
    """
    text = (
        '10\tGOSUB 30 "IN","OUT"\n'
        "20\tGOTO 50\n"
        "30\tTABLE($ARG1,$ARG2,0.0,10.0,1.0,20.0)\n"
        "40\tRETURN\n"
        "50\tGOTO 10\n"
    )
    assert "W107" not in codes(text)


# -- arguments -------------------------------------------------------------


def test_unknown_command():
    assert "E110" in codes("10\tONN(A)\n")


def test_too_many_points_for_on():
    args = ",".join("P%d" % i for i in range(1, 18))
    assert "E111" in codes("10\tON(%s)\n" % args)


def test_priority_consumes_a_parameter_slot():
    sixteen = ",".join("P%d" % i for i in range(1, 17))
    assert "E111" in codes("10\tON(@EMER,%s)\n" % sixteen)
    fifteen = ",".join("P%d" % i for i in range(1, 16))
    assert "E111" not in codes("10\tON(@EMER,%s)\n" % fifteen)


def test_table_requires_whole_pairs():
    assert "E111" in codes("10\tTABLE(A,B,0,1,2)\n")


def test_priority_rejected_where_not_allowed():
    assert "E112" in codes("10\tAUTO(@EMER,A)\n")


def test_integer_where_decimal_required():
    assert "W113" in codes("10\tHLIMIT(84,ROOM16)\n")
    assert "W113" not in codes("10\tHLIMIT(84.0,ROOM16)\n")


def test_the_decimal_finding_is_advice_not_an_error():
    """Was E113 until a shipped program was seen running ``INITTO(0,...)``.

    ERROR claims the compiler will refuse the line, and that claim cannot be
    supported: the Insight Program Editor documents the argument as simply "a
    number", and field code uses integers. The advice to write the decimal
    stands; the severity was the part that was wrong.
    """
    assert severity_of("10\tHLIMIT(84,ROOM16)\n", "W113") == "warning"


def test_set_accepts_integers_on_apogee_only():
    text = "10\tSET(75,RMSET)\n"
    assert "W113" not in codes(text, firmware=spec.Firmware.APOGEE)
    assert "W113" in codes(text, firmware=spec.Firmware.LOGICAL)


def test_enum_argument_checked():
    assert "E114" in codes("10\tLOOP(5,PV,CV,SP,100,0,0,1,50.0,0.0,100.0,0)\n")


# -- control flow ----------------------------------------------------------


def test_branch_to_missing_line():
    assert "E201" in codes("10\tACT(999)\n20\tRETURN\n")


def test_goto_to_missing_line_is_redirected():
    assert "W202" in codes("10\tGOTO 15\n20\tON(A)\n")


def test_a_dangling_goto_is_a_warning_not_an_error():
    """It was ERROR on APOGEE, on the belief Desigo CC refuses to save it.

    Thirty-six of these came out of a live Desigo CC in running programs, so
    the belief was wrong. Siemens files it under "Common Compiler Errors *and
    Warnings*" and ships it as an opt-in Program Editor toggle.
    """
    text = "10\tGOTO 15\n20\tON(A)\n30\tGOTO 10\n"
    assert severity_of(text, "W202") == "warning"
    assert severity_of(text, "W202", firmware=spec.Firmware.APOGEE) == "warning"


def test_a_dangling_goto_says_where_control_actually_lands():
    prog = parser.parse("10\tGOTO 15\n20\tON(A)\n30\tGOTO 10\n", name="t")
    found = [d for d in linter.lint(prog) if d.code == "W202"]
    assert found and "lands on line 20" in found[0].message


def test_backward_goto_that_is_not_the_last_is_a_compiler_error():
    """The compiler permits exactly one backward GOTO: the last one.

    Its error text is "backwards GOTO found. With the exception of the last
    GOTO in the program, there was a GOTO found that refers to an earlier line
    number."
    """
    text = (
        "10\tON(A)\n"
        "20\tON(B)\n"
        "30\tIF(X.GT.1) THEN GOTO 20\n"
        "40\tGOTO 10\n"
    )
    assert severity_of(text, "W203") == "error"


def test_main_loop_trampoline_is_only_informational():
    text = "10\tON(A)\n20\tOFF(B)\n30\tGOTO 10\n"
    assert severity_of(text, "W203") == "info"


def test_goto_to_comment_warns_but_gosub_only_informs():
    goto = "10\tGOTO 30\n20\tON(A)\n30\tC label\n40\tON(B)\n"
    assert severity_of(goto, "W204") == "warning"
    gosub = "10\tGOSUB 30\n20\tGOTO 10\n30\tC label\n40\tRETURN\n"
    assert severity_of(gosub, "W204") == "info"


def test_time_based_command_outside_the_main_loop():
    text = (
        "10\tTOD(1,1,8:00,17:00,LITES)\n"
        "20\tON(A)\n"
        "30\tOFF(B)\n"
        "40\tGOTO 20\n"
    )
    assert "E205" in codes(text)


def test_time_based_command_inside_the_loop_is_clean():
    text = "10\tON(A)\n20\tTOD(1,1,8:00,17:00,LITES)\n30\tGOTO 10\n"
    assert "E205" not in codes(text)


def test_unreachable_code():
    text = "10\tGOTO 30\n20\tON(DEAD)\n30\tOFF(B)\n40\tGOTO 10\n"
    assert "W206" in codes(text)


def test_subroutine_without_return():
    text = "10\tGOSUB 100\n20\tGOTO 10\n100\tON(A)\n110\tON(B)\n"
    assert "E210" in codes(text)


def test_stray_return():
    assert "E212" in codes("10\tON(A)\n20\tRETURN\n30\tGOTO 10\n")


def test_stray_return_is_an_error_only_when_the_program_uses_gosub():
    """A template ships with the RETURN and no caller; that is not an error.

    Siemens' MEC100K program is exactly this shape -- a subroutine plus a
    comment telling the engineer where to add the GOSUB statements. It loads
    and runs. A program that does use GOSUB elsewhere and still strands a
    RETURN is a real defect and keeps ERROR.
    """
    template = "10\tON(A)\n20\tGOTO 50\n30\tSET(1.0,B)\n40\tRETURN\n50\tGOTO 10\n"
    assert severity_of(template, "E212") == "warning"

    real = (
        '10\tGOSUB 30 "X"\n'
        "20\tGOTO 60\n"
        "30\tSET($ARG1,B)\n"
        "40\tRETURN\n"
        "50\tRETURN\n"
        "60\tGOTO 10\n"
    )
    assert severity_of(real, "E212") == "error"


def test_time_command_in_subroutine():
    text = (
        "10\tGOSUB 100\n"
        "20\tGOTO 10\n"
        "100\tSAMPLE(60) ON(A)\n"
        "110\tRETURN\n"
    )
    assert "E213" in codes(text)


def test_gosub_inside_if():
    text = "10\tIF(A.GT.1) THEN GOSUB 100\n20\tGOTO 10\n100\tON(B)\n110\tRETURN\n"
    assert "E214" in codes(text)


def test_wait_as_if_target():
    text = "10\tIF(A.GT.1) THEN WAIT(60,B,C,11)\n20\tGOTO 10\n"
    assert "W215" in codes(text)


def test_onpwrt_placement():
    assert "W220" in codes("10\tON(A)\n20\tONPWRT(10)\n30\tGOTO 10\n")
    assert "W220" not in codes('10\tLOCAL("X")\n20\tONPWRT(20)\n30\tGOTO 20\n')


def test_duplicate_onpwrt():
    assert "W221" in codes("10\tONPWRT(10)\n20\tONPWRT(20)\n30\tGOTO 10\n")


# -- semantics -------------------------------------------------------------


def test_table_x_must_ascend():
    assert "E301" in codes("10\tTABLE(OAT,HWSP,60,100,0,180)\n")
    assert "E301" not in codes("10\tTABLE(OAT,HWSP,0,180,60,100)\n")


def test_todmod_rejects_mode_16():
    diags = codes("10\tTODMOD(1,1,1,1,1,8,16)\n")
    assert "E302" in diags


def test_holiday_mode_without_holida():
    assert "W304" in codes("10\tTOD(16,1,8:00,17:00,LITES)\n20\tGOTO 10\n")


def test_holida_date_range():
    assert "E305" in codes("10\tHOLIDA(13,25)\n")


def test_holida_must_precede_tod():
    text = "10\tTOD(1,1,8:00,17:00,L)\n20\tHOLIDA(12,25)\n30\tGOTO 10\n"
    assert "W306" in codes(text)


def test_dc_pattern_digits():
    assert "E307" in codes("10\tDC(FAN1,1358)\n")
    assert "E307" not in codes("10\tDC(FAN1,1350)\n")


def test_pdldat_ranges():
    assert "E308" in codes("10\tPDLDAT(FAN17,600,5,180,10)\n")


def test_timavg_sample_bounds():
    assert "E309" in codes("10\tTIMAVG(AVG,600,20,RMTEMP)\n")


def test_loop_limits_and_bias():
    assert "E310" in codes("10\tLOOP(0,PV,CV,SP,100,0,0,1,50.0,100.0,0.0,0)\n")
    assert "W310" in codes("10\tLOOP(0,PV,CV,SP,100,0,0,1,150.0,0.0,100.0,0)\n")


def test_ssto_zone_range():
    assert "E311" in codes(
        "10\tSSTO(9,1,A,B,6:30,7:45,8:00,15:30,16:45,17:00,0.0,0.0)\n"
    )


def test_sample_cannot_wrap_a_timing_command():
    assert "E313" in codes("10\tSAMPLE(60) TOD(1,1,8:00,17:00,L)\n20\tGOTO 10\n")


def test_if_operand_limit_is_firmware_dependent():
    """Rev. 5 says 13 operands; the Desigo CC editor says 16.

    Both figures are published and they disagree, so the linter applies the
    Desigo figure to APOGEE firmware and the conservative one to the older
    families rather than picking a winner.
    """
    cond = ".AND.".join("P%d.GT.1" % i for i in range(1, 9))  # 16 operands
    text = "10\tIF(%s) THEN ON(X)\n20\tGOTO 10\n" % cond
    assert "E314" not in codes(text, firmware=spec.Firmware.APOGEE)
    assert "E314" in codes(text, firmware=spec.Firmware.LOGICAL)

    bigger = ".AND.".join("P%d.GT.1" % i for i in range(1, 11))  # 20 operands
    assert "E314" in codes(
        "10\tIF(%s) THEN ON(X)\n20\tGOTO 10\n" % bigger,
        firmware=spec.Firmware.APOGEE,
    )


def test_operator_limit():
    expr = " + ".join("P%d" % i for i in range(1, 40))
    assert "E315" in codes("10\tX = %s\n20\tGOTO 10\n" % expr)
    short = " + ".join("P%d" % i for i in range(1, 10))
    assert "E315" not in codes("10\tX = %s\n20\tGOTO 10\n" % short)


def test_colon_qualified_point_references_parse():
    """FLN subpoints and cross-program locals use a colon."""
    for src in (
        'ON("Dev201:DAY_CLG_STPT")',
        "X = PROG1:FLAG",
        "IF(Dev201:STPT.GT.72.0) THEN ON(FAN)",
    ):
        assert parser.parse_statement_text(src) is not None


def test_subroutine_benefit_table_is_applied_verbatim():
    """Siemens' table, including the row people get wrong.

    A one-line subroutine is rated NO at every call count. Three lines at two
    calls is EVEN. Three lines at three calls is YES.
    """
    from ppcl.rules.performance import subroutine_benefit

    assert subroutine_benefit(1, 4) == "no"
    assert subroutine_benefit(2, 4) == "yes"
    assert subroutine_benefit(3, 2) == "even"
    assert subroutine_benefit(3, 3) == "yes"
    assert subroutine_benefit(4, 2) == "yes"


def test_one_line_subroutine_is_never_worth_it():
    text = "10\tGOSUB 100\n20\tGOTO 10\n100\tON(A)\n110\tRETURN\n"
    assert "P708" in codes(text)


def test_subroutine_above_break_even_is_not_flagged():
    # Three lines called three times is rated YES.
    ok = (
        "10\tGOSUB 100\n"
        "20\tGOSUB 100\n"
        "30\tGOSUB 100\n"
        "40\tGOTO 10\n"
        "100\tON(A)\n"
        "110\tON(B)\n"
        "120\tON(C)\n"
        "130\tRETURN\n"
    )
    assert "P708" not in codes(ok)


def test_adaptive_control_signatures():
    """ADAPTM and ADAPTS take exactly 14 named parameters.

    The Desigo CC Command Assist shows only ``pt1..pt14``, which is why this
    was once modelled as "1 to 14 points". A6V10374898 Ch.3 gives the real
    parameter lists, so a short call is now correctly an error.
    """
    adapts = (
        "10\tADAPTS(DAT,CVOUT,DASP,5,3.0,120,1,"
        "40.0,100.0,0.0,100.0,0.0,0,ADERR)\n20\tGOTO 10\n"
    )
    adaptm = (
        "10\tADAPTM(SAT,MCV,SASP,MATCV,20.0,5,3.0,"
        "90,120,150,33.0,33.0,67.0,AMERR)\n20\tGOTO 10\n"
    )
    assert "E111" not in codes(adapts)
    assert "E111" not in codes(adaptm)

    for name in ("ADAPTM", "ADAPTS"):
        assert "E111" in codes("10\t%s(A,B,C)\n20\tGOTO 10\n" % name), name
        too_many = ",".join("P%d" % i for i in range(1, 17))
        assert "E111" in codes("10\t%s(%s)\n20\tGOTO 10\n" % (name, too_many)), name


def test_adaptive_control_parameters_are_named_not_anonymous():
    """The whole point of the rewrite: an agent can explain each argument."""
    for name in ("ADAPTM", "ADAPTS"):
        params = [p.name for p in spec.ALL[name].fixed]
        assert len(params) == 14, name
        assert params[:3] == ["pv", "cv", "sp"], name
        assert params[-1] == "err", name


def test_lsq2_signature_and_line_references():
    """LSQ2(execution,pt1,..,pt6,startline#,endline#)."""
    good = (
        "100\tLSQ2(1,A,B,C,200,220)\n"
        "200\tLSQDAT(X1,Y1,Z1)\n"
        "210\tLSQDAT(X2,Y2,Z2)\n"
        "220\tLSQDAT(X3,Y3,Z3)\n"
        "230\tGOTO 100\n"
    )
    assert "E111" not in codes(good)
    assert "E201" not in codes(good)
    # Fewer than execution + one point + two line numbers is invalid.
    assert "E111" in codes("10\tLSQ2(1,A)\n20\tGOTO 10\n")
    # A trailing line number that does not exist is a bad reference.
    assert "E201" in codes("10\tLSQ2(1,A,900,910)\n20\tGOTO 10\n")


def test_pdl_commands_must_be_defined_in_order():
    """PDLMTR, PDLSET, PDLDPG, PDL, PDLDAT -- in that order."""
    good = (
        "10\tPDLMTR(1,M,0)\n"
        "20\tPDLSET(1,8:00,100.0)\n"
        "30\tPDLDPG(1,KW,TGT)\n"
        "40\tPDL(1,KW,TGT,100,199,0)\n"
        "50\tPDLDAT(FAN,10,5,180,10)\n"
        "60\tGOTO 10\n"
    )
    assert "W313" not in codes(good)
    assert "W313" in codes("10\tPDL(1,KW,TGT,100,199,0)\n"
                           "20\tPDLMTR(1,M,0)\n30\tGOTO 10\n")


def test_a_load_handler_panel_is_not_flagged_for_the_missing_three():
    """The five are split across panels by design.

    A predictor panel carries PDLMTR/PDLSET/PDLDPG; each load-handler carries
    PDL and PDLDAT. Only what is present gets ordered.
    """
    handler = ("10\tPDL(1,KW,TGT,100,199,0)\n"
               "20\tPDLDAT(FAN,10,5,180,10)\n30\tGOTO 10\n")
    assert "W313" not in codes(handler)
    predictor = ("10\tPDLMTR(1,M,0)\n20\tPDLSET(1,8:00,100.0)\n"
                 "30\tPDLDPG(1,KW,TGT)\n40\tGOTO 10\n")
    assert "W313" not in codes(predictor)


def test_only_one_ordering_finding_per_program():
    """The whole block wants reordering; five findings would be five ways of
    saying the same thing."""
    scrambled = (
        "10\tPDLDAT(FAN,10,5,180,10)\n"
        "20\tPDL(1,KW,TGT,100,199,0)\n"
        "30\tPDLDPG(1,KW,TGT)\n"
        "40\tPDLSET(1,8:00,100.0)\n"
        "50\tPDLMTR(1,M,0)\n"
        "60\tGOTO 10\n"
    )
    prog = parser.parse(scrambled, name="t")
    assert len([d for d in linter.lint(prog) if d.code == "W313"]) == 1


def test_device_local_constructs_do_not_cross_the_network():
    """Resident points, status indicators and special functions are per panel."""
    assert "W340" in codes('10\tIF("PANEL2:TIME" .GT. 8:00) THEN ON(FAN)\n20\tGOTO 10\n')
    assert "W340" in codes('10\tIF("PANEL2:FAN" .EQ. ALARM) THEN ON(HORN)\n20\tGOTO 10\n')
    assert "W340" in codes('10\tX = TOTAL("PANEL2:PUMP")\n20\tGOTO 10\n')
    assert "W340" in codes("10\tIF([Bldg1]TIME .GT. 8:00) THEN ON(FAN)\n20\tGOTO 10\n")


def test_an_ordinary_off_panel_point_is_not_flagged():
    """Only the device-local constructs are affected, not every qualified name."""
    assert "W340" not in codes('10\tIF("PANEL2:MAT" .LT. 38.0) THEN ON(FAN)\n20\tGOTO 10\n')
    assert "W340" not in codes("10\tIF([Bldg1]MAT .LT. 38.0) THEN ON(FAN)\n20\tGOTO 10\n")
    assert "W340" not in codes("10\tIF(TIME .GT. 8:00) THEN ON(FAN)\n20\tGOTO 10\n")
    assert "W340" not in codes("10\tX = TOTAL(PUMP)\n20\tGOTO 10\n")


def test_the_network_locality_finding_is_advice():
    """A colon is also FLN subpoint syntax, so this cannot be asserted."""
    text = '10\tIF("PANEL2:TIME" .GT. 8:00) THEN ON(FAN)\n20\tGOTO 10\n'
    assert severity_of(text, "W340") == "info"


def test_a_parenthesis_in_a_point_name_is_an_error():
    """The Program Editor will not compile or save these at all."""
    assert "E120" in codes('10\tON("FAN(1)")\n20\tGOTO 10\n')
    assert "E120" not in codes('10\tON("FAN1")\n20\tGOTO 10\n')


def test_a_command_call_is_not_mistaken_for_a_parenthesised_name():
    assert "E120" not in codes("10\tA = SQRT(B)\n20\tGOTO 10\n")


def test_unguarded_releas_in_the_main_loop_is_flagged():
    text = (
        "100\tIF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)\n"
        "110\tRELEAS(SFAN)\n"
        "120\tGOTO 100\n"
    )
    assert "W339" in codes(text)


def test_a_releas_guarded_by_a_priority_test_is_quiet():
    """Siemens' own remedy, and the shape real boiler programs already use."""
    text = (
        "100\tIF(MAT.LT.38.0) THEN OFF(@EMER,SFAN)\n"
        "110\tIF(SFAN .NE. @NONE) THEN RELEAS(@EMER,SFAN)\n"
        "120\tGOTO 100\n"
    )
    assert "W339" not in codes(text)


def test_a_releas_that_runs_once_at_startup_is_quiet():
    text = (
        "100\tRELEAS(SFAN)\n"
        "110\tON(SFAN)\n"
        "120\tGOTO 110\n"
    )
    assert "W339" not in codes(text)


def test_the_releas_finding_is_advice_not_a_warning():
    text = "100\tRELEAS(SFAN)\n110\tGOTO 100\n"
    assert severity_of(text, "W339") == "info"


def _lsq2_program(rows, start=200, end=260):
    text = "100\tLSQ2(1,A,B,C,D,E,F,%d,%d)\n" % (start, end)
    for i in range(rows):
        text += "%d\tLSQDAT(X%d,Y%d,Z%d)\n" % (200 + i * 10, i, i, i)
    return text + "300\tGOTO 100\n"


def test_lsq2_wants_exactly_seven_lsqdat_rows():
    """Eight lines total: the LSQ2 plus seven LSQDAT rows."""
    assert "W338" not in codes(_lsq2_program(7))
    assert "W338" in codes(_lsq2_program(5))
    assert "W338" in codes(_lsq2_program(7, end=340) + "340\tLSQDAT(Q,Q,Q)\n")


def test_lsq2_rows_outside_the_named_range_do_not_count():
    """Seven LSQDAT statements exist, but only five are inside the range."""
    text = _lsq2_program(7, end=240)
    assert "W338" in codes(text)


def test_lsq2_with_a_backwards_range_is_reported():
    assert "W338" in codes(_lsq2_program(7, start=260, end=200))


def test_lsqdat_takes_exactly_three_points():
    assert "E111" not in codes("10\tLSQDAT(A,B,C)\n20\tGOTO 10\n")
    assert "E111" in codes("10\tLSQDAT(A,B)\n20\tGOTO 10\n")
    assert "E111" in codes("10\tLSQDAT(A,B,C,D)\n20\tGOTO 10\n")


def test_lsq2_line_numbers_survive_renumbering():
    from ppcl import formatter

    prog = parser.parse(
        "100\tLSQ2(1,A,B,C,200,220)\n"
        "200\tLSQDAT(X1,Y1,Z1)\n"
        "210\tLSQDAT(X2,Y2,Z2)\n"
        "220\tLSQDAT(X3,Y3,Z3)\n"
        "230\tGOTO 100\n"
    )
    result = formatter.renumber(prog, start=10, step=10)
    assert "LSQ2(1,A,B,C,20,40)" in result.text
    # The execution parameter is a value, not a line, and must not be rewritten.
    assert result.text.splitlines()[0].endswith("LSQ2(1,A,B,C,20,40)")


def test_arc_is_not_a_function():
    """The Desigo precedence table's ARC is a documentation error; ATN is real."""
    assert "E118" not in codes("10\tX = ATN(Y)\n20\tGOTO 10\n")
    found = codes("10\tX = ARC(Y)\n20\tGOTO 10\n")
    assert "E118" in found


def test_unknown_function_suggests_a_correction():
    prog = parser.parse("10\tX = SQRTT(Y)\n20\tGOTO 10\n", name="t")
    diag = next(d for d in linter.lint(prog) if d.code == "E118")
    assert "SQRT" in diag.detail


def test_a_bacnet_sampled_assignment_lints_clean():
    """The shape a real Desigo program uses to mirror a third-party sensor.

    Names and device instance are invented -- see HANDOFF section 0. What the
    test is for is the *form*: a SAMPLE gating an assignment from a BACnet
    encoded reference, which has to parse and produce no findings.
    """
    src = (
        "00010\tC\n"
        "00020\tC Virtual outdoor air temp, mirrored over BACnet\n"
        "00030\tC\n"
        '00040\tSAMPLE(120) "VIRTOAT" = "BAC_12345_AI_1"\n'
        "00050\tGOTO 40\n"
    )
    prog = parser.parse(src, name="t")
    assert prog.errors == []
    bad = [
        d for d in linter.lint(prog)
        if d.severity.value in ("error", "warning")
    ]
    assert bad == [], "\n".join(d.format("t") for d in bad)


def test_bacnet_reference_form():
    assert "W117" not in codes('10\tON("BAC_10_MO_1")\n20\tGOTO 10\n')
    assert "W117" in codes('10\tON("BAC_10_ZZ_1")\n20\tGOTO 10\n')
    assert "W117" in codes('10\tON("BAC_10_MO")\n20\tGOTO 10\n')


def test_define_arguments_may_be_unquoted():
    """Siemens' documented example: DEFINE(A01,Bld01.Ahu01)."""
    text = "10\tDEFINE(A01,Bld01.Ahu01)\n20\tON(\"%A01%.RAF\")\n30\tGOTO 20\n"
    found = codes(text)
    assert "E105" not in found
    assert "W116" not in found
    assert "E100" not in found


def test_undeclared_local():
    assert "E320" in codes('10\t"$FLAG" = 1.0\n20\tGOTO 10\n')
    assert "E320" not in codes('10\tLOCAL("FLAG")\n20\t"$FLAG" = 1.0\n30\tGOTO 20\n')


def test_builtin_locals_need_no_declaration():
    assert "E320" not in codes('10\t"$LOC1" = 1.0\n20\tGOTO 10\n')


def test_unused_local():
    assert "W321" in codes('10\tLOCAL("SPARE")\n20\tON(A)\n30\tGOTO 20\n')


# -- priority --------------------------------------------------------------


def test_unreleased_priority():
    text = "10\tIF(A.LT.1) THEN OFF(@EMER,SFAN)\n20\tGOTO 10\n"
    assert "W330" in codes(text)


def test_matched_release_is_clean():
    text = (
        "10\tIF(A.LT.1) THEN OFF(@EMER,SFAN)\n"
        "20\tIF(A.GE.1) THEN RELEAS(@EMER,SFAN)\n"
        "30\tGOTO 10\n"
    )
    assert "W330" not in codes(text)


def test_a_point_driven_at_priority_every_pass_is_W341_not_W330():
    """Both branches covered means the point cannot strand -- but an operator
    cannot hold it either.

    Siemens' own library is full of this: 148 of the 170 elevated-priority
    writes in it are driven every pass rather than left behind. Calling that
    "never released" told the engineer the wrong thing, because nothing is
    stuck; what is true is that a command from an operator or a schedule is
    overwritten within a second or two, silently.
    """
    text = (
        "10\tIF(OAT.GT.60.0) THEN ON(@OPER,SFAN) ELSE OFF(@OPER,SFAN)\n"
        "20\tGOTO 10\n"
    )
    found = codes(text)
    assert "W341" in found
    assert "W330" not in found


def test_an_unconditional_command_at_priority_is_W341():
    text = "10\tON(@OPER,SFAN)\n20\tGOTO 10\n"
    found = codes(text)
    assert "W341" in found
    assert "W330" not in found


def test_only_one_branch_covered_is_still_W330():
    """The strandable case. When the condition clears, nothing rewrites it."""
    text = (
        "10\tIF(OAT.GT.90.0) THEN ON(@OPER,CHLR)\n"
        "20\tGOTO 10\n"
    )
    found = codes(text)
    assert "W330" in found
    assert "W341" not in found


def test_a_sampled_command_is_not_driven_every_pass():
    """SAMPLE exists to make a statement run less often than every pass."""
    text = "10\tSAMPLE(60) ON(@OPER,SFAN)\n20\tGOTO 10\n"
    found = codes(text)
    assert "W330" in found
    assert "W341" not in found


def test_a_released_point_raises_neither():
    text = (
        "10\tIF(OAT.GT.60.0) THEN ON(@OPER,SFAN) ELSE RELEAS(@OPER,SFAN)\n"
        "20\tGOTO 10\n"
    )
    found = codes(text)
    assert "W330" not in found and "W341" not in found


def test_release_at_too_low_a_priority():
    text = (
        "10\tIF(A.LT.1) THEN OFF(@OPER,SFAN)\n"
        "20\tIF(A.GE.1) THEN RELEAS(@PDL,SFAN)\n"
        "30\tGOTO 10\n"
    )
    assert "W331" in codes(text)


def test_releas_is_not_treated_as_a_conflicting_writer():
    text = (
        "10\tIF(A.LT.1) THEN OFF(@EMER,SFAN)\n"
        "20\tIF(A.GE.1) THEN RELEAS(@EMER,SFAN)\n"
        "30\tON(SFAN)\n"
        "40\tGOTO 10\n"
    )
    assert "W332" not in codes(text)


def test_two_unconditional_writers_conflict():
    text = "10\tSET(50.0,VLV)\n20\tTABLE(OAT,VLV,0,10,60,90)\n30\tGOTO 10\n"
    assert severity_of(text, "W332") == "warning"


def test_guarded_override_is_only_informational():
    text = (
        "10\tTABLE(OAT,VLV,0,10,60,90)\n"
        "20\tIF(SFAN.EQ.OFF) THEN SET(0.0,VLV)\n"
        "30\tGOTO 10\n"
    )
    assert severity_of(text, "W332") == "info"


def test_unguarded_duty_cycle():
    assert "W333" in codes("10\tDC(EFAN1,1350)\n20\tGOTO 10\n")


def test_analog_equality_with_point_database():
    text = "10\tIF(RMTEMP.EQ.80) THEN ON(A)\n20\tGOTO 10\n"
    assert "W334" in codes(text, point_types={"RMTEMP": "LAI"})
    assert "W334" not in codes(text, point_types={"RMTEMP": "LDI"})


def test_flag_comparisons_are_not_flagged():
    text = ' 10\tLOCAL("F")\n20\tIF("$F".EQ.1.0) THEN ON(A)\n30\tGOTO 20\n'
    assert "W334" not in codes(text)


# -- optimisation ----------------------------------------------------------


def test_mergeable_commands():
    assert "P701" in codes("10\tON(A)\n20\tON(B)\n30\tON(C)\n40\tGOTO 10\n")


def test_duplicate_statements():
    assert "P702" in codes("10\tON(A)\n20\tOFF(B)\n30\tON(A)\n40\tGOTO 10\n")


def test_constant_condition():
    assert "P703" in codes("10\tIF(1.GT.2) THEN ON(A)\n20\tGOTO 10\n")


def test_self_assignment():
    assert "P704" in codes("10\tX = X\n20\tGOTO 10\n")


def test_suppression():
    text = "10\tON(A)\n10\tOFF(A)\n"
    assert "E102" in codes(text)
    assert "E102" not in codes(text, disabled=["E102"])


# -- GETVAL / SETVAL property access ---------------------------------------


def test_setval_out_of_service_is_flagged():
    """The point stops tracking its input and still looks uncommanded."""
    text = '10\tSETVAL(1,@OoServe,"Room101:ROOM TEMP")\n20\tGOTO 10\n'
    assert "W336" in codes(text)


def test_setval_dangerous_property_by_number_is_flagged():
    """104 is @DefCmd; the number form must be recognised too."""
    assert "W336" in codes("10\tSETVAL(50.0,104,BAC_12345_AO_67)\n20\tGOTO 10\n")


def test_setval_of_an_ordinary_property_stays_quiet():
    assert "W336" not in codes("10\tSETVAL(72.0,@PrVal,ZNSP)\n20\tGOTO 10\n")


def test_a_property_specifier_is_not_reported_as_an_unresolved_point():
    """@PrVal names a property of a point, not a point."""
    from ppcl import analyzer

    prog = parser.parse("10\tGETVAL(DEST,SRC,@PrVal)\n20\tGOTO 10\n")
    names = {u.name for u in analyzer.analyze(prog).uses}
    assert "@PrVal" not in names
    assert {"DEST", "SRC"} <= names


# -- SSTO ------------------------------------------------------------------


SSTOCO_LINE = (
    "10\tSSTOCO(1,1,ROOM10,OATEMP,75.0,0.01,0.3,0.05,0.083,"
    "72.0,0.1,0.1,0.2,0.083)\n"
)
SSTO_LINE = (
    "20\tSSTO(1,1,ONTIM,OFTIM,6:30,7:45,8:00,15:30,16:45,17:00,0.0,0.0)\n"
)


def test_ssto_whose_calculated_times_are_never_read_is_flagged():
    """SSTO only computes; TOD or TODSET has to act on the result.

    The program is legal and the numbers are right -- it simply does nothing,
    which is the failure mode an SSTO installation actually has.
    """
    orphaned = SSTOCO_LINE + SSTO_LINE + "30\tTOD(1,1,6:00,18:00,SFAN)\n40\tGOTO 10\n"
    assert "W337" in codes(orphaned)


def test_ssto_feeding_a_tod_stays_quiet():
    wired = SSTOCO_LINE + SSTO_LINE + "30\tTOD(1,1,ONTIM,OFTIM,SFAN)\n40\tGOTO 10\n"
    assert "W337" not in codes(wired)


# -- firmware availability -------------------------------------------------


def test_oip_is_an_error_on_pxc_a_but_fine_on_apogee():
    """A6V10374898: the PXC.A runtime considers OIP invalid, with no
    replacement offered. "Valid PPCL" and "runs on my panel" differ."""
    text = '10\tOIP(RPT7AM,"P/D/P/Y/V/A/N/*")\n20\tGOTO 10\n'
    assert "E119" not in codes(text, firmware=spec.Firmware.APOGEE)
    assert severity_of(text, "E119", firmware=spec.Firmware.PXC_A) == "error"


def test_an_unrestricted_command_is_never_flagged_by_firmware():
    for firmware in spec.Firmware:
        assert "E119" not in codes(
            "10\tON(SFAN)\n20\tGOTO 10\n", firmware=firmware
        ), firmware.value


def test_every_firmware_has_the_limits_the_rules_index_by():
    """A new firmware that is missing from a limit table is a KeyError at
    lint time, which is a crash rather than a finding."""
    for firmware in spec.Firmware:
        assert firmware in spec.MMI_LINE_LIMIT, firmware.value
        assert firmware in spec.MMI_CONTINUATION_LIMIT, firmware.value
        assert firmware in spec.MAX_OPERANDS, firmware.value


def test_linting_works_on_every_firmware():
    text = '10\tLOCAL("F")\n20\tIF(MAT.LT.38.0) THEN "$F" = 1.0\n30\tGOTO 20\n'
    for firmware in spec.Firmware:
        codes(text, firmware=firmware)


def test_every_statement_pxc_a_removed_is_rejected_there():
    """Ten statements, one table, from A6V10374898.

    Was one (OIP) until the manual's "Obsolete PPCL Statements Removed from
    the Language" page was found. It also settles a question that sat open for
    four research passes: ADAPTM and ADAPTS really are gone on PXC.A.
    """
    from ppcl import spec

    for name in spec.PXC_A_REMOVED:
        cmd = spec.ALL[name]
        assert spec.Firmware.PXC_A not in cmd.firmware, name
        assert spec.Firmware.APOGEE in cmd.firmware, name
        assert any("PXC.A" in note for note in cmd.notes), name


def test_removed_statements_fire_E119_on_pxc_a_and_not_on_apogee():
    from ppcl import spec

    src = (
        "00010\tONPWRT(100)\n"
        "00020\tALARM(PT)\n"
        "00030\tNORMAL(PT)\n"
        "00040\tEPHONE(1)\n"
        "00050\tDPHONE(1)\n"
        "00060\tENCOV(PT)\n"
        "00070\tDISCOV(PT)\n"
        "00100\tON(FAN)\n"
        "00110\tGOTO 100\n"
    )
    prog = parser.parse(src, name="t")
    on_pxc = [d for d in linter.lint(prog, firmware=spec.Firmware.PXC_A)
              if d.code == "E119"]
    assert len(on_pxc) == 7
    assert not [d for d in linter.lint(prog, firmware=spec.Firmware.APOGEE)
                if d.code == "E119"]


def test_the_finding_carries_siemens_reason_not_just_the_refusal():
    """"ONPWRT is unavailable" is true and useless.

    Why it is unavailable is the part that changes what you do: there is no
    warmstart, so the program always resumes at line 1 regardless.
    """
    from ppcl import spec

    prog = parser.parse("00010\tONPWRT(100)\n00020\tON(F)\n00030\tGOTO 20\n",
                        name="t")
    d = next(x for x in linter.lint(prog, firmware=spec.Firmware.PXC_A)
             if x.code == "E119")
    assert "warmstart" in d.detail
    assert "first line after a power failure" in d.detail
    assert "Obsolete PPCL Statements" in d.manual


def test_a_comment_limit_counts_the_comment_text_only():
    """"not including the line number or operator C" -- A6V10374898.

    Measuring the raw line instead flags a comment several characters early,
    which is how this read until that page was found.
    """
    at_limit = "00010\tC " + "x" * 66 + "\n00020\tON(F)\n00030\tGOTO 20\n"
    over = "00010\tC " + "x" * 67 + "\n00020\tON(F)\n00030\tGOTO 20\n"
    assert "W104" not in codes(at_limit)
    assert "W104" in codes(over)


def test_an_executable_line_still_counts_its_line_number():
    """Different rule for a different reason: 125-1896 gives the executable
    limit as characters per line *including* the line number."""
    body = "ON(" + ",".join('"LONGPOINT%d"' % i for i in range(1, 8)) + ")"
    assert "W104" in codes("00010\t" + body + "\n00020\tGOTO 10\n")
