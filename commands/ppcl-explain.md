---
description: Explain a PPCL command, priority, point type, rule code, or a whole program
argument-hint: <command | @priority | point-type | rule-code | path>
---

Explain `$1`.

**If it names a command, priority, point type, function or rule code**, call
`ppcl_explain` and answer from what it returns. Do not recall a signature from
memory — argument limits, firmware restrictions and the manual's own cautions
are easy to misremember and expensive to get wrong.

Then add what the reference cannot: when you would actually reach for it, what
it interacts badly with, and the mistake people make with it.

**If it is a path to a program**, call `ppcl_analyze` and `ppcl_lint`, then
explain what the program does — its modes, its interlocks, what it commands and
at what priority — in the order someone reading it for the first time would
want. Name the main loop and anything that runs once at startup.

**If it is a broader topic** — priority, the execution model, SSTO, decision
tables, what is verified versus approximated — call `ppcl_help` with a topic or
a search query.

Keep the manual citation attached to anything you assert about the language.
The point of a citation is that the reader can take it to a vendor.
