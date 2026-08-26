"""Agentic search loop (PRD §6.5.4).

    understand → generate queries → search → scrape top sources → synthesize
    a cited answer → return reasoning steps.

Fully configurable (max_steps, max_sources). Degrades gracefully when no LLM
key is configured: queries become heuristics, synthesis becomes extractive.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

from jiro.ai.llm import LLM, count_tokens
from jiro.config import Settings
from jiro.errors import LLMError
from jiro.models import SearchRequest
from jiro.scraping.engines import SearchOrchestrator

MAX_SNIPPET_CHARS = 600


class Agent:
    def __init__(self, settings: Settings, orchestrator: SearchOrchestrator,
                 scraper: Any, llm: Optional[LLM] = None) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self.scraper = scraper  # callable: scrape_url(url) -> dict
        self.llm = llm or LLM(settings)
        cfg = settings.agent
        self.max_steps = int(cfg.get("max_steps", 5))
        self.max_sources = int(cfg.get("max_sources", 8))
        self.max_snippets = int(cfg.get("max_snippets_per_source", 3))
        self.deadline_seconds = float(cfg.get("deadline_seconds", 0) or 0)
        self.content_budget = int(cfg.get("content_budget_chars", 200000) or 200000)

    async def _llm_complete(self, messages: List[Dict[str, Any]],
                            *, system: Optional[str] = None) -> str:
        """LLM completion guarded by a per-call timeout."""
        timeout = max(10.0, self.settings.timeout * 2)
        try:
            return await asyncio.wait_for(
                self.llm.complete(messages, system=system), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise LLMError("LLM call timed out", details={"timeout": timeout}) from exc

    @staticmethod
    def _deadline_exceeded(deadline_at: Optional[float]) -> bool:
        return deadline_at is not None and time.monotonic() >= deadline_at

    async def research(self, query: str, *, max_sources: Optional[int] = None,
                        provider: Optional[str] = None,
                        model: Optional[str] = None,
                        deadline: Optional[float] = None) -> Dict[str, Any]:
        max_sources = min(max_sources or self.max_sources, 20)
        deadline = deadline or self.deadline_seconds or None
        deadline_at = (time.monotonic() + deadline) if deadline else None
        content_left = self.content_budget
        steps: List[Dict[str, Any]] = []
        used_llm = False
        deadline_exceeded = False

        # 1–2. Understand + generate search queries
        plan = await self._plan_queries(query, provider=provider, model=model,
                                        used_llm_flag=[used_llm])
        used_llm = plan.pop("_used_llm", used_llm)
        query_list = plan["queries"]
        steps.append({"step": "plan", "queries": query_list})

        # 3. Search
        search_results: List[Dict[str, Any]] = []
        seen_links: Dict[str, Dict[str, Any]] = {}
        for q in query_list[: self.max_steps]:
            if self._deadline_exceeded(deadline_at):
                deadline_exceeded = True
                steps.append({"step": "stop", "reason": "deadline_exceeded"})
                break
            try:
                resp = await self.orchestrator.search(
                    SearchRequest(q=q, num=min(10, max_sources), engine="auto")
                )
            except Exception as exc:
                steps.append({"step": "search", "query": q, "status": "failed",
                              "error": str(exc)})
                continue
            search_results.append(resp.model_dump())
            steps.append({"step": "search", "query": q,
                          "results": len(resp.organic_results),
                          "engine": resp.search_metadata.get("engine")})
            for item in resp.organic_results:
                link = item.link
                if link and link not in seen_links:
                    seen_links[link] = {
                        "title": item.title, "url": link, "snippet": item.snippet,
                        "engine": resp.search_metadata.get("engine"),
                    }
            if len(seen_links) >= max_sources * 3:
                break

        # 4. Scrape top sources (limited)
        sources: List[Dict[str, Any]] = []
        candidates = list(seen_links.values())[: max_sources * 2]
        for src in candidates:
            if len(sources) >= max_sources:
                break
            if self._deadline_exceeded(deadline_at):
                deadline_exceeded = True
                steps.append({"step": "stop", "reason": "deadline_exceeded"})
                break
            try:
                page = await self.scraper(src["url"])
                content = (page.get("content") or "")[:3000]
                content = content[: max(0, content_left)]
                content_left -= len(content)
                if not content.strip():
                    raise ValueError("empty content")
                steps.append({"step": "scrape", "url": src["url"], "status": "success"})
                sources.append({
                    "title": page.get("title") or src["title"],
                    "url": src["url"],
                    "snippet": src.get("snippet", ""),
                    "content": content,
                    "scraped": True,
                })
            except Exception as exc:
                steps.append({"step": "scrape", "url": src["url"], "status": "failed",
                              "error": str(exc)})
                # Graceful fallback: keep the snippet-only source so the agent
                # can still cite it even when the page cannot be fetched.
                if src.get("snippet"):
                    sources.append({
                        "title": src["title"],
                        "url": src["url"],
                        "snippet": src.get("snippet", ""),
                        "content": "",
                        "scraped": False,
                    })

        # 5. Synthesize answer
        answer, provider_used, model_used = await self._synthesize(
            query, sources, provider=provider, model=model,
            used_llm_flag=[used_llm],
        )
        steps.append({"step": "synthesize", "sources_used": len(sources),
                      "provider": provider_used})

        citations = [
            {"title": s["title"], "url": s["url"], "snippet": s["snippet"][:200]}
            for s in sources
        ]
        return {
            "answer": answer,
            "citations": citations,
            "search_results": search_results,
            "sources_used": sources,
            "reasoning_steps": steps,
            "provider": provider_used,
            "model": model_used,
            "deadline_exceeded": deadline_exceeded,
        }

    # ------------------------------------------------------------------ plan
    async def _plan_queries(self, query: str, *, provider: Optional[str],
                            model: Optional[str], used_llm_flag: List[bool]) -> Dict[str, Any]:
        """Return up to 3 queries. Optionally uses the LLM to derive them."""
        if self.llm.available:
            try:
                prompt = (
                    f"Given the research question, return 1-3 concise web search queries "
                    f"that would best answer it. Focus on specific, actionable search terms. "
                    f"Avoid generic words like 'best', 'top', 'guide'. "
                    f"Output only the queries, one per line, no numbering.\n\n"
                    f"Question: {query}"
                )
                text = await self._llm_complete(
                    [{"role": "user", "content": prompt}]
                )
                queries = [ln.strip(" -•0123456789.)\t") for ln in text.splitlines()
                           if ln.strip()]
                queries = [q for q in queries if 2 < len(q) < 120][:3]
                if queries:
                    used_llm_flag[0] = True
                    return {"queries": queries, "_used_llm": True}
            except LLMError:
                pass
        # Heuristic: extract the core topic from the question and generate
        # specific search queries that search engines handle well.
        stopwords = {
            "what", "who", "where", "when", "why", "how", "is", "are", "the",
            "a", "an", "of", "for", "in", "on", "best", "top", "good", "great",
            "which", "this", "that", "with", "from", "about", "into", "most",
            "popular", "use", "using", "used", "library", "libraries",
            "tool", "tools", "framework", "frameworks",
        }
        words = re.findall(r"[A-Za-z][A-Za-z\-]+", query)
        meaningful = [w for w in words if w.lower() not in stopwords]
        if not meaningful:
            meaningful = [w for w in words if w.lower() not in {
                "what", "who", "where", "when", "why", "how", "is", "are", "the",
            }]

        topic = " ".join(meaningful[:5]) if meaningful else query
        # Generate queries that search engines handle well
        queries = [
            f"{topic} compared",          # comparison results
            f"{topic} recommended",        # recommendation results
            f"{topic} 2026",              # recent results
        ]
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for q in queries:
            ql = q.lower().strip()
            if ql not in seen and len(ql) > 5:
                seen.add(ql)
                unique.append(q)
        return {"queries": unique[:3], "_used_llm": False}

    # ------------------------------------------------------------ synthesize
    async def _synthesize(self, query: str, sources: List[Dict[str, Any]], *,
                          provider: Optional[str], model: Optional[str],
                          used_llm_flag: List[bool]) -> tuple:
        if self.llm.available:
            try:
                context = self._build_context(sources)
                system = ("You are Jiro, a precise research assistant. Answer the "
                          "question using ONLY the web excerpts below. Use numbered "
                          "citations like [1], [2] referring to the source list. Say "
                          "when sources are insufficient. Be concise and factual.")
                user = (f"Question: {query}\n\n"
                        f"Sources:\n{context}\n\n"
                        f"Answer with citations [n].")
                answer = await self._llm_complete(
                    [{"role": "user", "content": user}], system=system
                )
                used_llm_flag[0] = True
                return answer, self.llm.provider_name, self.llm.model
            except LLMError:
                pass
        return (LLM.synthesize_without_llm(query, sources),
                "extractive-fallback", None)

    @staticmethod
    def _build_context(sources: List[Dict[str, Any]]) -> str:
        blocks = []
        for i, src in enumerate(sources, start=1):
            snippet = re.sub(r"\s+", " ", src.get("snippet") or "")[:400]
            content = re.sub(r"\s+", " ", src.get("content") or "")[:MAX_SNIPPET_CHARS]
            blocks.append(f"[{i}] {src['title']}\nURL: {src['url']}\n"
                          f"Snippet: {snippet}\nExcerpt: {content}")
        return "\n\n".join(blocks)

    # ------------------------------------------------------- multi-step agent
    async def run_agent(self, goal: str, *, max_steps: int = 5,
                        max_sources: int = 8, max_sources_per_step: int = 3,
                        refine: bool = True, provider: Optional[str] = None,
                        model: Optional[str] = None,
                        deadline: Optional[float] = None) -> Dict[str, Any]:
        """Autonomous multi-step research (PRD Phase 3 /ai/agent)."""
        max_steps = min(max_steps, 20)
        deadline = deadline or self.deadline_seconds or None
        deadline_at = (time.monotonic() + deadline) if deadline else None
        content_left = self.content_budget
        steps: List[Dict[str, Any]] = []
        sources: List[Dict[str, Any]] = []
        search_results: List[Dict[str, Any]] = []
        current_query = goal
        used_llm = False
        deadline_exceeded = False

        steps.append({"step": "plan", "goal": goal,
                      "max_steps": max_steps, "refine": refine})

        for step_num in range(1, max_steps + 1):
            if self._deadline_exceeded(deadline_at):
                deadline_exceeded = True
                steps.append({"step": "stop", "reason": "deadline_exceeded"})
                break
            # --- search
            try:
                resp = await self.orchestrator.search(
                    SearchRequest(q=current_query, num=min(10, max_sources_per_step * 3),
                                  engine="auto")
                )
            except Exception as exc:
                steps.append({"step": "search", "query": current_query,
                              "status": "failed", "error": str(exc)})
                break
            search_results.append(resp.model_dump())
            steps.append({"step": "search", "iteration": step_num,
                          "query": current_query,
                          "results": len(resp.organic_results),
                          "engine": resp.search_metadata.get("engine")})

            # --- scrape top candidates from this step
            candidates = [{"title": o.title, "url": o.link, "snippet": o.snippet}
                          for o in resp.organic_results
                          if o.link and o.link not in {s["url"] for s in sources}]
            step_sources = 0
            for cand in candidates[: max_sources_per_step]:
                if len(sources) >= max_sources:
                    break
                try:
                    page = await self.scraper(cand["url"])
                    content = (page.get("content") or "")[:2500]
                    content = content[: max(0, content_left)]
                    content_left -= len(content)
                    if not content.strip():
                        raise ValueError("empty content")
                    steps.append({"step": "scrape", "iteration": step_num,
                                  "url": cand["url"], "status": "success"})
                    sources.append({
                        "title": page.get("title") or cand["title"],
                        "url": cand["url"],
                        "snippet": cand.get("snippet", ""),
                        "content": content,
                    })
                    step_sources += 1
                except Exception as exc:
                    steps.append({"step": "scrape", "iteration": step_num,
                                  "url": cand["url"], "status": "failed",
                                  "error": str(exc)})
                    if cand.get("snippet"):
                        sources.append({
                            "title": cand["title"], "url": cand["url"],
                            "snippet": cand.get("snippet", ""), "content": "",
                        })
                        step_sources += 1

            if not step_sources and not resp.organic_results:
                steps.append({"step": "stop", "reason": "no new information found"})
                break

            # --- decide: conclude or refine the query
            if refine and self.llm.available:
                decision = await self._decide_next(goal, current_query, sources,
                                                   step_num, max_steps,
                                                   provider=provider, model=model)
                if decision.get("conclude"):
                    steps.append({"step": "conclude",
                                  "reason": decision.get("reason", "sufficient")})
                    used_llm = True
                    break
                if decision.get("next_query"):
                    current_query = decision["next_query"]
                    used_llm = True
                    steps.append({"step": "refine", "iteration": step_num,
                                  "from": current_query,
                                  "to": decision["next_query"]})
                    continue
            # heuristic refinement fallback (no LLM or LLM silent)
            improved = self._heuristic_refine(goal, current_query, sources, step_num)
            if improved and improved != current_query and refine:
                steps.append({"step": "refine", "iteration": step_num,
                              "from": current_query, "to": improved,
                              "mode": "heuristic"})
                current_query = improved
                continue
            steps.append({"step": "stop", "reason": "no further refinement suggested",
                          "iteration": step_num})
            break

        # --- synthesize
        answer, provider_used, model_used = await self._synthesize(
            goal, sources, provider=provider, model=model,
            used_llm_flag=[used_llm],
        )
        steps.append({"step": "synthesize", "sources_used": len(sources),
                      "provider": provider_used})
        return {
            "answer": answer,
            "citations": [
                {"title": s["title"], "url": s["url"], "snippet": s["snippet"][:200]}
                for s in sources
            ],
            "search_results": search_results,
            "sources_used": sources,
            "reasoning_steps": steps,
            "provider": provider_used,
            "model": model_used,
            "deadline_exceeded": deadline_exceeded,
        }

    async def _decide_next(self, goal: str, current_query: str,
                           sources: List[Dict[str, Any]], step_num: int,
                           max_steps: int, *, provider: Optional[str],
                           model: Optional[str]) -> Dict[str, Any]:
        try:
            context = self._build_context(sources) or "no sources yet"
            system = ("You are an autonomous research planner. Decide whether enough "
                      "information has been gathered to answer the goal. If yes, reply "
                      "exactly: CONCLUDE. Otherwise reply with exactly one improved "
                      "web search query and nothing else.")
            user = (f"Goal: {goal}\nCurrent query: {current_query}\nStep {step_num} "
                    f"of {max_steps}\n\nGathered so far:\n{context[:3000]}")
            text = await self._llm_complete([{"role": "user", "content": user}],
                                            system=system)
            text = text.strip().upper()
            if text.startswith("CONCLUDE"):
                return {"conclude": True, "reason": "llm: sufficient information"}
            cleaned = re.sub(r"\s+", " ", text.strip(" .")).strip()
            if cleaned and cleaned != current_query.upper() and 2 < len(cleaned) < 160:
                return {"next_query": cleaned}
        except LLMError:
            pass
        return {}

    @staticmethod
    def _heuristic_refine(goal: str, current_query: str,
                          sources: List[Dict[str, Any]], step_num: int) -> str:
        """Deterministic query refinement when no LLM is available."""
        if step_num >= 3:
            return ""
        # add the most informative keyword from sources not already in the query
        words = set(re.findall(r"[A-Za-z][A-Za-z\-]{3,}", current_query.lower()))
        counts: Dict[str, int] = {}
        for src in sources:
            blob = (src.get("title", "") + " " + src.get("snippet", "")).lower()
            for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", blob):
                if w not in words and w not in {
                    "that", "this", "with", "from", "have", "they", "their", "what",
                    "how", "are", "the", "and", "for", "your", "you", "our", "not",
                    "will", "can", "has", "about", "into", "been", "was",
                }:
                    counts[w] = counts.get(w, 0) + 1
        if not counts:
            return ""
        top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:2]
        return f"{current_query} {' '.join(w for w, _ in top)}"

    @staticmethod
    def estimate_tokens(result: Dict[str, Any]) -> tuple:
        text = result.get("answer", "") + str(result.get("search_results", ""))
        return count_tokens(text), 0

    # -------------------------------------------------------------- streaming
    async def research_stream(self, query: str, *, max_sources: Optional[int] = None,
                               provider: Optional[str] = None,
                               model: Optional[str] = None,
                               deadline: Optional[float] = None):
        """Yield SSE-friendly event dicts while running /ai/search."""
        max_sources = min(max_sources or self.max_sources, 20)
        deadline = deadline or self.deadline_seconds or None
        deadline_at = (time.monotonic() + deadline) if deadline else None
        content_left = self.content_budget
        plan = await self._plan_queries(query, provider=provider, model=model,
                                        used_llm_flag=[False])
        query_list = plan["queries"]
        yield {"type": "plan", "queries": query_list}

        seen_links: Dict[str, Dict[str, Any]] = {}
        sources: List[Dict[str, Any]] = []
        for q in query_list[: self.max_steps]:
            if self._deadline_exceeded(deadline_at):
                yield {"type": "stop", "reason": "deadline_exceeded"}
                break
            try:
                resp = await self.orchestrator.search(
                    SearchRequest(q=q, num=min(10, max_sources), engine="auto")
                )
            except Exception as exc:
                yield {"type": "search", "query": q, "status": "failed", "error": str(exc)}
                continue
            yield {"type": "search", "query": q, "results": len(resp.organic_results),
                   "engine": resp.search_metadata.get("engine")}
            for item in resp.organic_results:
                if item.link and item.link not in seen_links:
                    seen_links[item.link] = {
                        "title": item.title, "url": item.link, "snippet": item.snippet,
                    }
            if len(seen_links) >= max_sources * 3:
                break

        for src in list(seen_links.values())[: max_sources * 2]:
            if len(sources) >= max_sources:
                break
            if self._deadline_exceeded(deadline_at):
                yield {"type": "stop", "reason": "deadline_exceeded"}
                break
            try:
                page = await self.scraper(src["url"])
                content = (page.get("content") or "")[:3000]
                content = content[: max(0, content_left)]
                content_left -= len(content)
                if not content.strip():
                    raise ValueError("empty content")
                sources.append({
                    "title": page.get("title") or src["title"], "url": src["url"],
                    "snippet": src.get("snippet", ""), "content": content,
                })
                yield {"type": "source", "url": src["url"], "title": src["title"]}
            except Exception:
                yield {"type": "source", "url": src["url"], "status": "failed"}
                if src.get("snippet"):
                    sources.append({
                        "title": src["title"], "url": src["url"],
                        "snippet": src.get("snippet", ""), "content": "",
                    })

        answer, provider_used, model_used = await self._synthesize(
            query, sources, provider=provider, model=model, used_llm_flag=[False]
        )
        yield {"type": "synthesize", "provider": provider_used,
               "sources_used": len(sources)}
        yield {"type": "answer", "answer": answer, "citations": [
            {"title": s["title"], "url": s["url"], "snippet": s["snippet"][:200]}
            for s in sources
        ]}
