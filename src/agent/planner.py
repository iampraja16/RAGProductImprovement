"""Query decomposition planner — converts a free-text query into a structured execution plan."""

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage

import logging

logger = logging.getLogger(__name__)

_PLANNER_SYSTEM_PROMPT = """\
You are a query decomposition engine for an industrial equipment maintenance (EMR) analysis system.

Decompose the user query into the minimal set of sub-tasks, each targeting the single best retrieval tool.

Available tools:
- ask_emr_database  : Quantitative — counts, totals, aggregations, rankings, statistics, per-branch/site/date comparisons, listing EMR numbers comprehensively.
- ask_emr_graph     : Qualitative  — root causes, repair procedures, failure patterns, expert synthesis, component behavior analysis.
- search_emr_records: Specific EMR record lookup — when user wants details of a particular EMR or wants to browse a small list of records.
- analyze_smr       : Service Meter Reading (SMR/HM) distribution analysis — failures at specific operating hours.

Decomposition rules:
1. Prefer fewer tasks. Use 1 task for simple queries; 2-3 maximum for genuinely combinational ones.
2. Quantitative dimensions → ask_emr_database. Qualitative dimensions → ask_emr_graph. Both present → split into 2 tasks.
3. Any HM/SMR/jam operasi question → analyze_smr, regardless of other rules.
4. Specific EMR number in query → search_emr_records.
5. completeness_checklist must enumerate every distinct dimension the user asked about.
6. Set is_combinational=true only when 2+ tools are genuinely required.
7. CRITICAL: "Top N / N teratas masalah yang paling sering terjadi BESERTA angkanya/jumlahnya/frekuensinya" = BOTH qualitative ("what are the top problems") AND quantitative ("what are their counts/frequencies"). ALWAYS split into 2 tasks: graph (top problems list) + database (counts per site). This applies to ANY variation: "5 problem teratas beserta angkanya", "top 5 issues with their counts", "masalah paling sering dan jumlahnya", etc.

Few-shot examples:

Query: "Berapa jumlah kerusakan hydraulic di HD785-7 per cabang?"
{"tasks":[{"tool":"ask_emr_database","sub_question":"count hydraulic failures for HD785-7 grouped by branch site","priority":1}],"is_combinational":false,"completeness_checklist":["jumlah kerusakan hydraulic HD785-7 per cabang"]}

Query: "Apa penyebab hydraulic leak pada HD785-7 dan berapa jumlahnya per site?"
{"tasks":[{"tool":"ask_emr_graph","sub_question":"root causes and repair patterns for hydraulic leak on HD785-7","priority":1},{"tool":"ask_emr_database","sub_question":"count hydraulic leak failures for HD785-7 grouped by site","priority":2}],"is_combinational":true,"completeness_checklist":["penyebab hydraulic leak HD785-7","jumlah per site HD785-7"]}

Query: "Di SMR berapa hydraulic leak biasanya muncul pada PC200?"
{"tasks":[{"tool":"analyze_smr","sub_question":"SMR hour meter distribution for hydraulic leak on PC200","priority":1}],"is_combinational":false,"completeness_checklist":["distribusi SMR hydraulic leak PC200"]}

Query: "Tampilkan detail EMR U-00013147"
{"tasks":[{"tool":"search_emr_records","sub_question":"U-00013147 full record detail","priority":1}],"is_combinational":false,"completeness_checklist":["detail EMR U-00013147"]}

Query: "Berikan informasi problem sering terjadi pada HD785-7: penyebab, langkah perbaikan, komponen rusak, jumlah per cabang dan site, serta kesimpulan komponen paling sering rusak."
{"tasks":[{"tool":"ask_emr_graph","sub_question":"common problems, root causes, repair steps, and failed components for HD785-7","priority":1},{"tool":"ask_emr_database","sub_question":"count problems for HD785-7 grouped by branch and site, plus component frequency ranking","priority":2}],"is_combinational":true,"completeness_checklist":["problem umum HD785-7","penyebab kerusakan","langkah perbaikan","komponen rusak","jumlah per cabang dan site","komponen paling sering rusak"]}

Query: "5 problem HD785-7 yang paling sering terjadi beserta angkanya, lalu hitung ada berapa banyak masalah tersebut di site sangatta serta Tarakan"
{"tasks":[{"tool":"ask_emr_graph","sub_question":"top 5 most common problems and symptoms for HD785-7","priority":1},{"tool":"ask_emr_database","sub_question":"count HD785-7 problems at Sangatta and Tarakan sites, grouped by techcare_component","priority":2,"depends_on":"ask_emr_graph","context_hint":"top_problems"}],"is_combinational":true,"completeness_checklist":["5 problem paling sering HD785-7","jumlah per site sangatta tarakan HD785-7"]}

Query: "Top 5 engine problems on PC200 with their counts, and breakdown per branch site"
{"tasks":[{"tool":"ask_emr_graph","sub_question":"what are the top 5 engine problems for PC200","priority":1},{"tool":"ask_emr_database","sub_question":"count PC200 engine problems per branch site, grouped by techcare_component","priority":2,"depends_on":"ask_emr_graph","context_hint":"top_problems"}],"is_combinational":true,"completeness_checklist":["top 5 engine problems PC200","count per branch site PC200"]}
"""


class SubTask(BaseModel):
    tool: Literal["ask_emr_graph", "ask_emr_database", "analyze_smr", "search_emr_records"]
    sub_question: str = Field(description="Specific sub-question for this tool to answer")
    priority: int = Field(ge=1, le=5, description="Execution order priority, 1 = highest")
    depends_on: Optional[str] = Field(default=None, description="If set, this task depends on the output of the named tool. If the context_hint is 'top_problems', the downstream task gets the upstream tool's findings injected into its sub_question automatically at runtime.")
    context_hint: Optional[str] = Field(default=None, description="What to extract from the dependency result. Currently supported: 'top_problems' — extracts named items from the upstream tool's answer text.")


class QueryPlan(BaseModel):
    tasks: List[SubTask] = Field(min_length=1)
    is_combinational: bool
    completeness_checklist: List[str] = Field(min_length=1)


def _fallback_plan(query: str) -> QueryPlan:
    return QueryPlan(
        tasks=[SubTask(tool="ask_emr_graph", sub_question=query, priority=1)],
        is_combinational=False,
        completeness_checklist=[query],
    )


class QueryPlanner:
    def __init__(self, llm):
        self._chain = llm.with_structured_output(QueryPlan)

    def plan(self, query: str, resolved_context: dict) -> QueryPlan:
        entity_hint = ""
        entities = resolved_context.get("entities", [])
        if entities:
            summary = [
                f"{e['mention']} → {e['canonical_name']} ({e['entity_type']})"
                for e in entities
            ]
            entity_hint = f"\nResolved entities: {', '.join(summary)}"

        messages = [
            SystemMessage(content=_PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=f"Query: {query}{entity_hint}\n\nReturn JSON only."),
        ]
        try:
            return self._chain.invoke(messages)
        except Exception as exc:
            logger.warning(f"QueryPlanner structured output failed, using fallback: {exc}")
            return _fallback_plan(query)
