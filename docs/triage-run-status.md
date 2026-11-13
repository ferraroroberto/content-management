# Triage run status

A row of `triage_runs` is one stored newsletter-triage run. `tools/newsletter/triage/run_status.py` is the only module that names its states, maps the stored string to one and says which moves are legal.

## Why it is declared

The `status` column held three bare strings that the store, the runner and the review tab each spelled and compared on their own. Nothing refused a second write: a late `failed` could overwrite a run that had already finished `done`, and a stored value outside the three read as "wait for it to finish". One declared type and one settle rule make a new reader call the module instead of comparing a string.

## The states

The string values are the ones `schema.sql` documents for the column, so they never change.

| `RunStatus` | Value | Meaning | Review tab opens it |
|---|---|---|---|
| `RUNNING` | `running` | Registered; the stages are still working | no |
| `DONE` | `done` | Finished and results stored | yes |
| `FAILED` | `failed` | A stage raised; `stats["error"]` carries the cause | no |
| `UNKNOWN` | (never stored) | The stored value names none of the above | no |

`derive_run_status` maps a stored value to a state; an unrecognised or missing one is `UNKNOWN`, never `DONE`.

## Moves

`register_run` inserts `RUNNING`. `db.mark_run` settles a run to `DONE` or `FAILED` and nothing else: `settled_status` raises `IllegalRunTransition` for any other target, and the update is a compare-and-set on `status = 'running'`. A run that is already settled, or was replaced meanwhile, is left as it is and the call logs a warning. Settled runs never move again; re-running a window replaces the row.

## Who reads it

| Reader | Where | Calls |
|---|---|---|
| Insert a run | `register_run` in `tools/newsletter/triage/db.py` | `RunStatus.RUNNING` |
| Settle a run | `mark_run` in `tools/newsletter/triage/db.py` | `settled_status` |
| Mark done / failed | `run_window` in `tools/newsletter/triage/run.py` | `RunStatus.DONE`, `RunStatus.FAILED` |
| Review tab gate and message | `_render_review` in `app/tab_triage.py` | `derive_run_status` |

A new reader calls the module. It does not compare the status string.

## Tests

`tests/triage/test_triage_run_status.py` holds the stored-value table and the two proofs that a run cannot settle as anything but done or failed and that a settled run never moves again.
