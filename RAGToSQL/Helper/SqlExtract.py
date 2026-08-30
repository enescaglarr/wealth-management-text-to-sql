"""
Robust SQL extraction from LLM answers.

Vanna's built-in `extract_sql` matches `SELECT.*?;` first and uses greedy markdown matching. On
reasoning models (gpt-oss, qwen) the chain-of-thought is full of half-written SQL, so that regex
happily returns prose glued to a query, which then fails to execute.

Strategy here: strip the reasoning, enumerate every plausible candidate (fenced code blocks first,
then each SELECT/WITH position, latest first) and return the first candidate that actually parses
as a single SELECT. Parsing is the acceptance test, so prose can never be mistaken for SQL.
"""
import re

import sqlglot
from sqlglot import exp

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)(?:```|\Z)", re.S | re.I)
_START = re.compile(r"(?:^|[\s(;])(WITH|SELECT)\b", re.I)
_PROSE = re.compile(r"^\s*(check|note|let'?s|wait|the query|this |i will|i'?ll|done|output|match|proceed|final|so |but |also|however)", re.I)


def _strip_reasoning(text: str) -> str:
    if "</think>" in text.lower():
        text = re.split(r"</think>", text, flags=re.I)[-1]
    return re.sub(r"<think>.*?(?:</think>|\Z)", " ", text, flags=re.S | re.I)


def _trim(candidate: str) -> str:
    candidate = candidate.split("```")[0]
    candidate = re.split(r"\n\s*(?:SQLResult|Answer|Note|Explanation)\s*:", candidate, maxsplit=1, flags=re.I)[0]
    candidate = candidate.split(";")[0]
    lines = []
    for line in candidate.splitlines():
        if lines and _PROSE.match(line):
            break
        lines.append(line)
    return "\n".join(lines).strip().rstrip(";").strip()


def _is_select(sql: str) -> bool:
    try:
        tree = sqlglot.parse_one(sql, read="tsql")
    except Exception:
        return False
    return isinstance(tree, (exp.Select, exp.Union))


def _candidates(text: str):
    blocks = [b for b in _FENCE.findall(text) if _START.search(b)]
    for b in reversed(blocks):          # the final fenced block is normally the answer
        yield b
    starts = [m.start(1) for m in _START.finditer(text)]
    for s in reversed(starts):          # otherwise: each SELECT/WITH position, latest first
        yield text[s:]


def extract_sql(text: str) -> str:
    if not text or not text.strip():
        return ""
    text = _strip_reasoning(text)
    text = re.sub(r"^.*?SQLQuery:\s*", "", text, count=1, flags=re.S | re.I)
    first = ""
    for raw in _candidates(text):
        candidate = _trim(raw)
        if not candidate:
            continue
        first = first or candidate
        if _is_select(candidate):       # parsing is the acceptance test
            return candidate
    return first                        # nothing parsed - hand it over so the guard reports why
