"""Settlement Q&A Agent: ask questions about a completed reconciliation batch.

Built on the real ``claude-agent-sdk`` (not the plain ``anthropic`` package
``engine/ai_reasoner.py`` uses) -- deliberately, and only here. See
``docs/adr/0006-pdf-compliance.md`` for the full reasoning; the short version:

    ai_reasoner.py proposes match hypotheses. It is one step from a ledger
    entry, has zero tools, and stays that way -- giving it tool access would
    be a safety regression, not an upgrade, whatever a tech-stack slide says.

    This module answers questions about a batch that already finished
    running. It needs genuinely iterative, multi-turn tool use ("chaining
    queries based on previous results" -- the PDF's own words for what an
    AI-reasoning layer should eventually do) precisely because a wrong
    *answer* here costs a reviewer some time, not a rupee. That is exactly
    the gap Agent SDK's tool-calling loop is for.

Every tool this agent can call is a pure, read-only function over files
``run_pipeline`` already wrote to ``results/``. None of them write anything.
None of them import or call anything from ``engine.pipeline``,
``engine.assign``, or ``engine.verify``. There is no tool in this agent's
allow-list that could move a record from EXCEPTION to AUTO_MATCH, so a fully
subverted agent can hand a reviewer a wrong explanation -- it cannot touch
the ledger. Every other tool category the CLI ships with (Bash, file writes,
web access) is explicitly disabled, twice over (empty ``tools`` preset and
an explicit ``disallowed_tools`` list), because relying on a single
restriction to hold is exactly the failure mode the rest of this codebase
refuses to accept from its money path.

    pip install -e ".[llm]"
    # Needs the `claude` CLI on PATH (it spawns it as a subprocess) and
    # either ANTHROPIC_API_KEY or an already-authenticated `claude` session.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

DANGEROUS_BUILTIN_TOOLS = [
    "Bash",
    "BashOutput",
    "KillShell",
    "Write",
    "Edit",
    "NotebookEdit",
    "WebSearch",
    "WebFetch",
    "Task",
]

SYSTEM_PROMPT = """You answer questions about one already-completed \
settlement reconciliation batch, for the person who has to act on it.

You have exactly five tools, all read-only, all scoped to this batch's own
results directory. You cannot move money, edit any record, re-run the
pipeline, or reach outside these tools -- there is nothing else available to
you, this is not a permission you are being asked to respect, it is the
complete list of what you can do.

Rules:
- Cite the record id(s) or numbers your answer rests on. An answer with no
  citation is a guess, and you should say so rather than presenting a guess
  as fact.
- If a record id doesn't turn up in any tool result, say that plainly.
  Don't infer what it probably would have said.
- "Why wasn't X matched" questions should look at get_exception(X) for the
  diagnosed root cause before speculating.
