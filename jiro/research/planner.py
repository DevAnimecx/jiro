"""Deterministic research planner.

Produces a bounded plan from a validated request without calling anything. It
must, because:

- the core test suite cannot depend on a live model provider, and
- a plan derived from the question alone is auditable. Every subquestion and
  query can be traced to a rule in this file.

An LLM may later *refine* a plan, but the refined plan is re-validated by the
same bounds as the deterministic one. A model cannot widen the query count, add
subquestions past the cap, or introduce an exclusion the caller did not ask for.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, List, Sequence, Tuple

from jiro.log import get_logger
from jiro.research.errors import ResearchValidationError
from jiro.research.models import (
    PlannedQuery,
    ResearchPlan,
    ResearchRequest,
    SubQuestion,
)

log = get_logger("jiro.research.planner")

#: Hard caps on plan shape. The planner cannot exceed these even if a model
#: suggests it, and the deterministic planner never approaches them.
MAX_SUBQUESTIONS = 8
MAX_QUERIES_PER_PLAN = 12

#: Domains treated as authoritative when the question looks like it wants them.
#: Deliberately short: a long list would quietly bias results toward whoever is
#: on it rather than toward what is actually authoritative for the question.
_AUTHORITATIVE_BY_CATEGORY = {
    "academic": (),
    "government": ("who.int", "nih.gov", "cdc.gov", "europa.eu", "gov.uk",
                   "un.org"),
    "documentation": ("docs.python.org", "developer.mozilla.org",
                      "sqlite.org", "readthedocs.io"),
}

_STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does doing for from
had has have having how i if in into is it its me of on or our so than that the
their then there these they this to was we were what when where which who why
will with would you your about into over under about via using use
""".split())

#: Question shapes that call for a different treatment. Matched on normalised
#: prefixes, not full parsing — this is routing, not understanding.
_PREFIXES = (
    ("compare", "comparison"),
    ("difference between", "comparison"),
    ("vs ", "comparison"),
    ("versus", "comparison"),
    ("how does", "mechanism"),
    ("how do", "mechanism"),
    ("how to", "procedure"),
    ("why", "causal"),
    ("what is", "definition"),
    ("what are", "definition"),
    ("what does", "definition"),
    ("when", "temporal"),
    ("who", "attribution"),
    ("latest", "freshness"),
    ("recent", "freshness"),
    ("current", "freshness"),
)

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-\.]*")


def _tokens(text: str) -> List[str]:
    return [w for w in _WORD.findall(text.lower())
            if len(w) > 1 and w not in _STOPWORDS]


_LEADING_FILLER = re.compile(
    r"^(?:what|why|how|when|who|which|where|is|are|does|do|did|can|could|"
    r"should|would|will|explain|describe|compare|contrast|tell me about|"
    r"give me|please)\b[\s,]*",
    re.IGNORECASE,
)
_TRAILING_FILLER = re.compile(r"[?.!]+\s*$")


def topic_phrase(question: str) -> str:
    """The subject of the question, with the interrogative scaffolding removed.

    Keeps the caller's original casing and wording. Building subquestions from
    lowercased keyword tokens produces sentences like "What causes dns
    resolution fail intermittently kubernetes", which read as machine output
    and, worse, lose proper nouns a search engine weights.
    """
    text = _TRAILING_FILLER.sub("", question.strip())
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_FILLER.sub("", text).strip()
    # Drop a trailing auxiliary ("... do we" -> "...").
    text = re.sub(r"\s+(do|does|did|is|are|should|would|can|could|will)$",
                  "", text, flags=re.IGNORECASE).strip()
    return text or _TRAILING_FILLER.sub("", question.strip())


def classify_question(question: str) -> str:
    """Assign a coarse intent, used only to shape the plan.

    Returns one of ``comparison``, ``mechanism``, ``procedure``, ``causal``,
    ``definition``, ``temporal``, ``attribution``, ``freshness`` or
    ``general``.
    """
    lowered = " ".join(question.lower().split())
    for prefix, kind in _PREFIXES:
        if lowered.startswith(prefix) or f" {prefix}" in lowered:
            return kind
    return "general"


