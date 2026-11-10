# Podcast clip status

Each clip of a podcast episode has one review status in `<episode folder>/review.json`. `podcast/review_state.py` is the only module that names the states, maps a stored string to one and moves a clip between them.

## Why it is declared

The status was four bare strings spelled by the review state, the revise stage, the Podcast tab and the review CLI, and `set_status` accepted `changes` with no feedback behind it: a clip that read "changes requested" while no round was open for the revise stage to work on. One declared type and one rule for how `changes` is reached make a new reader call the module instead of comparing a string.

## The states

The string values are the ones `review.json` holds, so they never change.

| `ClipStatus` | Value | Meaning | Reached by |
|---|---|---|---|
| `PENDING` | `pending` | Not decided yet; also where a clip returns after a revision or a reopen | a new clip, "back to pending", a finished revision |
| `APPROVED` | `approved` | The owner accepted this version | `set_status` |
| `CHANGES` | `changes` | Feedback is saved and a round is open for the revise stage | `request_changes`, or feedback added while a revision ran |
| `DROPPED` | `dropped` | Left out of the package | `set_status` |

`derive_clip_status` maps a stored value to a state; a missing one is `PENDING`. A value outside the four raises `ValueError`: the file is written only by this module, so an unreadable status is corruption to surface, not a clip to count as pending.

## Moves

`set_status` moves a clip to `PENDING`, `APPROVED` or `DROPPED`. It refuses `CHANGES`, which `request_changes` alone sets because it saves the feedback and opens the round, and it refuses any value outside the set. The revise stage settles a finished round back to `PENDING` under the next version, and to `CHANGES` when feedback arrived while it ran.

## Who reads it

| Reader | Where | Calls |
|---|---|---|
| Clips the revise stage works on | `to_revise` | `derive_clip_status` |
| Status counts, review gate, summary line | `counts`, `review_done`, `summary` | `derive_clip_status` |
| Clips kept in the package | `kept_clips` | `derive_clip_status` |
| Approve, drop and reopen buttons | `_review_controls` in `app/tab_podcast.py` | `ClipStatus` |
| Review CLI actions | `STATUS_OF` in `podcast/review_cli.py` | `ClipStatus` |
| Round settled by a revision | `close` in `podcast/revise.py` | `ClipStatus` |

A new reader calls the module. It does not compare a status string.

## Tests

`tests/podcasting/test_podcast_clip_status.py` holds the stored-value table and the two proofs that `changes` cannot be set without feedback and that a status outside the set is never written.
