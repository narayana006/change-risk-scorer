"""Render a Result as Markdown (for a PR comment), plain text or JSON."""

from __future__ import annotations

import json

from .scoring import MAX_SCORE, Result


def _stats(result: Result) -> str:
    return (f"{result.files} file{'' if result.files == 1 else 's'} changed, "
            f"+{result.added} / -{result.deleted} lines")


def render_markdown(result: Result, base: str, head: str) -> str:
    lines = [
        f"## Change risk: {result.level.upper()} ({result.score}/{MAX_SCORE})",
        "",
        f"`{base}...{head}`: {_stats(result)}. Evaluated {result.evaluated_at:%a %Y-%m-%d %H:%M %Z}.",
        "",
    ]
    if result.reasons:
        lines += ["| Points | Factor | Why |", "|---:|---|---|"]
        lines += [f"| +{r.points} | {r.factor} | {r.detail.replace('|', chr(92) + '|')} |" for r in result.reasons]
    else:
        lines.append("No risk factors found.")
    if result.notes:
        lines.append("")
        lines += [f"- {n}" for n in result.notes]
    return "\n".join(lines) + "\n"


def render_text(result: Result, base: str, head: str) -> str:
    lines = [f"Change risk: {result.level.upper()} ({result.score}/{MAX_SCORE})",
             f"{base}...{head}: {_stats(result)}",
             f"Evaluated {result.evaluated_at:%a %Y-%m-%d %H:%M %Z}", ""]
    lines += [f"  +{r.points:<3} {r.factor}: {r.detail.replace('`', '')}" for r in result.reasons]
    if not result.reasons:
        lines.append("  No risk factors found.")
    lines += [f"  note: {n}" for n in result.notes]
    return "\n".join(lines) + "\n"


def render_json(result: Result, base: str, head: str) -> str:
    return json.dumps({
        "score": result.score,
        "level": result.level,
        "base": base,
        "head": head,
        "files": result.files,
        "added": result.added,
        "deleted": result.deleted,
        "evaluated_at": result.evaluated_at.isoformat(),
        "timing_checked": result.timing_checked,
        "reasons": [{"points": r.points, "factor": r.factor, "detail": r.detail} for r in result.reasons],
        "notes": result.notes,
    }, indent=2) + "\n"


RENDERERS = {"markdown": render_markdown, "text": render_text, "json": render_json}
