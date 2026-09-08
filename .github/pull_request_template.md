<!--
Thanks for contributing. The sections below are the ones review will actually ask
about — see CONTRIBUTING.md. Delete any that genuinely do not apply, but please do
not delete "How I verified this".
-->

## What problem does this solve?

<!-- The situation before this change. Link the issue if there is one. -->

## What changed

<!-- The shape of the change, not a file-by-file diff — the diff is right there. -->

## How I verified this

<!--
Paste the command AND its actual output. "Tested locally" will be sent back.
If a check did not pass, say so here rather than leaving it out.
-->

```
$ make format-check && make lint && make test

```

- [ ] `make test` is green
- [ ] `make lint` and `make format-check` are clean
- [ ] `make e2e` passes (if this touches the pipeline or the server)

**If this touches `src/settlegraph/engine/`**, the safety gates were re-checked after
`make generate && make run`:

| Gate | Required | Measured |
| :--- | :--- | :--- |
| `precision` | `== 1.0` | |
| `false_positives` | `== 0` | |
| `false_auto_book_rate` | `== 0.0` | |
| `recall` | `>= 0.75` | |
| `dangerous_miss_rate` | `== 0.0` | |
| `exception_recall` | `>= 0.90` | |
| `invariant_violations` | `== 0` | |

<!--
Recall is a target; precision is a floor. A change that raises recall by converting
abstentions into matches is only an improvement if precision stayed at 1.0.
-->

## What I deliberately did NOT change

<!-- Scope you left alone on purpose, and why. This is genuinely useful to a reviewer. -->

## Claims this makes stale

<!--
Any number or statement in README.md, docs/, a docstring, or the dashboard that is no
longer true after this change. "None" is a fine answer — but please check before
writing it. If you measured something that contradicts an existing claim, correct the
claim in this PR rather than leaving the contradiction.
-->

## Remaining risks

<!-- What could still break, what is untested, what you are unsure about. -->
