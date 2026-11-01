# Verification gates

No CI pipeline — run these locally before shipping, in order. `README.md`'s
[Verification](../README.md#-verification) section lists the bare commands;
this page is the per-stage detail (what each checks, its baseline file, and
how to shrink it).

## Test suite

Most of `tests/` is `unittest.TestCase`-based and picked up by discovery.
`tests/test_gmail_readonly.py` is pytest-style (plain test functions) —
`unittest discover` silently skips it, no error, no warning. Run it
separately with pytest, or run the whole suite through pytest instead, which
understands both styles.

## 1. Static-analysis ratchet (`scripts/static_ratchet_gate.py`)

Compares the repo's unused imports, unused variables, duplicated blocks and a
curated set of ruff bug (`B`) and security (`S`) findings (an HTTP call
without a timeout, `shell=True`, a mutable default argument) with the
committed baseline (`.static-ratchet-baseline.json`) and fails on any **new**
one, so the debt can only shrink. It needs Node (`npx` runs `jscpd`; the
first run downloads it) and `ruff` in the venv
(`pip install -r requirements.txt`). The wrapper reports a detector that
could not run as **NOT CONFIRMED** (a failure, never a pass or a skip).

After removing baselined debt, shrink the baseline in the same PR with
`scripts/static_ratchet.py --update-baseline`. `src/gmail_readonly/` and
`src/google_oauth_common/` are excluded: they are vendored copies. See the
scaffold's `docs/static-ratchet.md` for the exit codes and keys.

## 2. Docs ratchet (`scripts/docs_ratchet_gate.py`)

Fails when `README.md` grows past 40 KiB or past its baselined size, or when
`README.md` or a `docs/**/*.md` file gains a history marker (an issue
reference, a dated heading or a changelog heading) above its count in
`.docs-ratchet-baseline.json`. Docs state what is true and why, in the
present tense; history lives in git, PRs and issues. The wrapper reports a
doc it could not read as **NOT CONFIRMED**.

After rewording a marker away, shrink the baseline in the same PR with
`scripts/docs_ratchet.py --update-baseline`. See the scaffold's
`docs/docs-ratchet.md` for the exit codes and keys.

## 3. Comment ratchet (`scripts/comment_ratchet_gate.py`)

Counts `#NNN` issue references inside product-code comments per file (a
comment states the present-tense why; history lives in git, PRs and issues)
and fails when a file's count rises above `.comment-ratchet-baseline.json`.
The wrapper reports a file it could not scan as **NOT CONFIRMED**.

After rewording a reference away, shrink the baseline in the same PR with
`scripts/comment_ratchet.py --update-baseline`. `src/gmail_readonly/` and
`src/google_oauth_common/` are excluded: they are vendored copies. See the
scaffold's `docs/comment-ratchet.md` for the exit codes and keys.

## 4. Structure ratchet (`scripts/structure_ratchet_gate.py`)

Fails when a file passes 1,000 lines, a function 100 lines or a directory 40
modules and is new or above its size in `.structure-ratchet-baseline.json`,
or when the tracked root folders stop matching `shape` and `extra_roots` in
`.fleet.toml`. The wrapper reports something it could not measure as **NOT
CONFIRMED**.

After splitting a baselined file or function, shrink the baseline in the
same PR with `scripts/structure_ratchet.py --update-baseline`; only
`--allow-additions` adds an entry. `src/gmail_readonly/` and
`src/google_oauth_common/` are excluded: they are vendored copies. See the
scaffold's `docs/structure-ratchet.md` for the exit codes and keys.

## 5. Instruction ratchet (`scripts/instruction_ratchet_gate.py`)

Runs the weekly prompt audit's deterministic rules over the instruction files
(`CLAUDE.md`, `AGENTS.md`, every `.claude/skills/*/SKILL.md` and the docs a
skill links to) and fails when a file's count for a rule rises above
`.instruction-ratchet-baseline.json`, or a file passes its size cap. The
wrapper reports a file it could not measure as **NOT CONFIRMED**.

After rewording a hit away, shrink the baseline in the same PR with
`scripts/instruction_ratchet.py --update-baseline`; only `--allow-additions`
adds an entry. See the scaffold's `docs/instruction-ratchet.md` for the
rules, exit codes and keys.

## 6. Doc-references ratchet (`scripts/doc_refs_ratchet_gate.py`)

Reads `README.md`, `CLAUDE.md` and `docs/**/*.md` and fails when one names a
repo path, a `python -m` module or a relative link that does not exist and
is not recorded in `.doc-refs-ratchet-baseline.json`. Loopback ports are not
checked, because `.fleet.toml` declares no `port`. The wrapper reports a doc
or git it could not read as **NOT CONFIRMED**.

Point a reference at what exists now, or drop it; a path a doc names on
purpose goes in `allow_refs` in the `[doc_refs_ratchet]` table of
`.fleet.toml`. After fixing a baselined reference, shrink the baseline in
the same PR with `scripts/doc_refs_ratchet.py --update-baseline`; only
`--allow-additions` adds an entry. See the scaffold's
`docs/doc-refs-ratchet.md` for the exit codes and keys.

## 7. Dead-code ratchet (`scripts/dead_code_ratchet_gate.py`)

Fails when a new function, class or method appears that nothing in the repo
refers to and that is not recorded in `.dead-code-ratchet-baseline.json`.
Routes, decorated hooks, `__all__` exports and tests are never reported, and
a name used dynamically stays below the confidence floor. The vendored
`src/gmail_readonly/` and `src/google_oauth_common/` copies are excluded from
reporting but still read for references: their unused API
(`GmailMailbox.messages_by_ids`) belongs to the repo they were copied from.
The wrapper reports git or a Python file it could not read or parse as **NOT
CONFIRMED**.

Delete the definition or call it; a real entry point the rules miss goes in
`entry_decorators` or `allow` in the `[dead_code_ratchet]` table of
`.fleet.toml`. After deleting a baselined definition, shrink the baseline in
the same PR with `scripts/dead_code_ratchet.py --update-baseline`; only
`--allow-additions` adds an entry. See the scaffold's
`docs/dead-code-ratchet.md` for the exit codes and keys.

## 8. Conventions ratchet (`scripts/conventions_ratchet_gate.py`)

Holds three fleet conventions as `ast` rules and fails when a file's count
of hits for one rises above `.conventions-ratchet-baseline.json`, which is
empty today: `no-window` (a `subprocess` spawn without `creationflags=`),
`logging-not-print` (a `print` in a library package; command-line modules
print by design and are skipped) and `no-drive-path` (a string literal that
starts with a drive letter). The `secret-compare` and `git-wrapper` rules
stay off because the repo has no helper for either. The wrapper reports git
or a Python file it could not read or parse as **NOT CONFIRMED**.

Follow the convention; a call that must break it keeps a reasoned
`# <rule>-exempt: <reason>` marker on one of its lines. Only
`scripts/conventions_ratchet.py --update-baseline --allow-additions` adds an
entry. See the scaffold's `docs/conventions-ratchet.md` for the exit codes
and keys.
