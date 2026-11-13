# Engagement comment status

Each row of the `comments` table has one review status. `tools/engagement/db/comment_status.py` is the only module that names the states and maps a stored string to one, and `update_comment_status` writes only through it.

## Why it is declared

The status was five bare strings spelled by the scraper cascades, the rule classifier, the review app, the UI and the live-app check, and the one writer took any string: a typo stored a status no filter, count or badge knew, and the row dropped out of the review queue. One declared type makes a new reader call the module instead of spelling a status.

## The states

The string values are the ones `schema.sql` documents for the column, so they never change.

| `CommentStatus` | Value | Meaning |
|---|---|---|
| `PENDING` | `pending` | Waiting for a decision; also where a surfaced or reopened comment returns |
| `APPROVED` | `approved` | The owner (or the blacklist cascade) accepted the suggested action |
| `SENT` | `sent` | The reply went out |
| `REJECTED` | `rejected` | The owner declined the suggestion |
| `IGNORED` | `ignored` | Not worth a decision, e.g. already replied to |
| `UNKNOWN` | (never stored) | The stored value names none of the above |

`derive_comment_status` maps a stored value to a state; a missing or unrecognised one is `UNKNOWN`, never `PENDING`, so an unreadable row does not join the decision queue. The UI still shows its raw value as the row badge.

## Moves

`update_comment_status` stores a state through `writable_status`, which raises `ValueError` for `UNKNOWN` and for any value outside the set before the Supabase client is touched. The cascades filter on `pending` and write `approved` or `ignored` through the same enum.

## Who reads it

| Reader | Where | Calls |
|---|---|---|
| Review writes (approve, reject, ignore, surface) | `tools/engagement/ui.py` | `update_comment_status` with `CommentStatus` |
| Status counts and the pending/all view | `tools/engagement/ui.py` | `STORED_STATUSES`, `CommentStatus` |
| Row badge | `_render_comment_card` in `tools/engagement/ui.py` | `derive_comment_status` |
| Cascades and ignore-replied | `tools/engagement/db/client.py` | `CommentStatus` |
| Pending fetch and blacklist auto-approve | `tools/engagement/classify/rules.py` | `CommentStatus` |
| Live-app check | `tools/engagement/check_review_app.py` | `CommentStatus` |

A new reader calls the module. It does not spell a status string.

## Tests

`tests/linkedin/test_engagement_comment_status.py` holds the stored-value table, pins the values to `schema.sql`, and proves a status outside the set is never written.
