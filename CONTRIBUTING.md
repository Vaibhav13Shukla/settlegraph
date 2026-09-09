# Contributing to SettleGraph

Thanks for being here. SettleGraph is a settlement reconciliation engine — it matches
gateway, bank and merchant records into one ledger and refuses to guess when the
evidence is thin. Because it reasons about money, this project holds a harder line on
verification than most, and this document exists so that line is written down instead of
being something you discover in review.

Every kind of contribution counts. If you cannot contribute code, you can still help a
lot by:

- opening an issue when a number in the docs does not reproduce for you,
- improving a confusing explanation in `README.md` or `docs/`,
- running the harnesses on your own hardware and reporting what you measured,
- telling us where the dashboard misled you about what the system actually did.

The last one is worth as much as a patch. A reconciliation tool that quietly overstates
its own accuracy is worse than one that is slow.

---

## Table of contents

- [Code of conduct](#code-of-conduct)
- [I have a question](#i-have-a-question)
- [Getting set up](#getting-set-up)
- [Where everything lives](#where-everything-lives)
- [Reporting a bug](#reporting-a-bug)
- [Suggesting an enhancement](#suggesting-an-enhancement)
- [Your first code contribution](#your-first-code-contribution)
- [The non-negotiables](#the-non-negotiables)
- [Evidence discipline](#evidence-discipline)
- [Style guide](#style-guide)
- [Commit messages](#commit-messages)
- [Opening a pull request](#opening-a-pull-request)
- [Improving the documentation](#improving-the-documentation)
- [Maintainers and support](#maintainers-and-support)
- [Known governance gaps](#known-governance-gaps)

---

## Code of conduct

Be decent. Assume good faith, critique the work and not the person, and accept that
"your measurement contradicts mine" is a normal and useful thing for someone to say.

This project has **not yet adopted a formal code of conduct** — see
[Known governance gaps](#known-governance-gaps). Until it does, report anything you
would want a maintainer to know about by opening an issue, or privately via GitHub to
[@Vaibhav13Shukla](https://github.com/Vaibhav13Shukla).

## I have a question

Before opening an issue for a question, please check:

1. **[`README.md`](README.md)** — what the system does and the headline numbers.
2. **[`docs/EVALUATION.md`](docs/EVALUATION.md)** — every metric, how it was measured,
   and the exact command that reproduces it.
3. **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — how the pipeline fits together.
4. **[`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md)** — the shape of every input CSV.
5. **[`docs/adr/`](docs/adr/)** — why a given design decision was made, and what was
   rejected.
6. Existing [issues](https://github.com/Vaibhav13Shukla/settlegraph/issues), including
   closed ones.

If none of those answer it, open an issue and include your OS, your Python version
(`python --version`), and what you were trying to do.

## Getting set up

Python **3.11 or newer** is required. CI's test matrix
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs on 3.11, 3.12 and 3.13,
across Ubuntu, Windows and macOS.

```bash
git clone https://github.com/Vaibhav13Shukla/settlegraph.git
cd settlegraph

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

make install                       # python -m pip install -e ".[dev]"
```

Confirm the checkout is healthy before you change anything:

```bash
make test                          # pytest -v --basetemp .pytest-tmp
make lint                          # ruff check src tests scripts datagen api
```

`make test` should be fully green. If it is not on a clean checkout, that is itself a
bug worth reporting — say so in an issue rather than working around it.

To see the thing run end to end:

```bash
make generate                      # synthesise a 1,000-record batch (seed 42)
make run                           # reconcile it into results/
make serve                         # dashboard at http://127.0.0.1:8080
```

Other useful targets: `make benchmark` (compare against three naive matchers),
`make simulate` (failure containment scenarios), `make e2e`
(`python scripts/run_e2e.py`), `make format`, `make clean`.

## Where everything lives

| Path | What it is |
| :--- | :--- |
| `src/settlegraph/engine/` | The pipeline: ingest → normalize → match → score → assign → verify → evaluate |
| `src/settlegraph/models/` | Pydantic record schemas, one per source |
| `src/settlegraph/web/index.html` | The dashboard (self-contained: no build step, no CDN) |
| `src/settlegraph/cli.py` | The `settlegraph` command |
| `src/settlegraph/server.py` | The HTTP API the dashboard reads |
| `tests/` | 33 test modules; `make test` runs all of them |
| `scripts/` | Evaluation harnesses — see below |
| `datagen/` | Synthetic batch generator, including the anomaly injection |
| `docs/` | Architecture, evaluation methodology, data contracts, red team, ADRs |
| `DEVLOG.md` | The running record of what was tried, measured, and withdrawn |
| `AGENTS.md` | Engineering standards this repo holds itself to |

The harnesses in `scripts/` are standalone and meant to be run by hand and read by a
human — they are not part of `make test`:

- `noise_sweep.py` — vary data quality, watch abstention rise and precision hold
- `chaos_batch.py` — inflict structural damage on the CSVs and find the breaking point
- `eval_holdout.py` — re-score on a seed the thresholds were never tuned against
- `stress_test.py` — throughput at 20,000 records
- `threshold_study.py` — whether the current thresholds are the right ones

## Reporting a bug

**Before you open the issue**, please:

- Pull the latest `master` and confirm it still happens.
- Run `make test` and note whether it passes.
- Search [existing issues](https://github.com/Vaibhav13Shukla/settlegraph/issues).
- Check whether the behaviour is already recorded as a known limit — `README.md` §8
  ("What This System Cannot Safely Do") and `docs/RED_TEAM.md` deliberately list things
  that do not work.

**A good bug report includes:**

- What you expected and what actually happened, stated separately.
- The exact command you ran, and the seed if a batch was involved. Runs are
  deterministic, so a seed usually makes a bug reproducible on our side too.
- The full traceback, as text rather than a screenshot.
- Your OS and `python --version`.
- Relevant files from `results/` — `summary.json`, `evaluation.json`, and
  `quarantine.json` if rows were held out.

**Please do not paste real settlement data.** Everything this project needs to reproduce
a bug can be generated synthetically. If a bug only appears on production-shaped data,
describe the shape (field lengths, encodings, null patterns) rather than sending records.

Security-sensitive reports: do not open a public issue. Contact
[@Vaibhav13Shukla](https://github.com/Vaibhav13Shukla) privately.

## Suggesting an enhancement

Check `docs/PRD.md` and the ADRs first — some obvious-looking ideas were considered and
rejected on purpose, and the reasoning is written down.

A good enhancement issue explains **the problem before the solution**: who is blocked,
what they cannot currently do, and what the batch would look like afterwards. If the
change affects matching behaviour, say how you would prove it did not cost precision.

## Your first code contribution

Good places to start, in rough order of difficulty:

1. **Documentation that does not reproduce.** Run a command from `docs/EVALUATION.md`
   and check the number. Mismatches are real bugs — in the doc or the code.
2. **Anomaly labels in the dashboard.** `renderAnomalyBreakdown` in
   `src/settlegraph/web/index.html` maps anomaly types to human descriptions; new types
   fall back to a description derived from the counts. Filling one in properly is a
   contained change.
3. **Row-level ingest hardening.** `_read_csv` in `src/settlegraph/engine/ingest.py`
   opens files as UTF-8 and quarantines only `ValidationError`, so a non-UTF-8 export or
   a `csv.Error` still aborts a whole file. This is documented as a known limit and is a
   genuinely useful fix — but it needs a harness that exercises it, not just a
   `try`/`except`.
4. **Exception root-cause categories.** `engine/exceptions.py` diagnoses why a record
   could not be matched. New categories need a test proving the diagnosis is right.

## The non-negotiables

These are asserted in CI by
[`.github/workflows/evaluation.yml`](.github/workflows/evaluation.yml) on a freshly
generated batch. A pull request that breaks any of them will not be merged, however
appealing the rest of it is:

| Gate | Required |
| :--- | :--- |
| `precision` | exactly `1.0` |
| `false_positives` | exactly `0` |
| `false_auto_book_rate` | exactly `0.0` |
| `recall` | `>= 0.75` |
| `dangerous_miss_rate` | exactly `0.0` |
| `exception_recall` | `>= 0.90` |
| `invariant_violations` | exactly `0` |

The asymmetry is deliberate. A missed match costs a human five minutes of review. A
**false** match silently books money against the wrong counterparty and may never be
caught. Recall is a target; precision is a floor.

Two rules follow from that, and they are the ones most likely to trip up an otherwise
good patch:

- **Abstaining is a correct outcome.** If your change converts abstentions into matches
  and recall goes up, that is only an improvement if precision stayed at 1.0 and the
  invariant gate still runs. Do not treat `LIKELY_MATCH` as a bug to be optimised away.
- **The AI layer may never widen the gate.** `engine/ai_reasoner.py` proposes
  hypotheses for records the deterministic matcher already failed on. Every hypothesis
  goes back through the same invariant check, and a promotion is labelled
  `AI_RESOLVED_MATCH` — never `AUTO_MATCH`. Every failure path (timeout, refusal,
  malformed response, invariant breach) must leave the record exactly where the
  deterministic layer put it.

## Evidence discipline

This is the house rule, and it is not negotiable either.

**Do not state a number you did not measure.** If you add a claim to the README, a
docstring, the dashboard, or a PR description, either the command that produces it must
be in the repo, or the claim does not go in. `docs/EVALUATION.md` names the reproduce
command for every figure it quotes; keep it that way.

When a measurement contradicts something the project already claims, **change the claim,
not the measurement.** The git history has several commits that do exactly this — a
threshold study that found no material gain, an abstention metric that was manufacturing
a false weakness, an overstated idempotency finding that was withdrawn. That is the
expected outcome of honest measurement, not an embarrassment.

Two practical consequences:

- The dashboard must never render a hardcoded verdict. An unknown value shows `--`, not
  `0` and not a checkmark; "we did not measure this" and "this passed" are different
  states and must look different.
- If you cannot prove something, write down that you could not. `README.md` §8 and
  `DEVLOG.md` exist for exactly this, and adding to them is a contribution.

## Style guide

Python is formatted and linted with **ruff** (config in `pyproject.toml`: line length
100, target `py311`, rules `E`, `F`, `I`, `W`). Run before pushing:

```bash
make format                        # ruff format src tests scripts datagen api
make lint                          # ruff check src tests scripts datagen api
```

CI runs `ruff format --check`, so unformatted code fails the build.

Beyond the linter:

- **Match the surrounding code.** This codebase uses long explanatory docstrings on
  non-obvious functions, stating not just what the code does but what it used to do
  wrong and why the current shape is correct. If you change such a function, update its
  docstring — a stale explanation is worse than none.
- **Type-hint public functions.** Pydantic models carry the data contracts; keep them
  authoritative.
- **Prefer a boring fix.** Smallest change that actually solves the problem, no
  speculative abstraction.
- **The dashboard has no build step.** `src/settlegraph/web/index.html` is a single
  self-contained file with hand-written CSS and no bundler, no framework and no CDN
  scripts. Please keep it that way; add design tokens to the `:root` block rather than
  new one-off colours.

## Commit messages

Format: `type(scope): what is now true`

```
fix(calibration): correct an abstention metric that manufactured a false weakness
feat(evaluate): score the two reconciliation legs that were never measured
test(idempotency): prove re-sent events are held, and withdraw an overstated finding
docs: honest red team, corrected chaos results, tightened README
```

Types in use: `feat`, `fix`, `test`, `docs`, `chore`. The scope is the module or area.

Note the convention in the subject line: it describes **the effect of the change**, not
the activity. "prove re-sent events are held" rather than "update idempotency tests".
Lowercase, no trailing period. There is no hard length limit in this project's history —
subjects run anywhere from about 60 to 120 characters — but a subject that needs more
than one line is a sign the commit is doing two things; split it or move detail to the
body.

## Opening a pull request

1. Branch from `master`. Do not commit to `master` directly.
2. Make the change, with tests.
3. Run the full local gate:
   ```bash
   make format-check && make lint && make test && make e2e
   ```
4. If you touched anything in `engine/`, also run the pipeline and check the gates in
   [The non-negotiables](#the-non-negotiables) still hold:
   ```bash
   make generate && make run
   ```
5. Open the PR. In the description, state:
   - what problem it solves,
   - **how you verified it** — the command and its actual output, not "tested locally",
   - what you deliberately did **not** change,
   - any claim in the docs your change makes stale.

A PR that says "tests pass" without saying which ones you ran will be asked for the
output. This is not distrust; the whole project rests on the difference between
"implemented" and "verified".

## Improving the documentation

Documentation changes are held to the same evidence rule as code. If you correct a
number, include the command you ran and its output in the PR description so the next
person can re-run it.

`DEVLOG.md` is append-only in spirit: it records findings **as they stood at the time**.
When something in it is later fixed, add a dated follow-up beneath the original entry
rather than editing history — the arc from "we found this" to "we closed it" is the
point of the file.

## Maintainers and support

Maintained by [@Vaibhav13Shukla](https://github.com/Vaibhav13Shukla).

This is a Razorpay Buildathon (Track 04) submission maintained by one person, so there
is **no response-time commitment**. Issues and PRs are read, but a reply may take a
while. If something is urgent for you, say so in the issue.

[`release.yml`](.github/workflows/release.yml) generates release notes automatically
from merged pull requests on tag push, which credits every merged PR's author by
GitHub handle. That mechanism does not extend to issue reports with no attached PR —
if you filed the report but someone else wrote the fix, say so in the PR description
and it will be called out by hand.

## Known governance gaps

Stated plainly rather than left for you to discover:

- **There is no `LICENSE` file, and `pyproject.toml` declares no license.** Until one is
  added, the code is under exclusive copyright by default and there are no granted terms
  for reuse or redistribution. If you are considering a substantial contribution, please
  open an issue and ask about licensing first — neither side should invest work with the
  terms unresolved.
- **There is no `CODE_OF_CONDUCT.md`.** Adopting the Contributor Covenant would be a
  welcome first contribution; it needs a real contact address, which is a maintainer
  decision rather than something to copy from a template.

Issue and pull request templates *do* exist — [`bug_report.yml`](.github/ISSUE_TEMPLATE/bug_report.yml),
[`enhancement.yml`](.github/ISSUE_TEMPLATE/enhancement.yml), and
[`pull_request_template.md`](.github/pull_request_template.md). They ask for exactly what
this document asks for, so filling them in honestly is the fastest route through review.