- You're explaining a decision that was already made by deterministic code
  (or, for AI_RESOLVED_MATCH rows, a hypothesis that already passed the
  same deterministic invariant gate). You are not re-deciding anything."""


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]}


def get_summary_impl(results_dir: Path) -> dict[str, Any]:
    """Batch-level counts, revenue assurance, forward cash position, drift,
    throughput -- everything `run_pipeline` wrote to `summary.json`."""
    data = _read_json(results_dir / "summary.json")
    if data is None:
        return _text_result({"error": "summary.json not found -- has the pipeline been run?"})
    return _text_result(data)


def get_assignment_impl(results_dir: Path, record_id: str) -> dict[str, Any]:
    """Find every assignment row (from either side of a match) touching a
    given record id, from assignments.csv."""
    rows = _read_csv_rows(results_dir / "assignments.csv")
    matches = [
        r for r in rows if r.get("source_a_id") == record_id or r.get("source_b_id") == record_id
    ]
    if not matches:
        unmatched = _read_csv_rows(results_dir / "unmatched.csv")
        orphan = [r for r in unmatched if r.get("record_id") == record_id]
        if orphan:
            return _text_result({"status": "unmatched", "record": orphan[0]})
        return _text_result({"status": "not_found", "record_id": record_id})
    return _text_result({"status": "matched", "assignments": matches})


def get_exception_impl(results_dir: Path, record_id: str) -> dict[str, Any]:
    """The diagnosed root-cause report for a record, and the AI reasoner's
    attempt at it (if config.llm_provider was on for this run and it tried)."""
    exceptions = _read_json(results_dir / "exceptions.json") or []
    matches = [e for e in exceptions if e.get("record_id") == record_id]
    ai_log = _read_json(results_dir / "ai_resolutions.json") or []
    ai_attempts = [e for e in ai_log if e.get("record_id") == record_id]
    if not matches and not ai_attempts:
        return _text_result({"status": "no_exception_on_record", "record_id": record_id})
    return _text_result({"exception_reports": matches, "ai_reasoner_attempts": ai_attempts})


def search_by_amount_or_utr_impl(results_dir: Path, query: str) -> dict[str, Any]:
    """Free-text search over assignments and unmatched records for a UTR
    fragment, order id, or amount (in paise) substring."""
    rows = _read_csv_rows(results_dir / "assignments.csv")
    unmatched = _read_csv_rows(results_dir / "unmatched.csv")
    q = query.strip()

    def _hit(row: dict[str, str]) -> bool:
        return any(q and q in str(v) for v in row.values())

    hits = [r for r in rows if _hit(r)][:20]
    unmatched_hits = [r for r in unmatched if _hit(r)][:20]
    return _text_result(
        {
            "query": query,
            "assignment_matches": hits,
            "unmatched_matches": unmatched_hits,
            "note": "Results capped at 20 rows each.",
        }
    )


def get_revenue_assurance_impl(results_dir: Path) -> dict[str, Any]:
    """Financial totals, exception-category exposure, and the forward cash
    position for this batch."""
    data = _read_json(results_dir / "revenue_assurance.json")
    if data is None:
        return _text_result({"error": "revenue_assurance.json not found."})
    return _text_result(data)


def build_ledger_tools(results_dir: Path) -> list[Any]:
    """Bind the five read-only tools to one batch's results directory.

    Imported lazily so importing this module doesn't require
    claude-agent-sdk to be installed unless a caller actually builds an
    agent -- mirrors how ai_reasoner.py only imports `anthropic` inside
    ClaudeReasoner.__init__.
    """
    from claude_agent_sdk import tool

    @tool("get_summary", "Batch-level counts, metrics, drift, throughput for this run.", {})
    async def get_summary(_args: dict[str, Any]) -> dict[str, Any]:
        return get_summary_impl(results_dir)

    @tool(
        "get_assignment",
        "Look up every assignment row touching a given record id (rzp_norm_*, bank_norm_*, merch_norm_*).",
        {"record_id": str},
    )
    async def get_assignment(args: dict[str, Any]) -> dict[str, Any]:
        return get_assignment_impl(results_dir, args["record_id"])

    @tool(
        "get_exception",
        "The diagnosed root-cause report for a record, plus any AI-reasoner attempt at it.",
        {"record_id": str},
    )
    async def get_exception(args: dict[str, Any]) -> dict[str, Any]:
        return get_exception_impl(results_dir, args["record_id"])

    @tool(
        "search_by_amount_or_utr",
        "Free-text search over assignments and unmatched records (UTR fragment, order id, or amount in paise).",
        {"query": str},
    )
    async def search_by_amount_or_utr(args: dict[str, Any]) -> dict[str, Any]:
        return search_by_amount_or_utr_impl(results_dir, args["query"])

    @tool(
        "get_revenue_assurance",
        "Financial totals, exception exposure, and forward cash position for this batch.",
        {},
    )
    async def get_revenue_assurance(_args: dict[str, Any]) -> dict[str, Any]:
        return get_revenue_assurance_impl(results_dir)

    return [
        get_summary,
        get_assignment,
        get_exception,
        search_by_amount_or_utr,
        get_revenue_assurance,
    ]


def build_options(results_dir: Path):
    """Assemble the locked-down ClaudeAgentOptions for the Q&A agent.

    Two independent restrictions, not one: an empty built-in `tools` preset
    *and* an explicit `disallowed_tools` list naming the dangerous ones by
    name. Either alone should be enough; both together is the same
    "don't trust a single control" posture the rest of this codebase
    applies to money, applied here to blast radius instead.
    """
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server

    tools = build_ledger_tools(results_dir)
    server = create_sdk_mcp_server(name="ledger", tools=tools)
    allowed = [f"mcp__ledger__{t.name}" for t in tools]

    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"ledger": server},
        tools=[],
        allowed_tools=allowed,
        disallowed_tools=DANGEROUS_BUILTIN_TOOLS,
        permission_mode="default",
        max_turns=8,
    )


async def ask(question: str, results_dir: Path | str = "results") -> str:
    """Ask one question, return the final text answer.

    Streams the underlying `query()` call and concatenates every
    AssistantMessage text block -- callers that want the raw event stream
    (to show tool calls live, say) should use `claude_agent_sdk.query`
    directly with `build_options`.
    """
    from claude_agent_sdk import AssistantMessage, TextBlock, query

    options = build_options(Path(results_dir))
    parts: list[str] = []
    async for message in query(prompt=question, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts).strip()