def extract_entities(question: str) -> Tuple[str, ...]:
    """Capitalised runs and quoted spans, which are the usual entity markers.

    Deliberately conservative. A wrong entity in a plan biases every query it
    touches, so anything ambiguous is left out rather than guessed.
    """
    found: List[str] = []

    for match in re.finditer(r'"([^"]{2,60})"|“([^”]{2,60})”', question):
        found.append((match.group(1) or match.group(2)).strip())

    for match in re.finditer(r"\b([A-Z][A-Za-z0-9]*(?:[ \-][A-Z][A-Za-z0-9]*)*)",
                             question):
        candidate = match.group(1).strip()
        # A capitalised first word is usually just sentence case.
        if match.start() == 0:
            head, _, rest = candidate.partition(" ")
            candidate = rest or head
        if len(candidate) > 1 and candidate.lower() not in _STOPWORDS:
            if candidate not in found:
                found.append(candidate)
    return tuple(found[:8])


def _core_terms(question: str) -> List[str]:
    return _tokens(question)


def _dedupe_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = " ".join(item.lower().split())
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


class DeterministicPlanner:
    """Builds a bounded plan from a request using fixed rules."""

    def __init__(self, *, max_subquestions: int = MAX_SUBQUESTIONS,
                 max_queries: int = MAX_QUERIES_PER_PLAN) -> None:
        self._max_subquestions = min(max_subquestions, MAX_SUBQUESTIONS)
        self._max_queries = min(max_queries, MAX_QUERIES_PER_PLAN)

    def plan(self, request: ResearchRequest) -> ResearchPlan:
        """Produce an initial plan for ``request``.

        The result always satisfies the caps above and never exceeds
        ``request.budget.max_queries`` queries.
        """
        question = request.question
        kind = classify_question(question)
        terms = _core_terms(question)
        entities = extract_entities(question)
        entity = entities[0] if entities else ""

        subquestions = self._subquestions(question, kind, entities, request)
        queries = self._queries(question, kind, terms, entity, subquestions,
                                request)

        authoritative = self._authoritative(request, kind)
        criteria = self._completion_criteria(request, subquestions)

        plan = ResearchPlan(
            question=question,
            subquestions=tuple(subquestions),
            queries=tuple(queries),
            entities=entities,
            angles=self._angles(kind, terms, entities),
            authoritative_domains=authoritative,
            completion_criteria=criteria,
            exclusions=request.exclusions,
            max_iterations=request.budget.max_iterations,
            revision=1,
            generator="deterministic",
        )
        log.debug("planned research", extra={
            "subquestions": len(subquestions), "queries": len(queries),
            "intent": kind,
        })
        return plan

    # ------------------------------------------------------------ subquestions

    def _subquestions(self, question: str, kind: str,
                      entities: Sequence[str],
                      request: ResearchRequest) -> List[SubQuestion]:
        core = topic_phrase(question) or " ".join(_core_terms(question))
        # Anchor subquestions on the whole topic, not the first proper noun.
        # "What causes DNS" is not the question the caller asked; "what causes
        # DNS resolution to fail intermittently in Kubernetes" is.
        subject = core
        out: List[SubQuestion] = []

        def add(sid: str, text: str, required: bool = True) -> None:
            if len(out) >= self._max_subquestions:
                return
            out.append(SubQuestion(id=sid, text=text, required=required,
                                   expected_evidence=self._expected(sid)))

        # A definition subquestion only makes sense when the question is about
        # what something is. For causal, procedural or temporal questions the
        # concept is already understood and the time is better spent elsewhere.
        if kind in ("definition", "general"):
            add("sq_definition", f"What {subject} is, precisely, and how it "
                                 f"is defined by authoritative sources?")

        if kind == "comparison":
            add("sq_compare", f"On which dimensions does {subject} differ "
                              f"from its alternatives?")
            add("sq_tradeoffs", f"What trade-offs and limitations apply to "
                                f"{subject}?")
        elif kind == "mechanism":
            add("sq_mechanism", f"What mechanism or process makes {subject} "
                                f"work as described?")
            add("sq_conditions", f"Under what conditions does {subject} hold, "
                                 f"and when does it fail?")
        elif kind == "causal":
            add("sq_causes", f"What causes {subject}, and what evidence links "
                             f"cause to effect?")
            add("sq_alternatives", f"What alternative explanations exist for "
                                   f"{subject}?")
        elif kind == "procedure":
            add("sq_steps", "What are the concrete steps for " + subject + ", in "
                             "order?")
            add("sq_preconditions", "What preconditions, and what failure "
                                    "modes, apply?")
        elif kind == "temporal":
            add("sq_timeline", f"What is the timeline of developments in "
                               f"{subject}?")
            add("sq_current", f"What is the most recent confirmed state of "
                              f"{subject}?")
        elif kind == "attribution":
            add("sq_who", f"Who is authoritative on {subject}, and what have "
                          f"they said?")
        else:
            add("sq_evidence", f"What evidence supports the main claims about "
                               f"{subject}?")

        add("sq_limitations", f"What are the known limitations, caveats and "
                              f"disagreements concerning {subject}?",
            required=request.risk_level != "low")
        add("sq_freshness", f"How current is the available information on "
                            f"{subject}?", required=request.risk_level == "high")
        return out

    # ------------------------------------------------------------------ angles

    @staticmethod
    def _expected(subquestion_id: str) -> Tuple[str, ...]:
        mapping = {
            "sq_definition": ("definition", "reference"),
            "sq_compare": ("comparison", "benchmark"),
            "sq_tradeoffs": ("analysis", "reference"),
            "sq_mechanism": ("technical", "reference"),
            "sq_conditions": ("technical", "analysis"),
            "sq_causes": ("study", "analysis"),
            "sq_alternatives": ("analysis", "study"),
            "sq_steps": ("documentation", "guide"),
            "sq_preconditions": ("documentation", "technical"),
            "sq_timeline": ("news", "reference"),
            "sq_current": ("news", "reference"),
            "sq_who": ("reference", "news"),
            "sq_evidence": ("study", "reference"),
            "sq_limitations": ("analysis", "reference"),
            "sq_freshness": ("news",),
        }
        return mapping.get(subquestion_id, ("reference",))

    # ----------------------------------------------------------------- queries

    def _queries(self, question: str, kind: str, terms: Sequence[str],
                 entity: str, subquestions: Sequence[SubQuestion],
                 request: ResearchRequest) -> List[PlannedQuery]:
        budget = request.budget.max_queries
        core = " ".join(terms)
        # Only qualify with the entity when it is not already in the terms.
        # Prefixing an entity that the question already contains produces
        # stutter ("DNS dns resolution fail"), which searches worse than the
        # plain terms and crowds out better queries.
        needs_entity = bool(entity) and entity.lower() not in core.lower()
        subject = f"{entity} {core}".strip() if needs_entity else core
        candidates: List[Tuple[str, str]] = []

        def add(text: str, category: str = "any") -> None:
            text = " ".join(text.split()).strip()
            if text:
                candidates.append((text, category))

        # The literal question is always searched first: it is what the caller
        # asked, and paraphrasing it away loses their terms.
        add(question)
        add(core)

        if kind == "comparison":
            add(f"{subject} comparison alternatives differences")
            add(f"{subject} advantages disadvantages limitations")
        elif kind in ("mechanism", "causal"):
            add(f"{subject} how it works mechanism")
            add(f"{subject} limitations failure modes")
        elif kind == "procedure":
            add(f"{subject} how to steps guide")
        elif kind in ("temporal", "freshness"):
            add(f"{subject} latest news update")
        elif kind == "attribution":
            add(f"{subject} official documentation")

        for category in request.preferred_source_categories:
            if category == "academic":
                add(f"{subject} study research paper", "academic")
            elif category == "government":
                add(f"{subject} guidance report", "government")
            elif category == "news":
                add(f"{subject} news report", "news")
            elif category == "documentation":
                add(f"{subject} official documentation reference",
                    "documentation")

        for exclusion in request.exclusions:
            add(f"{subject} {exclusion}", "any")

        ordered = _dedupe_keep_order(text for text, _ in candidates)
        ordered = ordered[:budget]

        # Spread queries across subquestions so one narrow question cannot
        # consume the whole budget.
        category_by_text = {text: category for text, category in candidates}
        out: List[PlannedQuery] = []
        sub_index = 0
        for index, text in enumerate(ordered):
            if sub_index >= len(subquestions):
                sub_index = 0
            sq = subquestions[sub_index]
            sub_index += 1
            out.append(PlannedQuery(
                id=f"q{index}",
                text=text,
                subquestion_id=sq.id,
                priority=index,
                source_category=category_by_text.get(text, "any"),
            ))
        return out

    # ------------------------------------------------------------- completion

    def _authoritative(self, request: ResearchRequest,
                       kind: str) -> Tuple[str, ...]:
        domains: List[str] = []
        for category in request.preferred_source_categories:
            domains.extend(_AUTHORITATIVE_BY_CATEGORY.get(category, ()))
        if kind in ("mechanism", "procedure", "definition") and not domains:
            domains.extend(_AUTHORITATIVE_BY_CATEGORY["documentation"])
        return _dedupe_keep_order(domains)[:10]

    @staticmethod
    def _completion_criteria(request: ResearchRequest,
                             subquestions: Sequence[SubQuestion]) -> Tuple[str, ...]:
        required = [s for s in subquestions if s.required]
        criteria = [
            f"every required subquestion answered: {len(required)}",
            "each material claim cites at least one stored evidence record",
            "no unresolved contradiction on a material claim",
        ]
        if request.risk_level == "high":
            criteria.append(
                "each material claim corroborated by at least 2 distinct "
                "publisher domains")
        if request.max_age_days is not None:
            criteria.append(
                f"supporting sources published within {request.max_age_days} "
                f"days")
        return tuple(criteria)

    @staticmethod
    def _angles(kind: str, terms: Sequence[str],
                entities: Sequence[str]) -> Tuple[str, ...]:
        base = {
            "definition": ("authoritative reference", "practitioner sources"),
            "comparison": ("independent benchmarks", "critique and trade-off "
                           "discussion"),
            "mechanism": ("primary documentation", "independent technical "
                         "analysis"),
            "causal": ("study evidence", "competing explanations"),
            "procedure": ("official documentation", "worked examples"),
            "temporal": ("dated announcements", "current-state reporting"),
            "attribution": ("primary statements", "independent reporting"),
            "freshness": ("recent reporting", "release notes"),
        }.get(kind, ("reference sources", "practitioner accounts"))
        if entities:
            base = base + (f"first-party material about {entities[0]}",)
        if terms:
            base = base + ("contrarian or critical views on "
                           + " ".join(terms[:3]),)
        return _dedupe_keep_order(base)[:6]


