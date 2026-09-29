"""Deep research: plan -> search & read -> find the gaps -> fill them -> a cited report.

The GPT Researcher / open_deep_research loop on Sarah's own tools: the
question is split into focused sub-questions, each researched in parallel
(search + read the pages + keep the relevant passages), the notes are
checked for gaps once and those are researched too, then a report is
written that cites its sources as [n]. About 3 free model requests; pages
are read locally. The report can be saved as a real document.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("sarah.agency")

Complete = Callable[[str], Awaitable[str]]

PLAN = (
    "You are planning web research. Question: {q}\n"
    "Write {n} focused web search queries that together cover what's needed to answer it well "
    "(different angles: facts, recent developments, comparisons, numbers, expert opinion). "
    'Reply with JSON only: {{"queries": ["...", "..."]}}'
)
GAPS = (
    "Question: {q}\nNotes gathered so far (by source):\n{notes}\n\n"
    "What important parts of the question are still unanswered or weakly supported? Give up to {n} new "
    "search queries to fill those gaps, or none if the notes are enough. "
    'Reply with JSON only: {{"queries": ["..."]}}'
)
REPORT = (
    "Write a research report answering: {q}\n\n"
    "Use only these numbered sources; cite them inline like [2] or [1][4] for every claim that comes from "
    "them. Structure: a 2-3 sentence answer first, then sections with ## headings, then 'Open questions' if "
    "anything stayed unclear. Plain, specific, no filler; say where sources disagree. Markdown. Do not add "
    "a source list (it's appended for you). Put the finished report between <report> and </report>, with "
    "nothing else inside the markers.\n\nSources:\n{sources}"
)

_PLANNING = re.compile(r"^(the user|i need|i should|i'll|i will|let me|okay|ok,|first,|key requirements|from sources|"
                       r"now,|so,|alright|analy[sz])", re.I)


def clean_report(text: str) -> str:
    """The report itself, without a free model's thinking out loud around it."""
    text = (text or "").strip()
    m = re.search(r"<report>(.*?)(?:</report>|$)", text, re.S | re.I)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # No markers: drop leading planning paragraphs ("The user wants...", "Let me...").
    paras = re.split(r"\n\s*\n", text)
    while len(paras) > 1 and _PLANNING.match(paras[0].strip().lstrip("#* ")):
        paras.pop(0)
    first_heading = next((i for i, p in enumerate(paras) if p.lstrip().startswith("#")), None)
    if first_heading and any(_PLANNING.match(p.strip()) for p in paras[:first_heading]):
        paras = paras[first_heading:]
    return "\n\n".join(paras).strip()


def _queries(text: str, limit: int) -> List[str]:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    out = [str(q).strip() for q in data.get("queries", []) if str(q).strip()]
    if not out:  # a plain list of lines as a fallback
        out = [l.strip(" -*0123456789.)\"") for l in (text or "").splitlines() if len(l.strip()) > 8][:limit]
    return out[:limit]


async def _default_complete(prompt: str) -> str:
    from backend.state import get_sarah
    from backend.usage import using

    client = getattr(get_sarah(), "_openrouter", None)
    if client is None:
        raise RuntimeError("no language model available")
    with using("research"):
        return await client.simple_completion(prompt, max_tokens=2200, temperature=0.3)


async def run(question: str, depth: int = 2, breadth: int = 4, complete: Optional[Complete] = None,
              research: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    if research is None:
        from .tools import research as research_tool
        research = research_tool
    complete = complete or _default_complete
    breadth = max(2, min(6, int(breadth or 4)))
    sources: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}

    async def gather(queries: List[str]) -> None:
        results = await asyncio.gather(*(research(question=q, sources=3) for q in queries), return_exceptions=True)
        for res in results:
            if isinstance(res, Exception) or not isinstance(res, dict):
                continue
            for s in res.get("sources", []):
                url = s.get("url")
                passages = [p for p in s.get("passages", []) if p]
                if not url or not passages:
                    continue
                if url in seen:
                    sources[seen[url]]["passages"] += [p for p in passages if p not in sources[seen[url]]["passages"]]
                else:
                    seen[url] = len(sources)
                    sources.append({"title": s.get("title") or url, "url": url, "passages": passages})

    queries = _queries(await complete(PLAN.format(q=question, n=breadth)), breadth) or [question]
    rounds = [queries]
    await gather(queries)
    if int(depth or 1) >= 2 and sources:
        notes = "\n".join(f"- {s['title']}: {s['passages'][0][:220]}" for s in sources[:16])
        more = _queries(await complete(GAPS.format(q=question, notes=notes, n=3)), 3)
        more = [m for m in more if m.lower() not in {q.lower() for q in queries}]
        if more:
            rounds.append(more)
            await gather(more)
    if not sources:
        return {"question": question, "report": "I couldn't find readable sources for that.", "sources": [],
                "queries": rounds}

    # Most relevant sources first, capped so the report prompt stays small.
    budget, blocks = 14000, []
    for i, s in enumerate(sources[:18], 1):
        text = " ".join(s["passages"])[:1400]
        if budget - len(text) < 0:
            break
        budget -= len(text)
        blocks.append(f"[{i}] {s['title']} ({s['url']})\n{text}")
    report = clean_report(await complete(REPORT.format(q=question, sources="\n\n".join(blocks))))
    used = sources[: len(blocks)]
    report += "\n\n## Sources\n" + "\n".join(f"{i}. [{s['title']}]({s['url']})" for i, s in enumerate(used, 1))
    return {"question": question, "report": report, "queries": rounds,
            "sources": [{"n": i, "title": s["title"], "url": s["url"]} for i, s in enumerate(used, 1)]}