def validate_plan(plan: ResearchPlan, request: ResearchRequest) -> ResearchPlan:
    """Re-apply every plan bound.

    Called after any refinement, including model-produced ones. A plan that
    exceeds the caller's query budget, the hard subquestion cap, or introduces
    an exclusion the caller did not ask for is rejected or trimmed here — not
    trusted because something produced it.
    """
    if not plan.question.strip():
        raise ResearchValidationError("plan must carry a question")

    subquestions = list(plan.subquestions)[:MAX_SUBQUESTIONS]
    valid_ids = {s.id for s in subquestions}

    seen = set()
    deduped: List[PlannedQuery] = []
    for query in plan.queries:
        if len(deduped) >= request.budget.max_queries:
            break
        text = " ".join(query.text.split())
        if not text:
            continue
        # A query bound to a dropped subquestion goes with it.
        if query.subquestion_id not in valid_ids:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(replace(query, text=text))

    exclusions = tuple(e for e in plan.exclusions if e in request.exclusions)

    return ResearchPlan(
        question=plan.question,
        subquestions=tuple(subquestions),
        queries=tuple(deduped),
        entities=tuple(plan.entities)[:12],
        angles=tuple(plan.angles)[:8],
        authoritative_domains=tuple(plan.authoritative_domains)[:10],
        completion_criteria=tuple(plan.completion_criteria)[:8],
        exclusions=exclusions,
        max_iterations=min(plan.max_iterations,
                           request.budget.max_iterations),
        revision=plan.revision,
        generator=plan.generator,
    )


def _max_subquestions_safe() -> int:
    return MAX_SUBQUESTIONS


__all__ = [
    "DeterministicPlanner",
    "MAX_QUERIES_PER_PLAN",
    "MAX_SUBQUESTIONS",
    "classify_question",
    "extract_entities",
    "validate_plan",
]