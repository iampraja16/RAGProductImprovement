"""Agentic GraphRAG orchestrator — Plan → Execute → Aggregate → Reflect → Compose."""

from __future__ import annotations

import json
import re
import logging
from typing import Dict, Any, List, TypedDict, Annotated, Optional
import operator

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

from src.services.providers import get_llm, get_graph_client, get_embeddings
from src.services.entity_resolver import EntityResolver
from src.agent.planner import QueryPlanner, QueryPlan, _fallback_plan
from src.agent.tools import get_registered_tools
from src.agent.prompts import RAG_SYNTHESIZER_PROMPT, PROVENANCE_DIVIDER, _build_fallback_provenance

logger = logging.getLogger(__name__)

_MAX_REFLECTION_RETRIES = 2


class AgentState(TypedDict):
    query: str
    chat_history: list
    steps: Annotated[list, operator.add]
    resolved_context: dict
    query_plan: dict
    tool_results: Annotated[list, operator.add]
    retry_count: int
    aggregated_context: str
    final_answer: str
    synthesizer_messages: list
    chunks: list
    sql: str
    sql_data: list
    graph_traversal: dict
    smr_data: list
    ppi_links: list


def _build_resolver() -> EntityResolver:
    return EntityResolver(get_graph_client(), get_llm(temperature=0.0), get_embeddings())


def _find_tool(tools: list, name: str):
    return next((t for t in tools if t.__name__ == name), None)


def _merge_ppi(existing: Optional[list], incoming: Optional[list]) -> Optional[list]:
    if not incoming:
        return existing
    if not existing:
        return incoming
    seen = {e["external_id"] for e in existing}
    merged = list(existing)
    for item in incoming:
        if item.get("external_id") not in seen:
            merged.append(item)
            seen.add(item["external_id"])
    return merged


class Agent:
    def __init__(self):
        self.llm = get_llm()
        self.tools = get_registered_tools()
        self.planner = QueryPlanner(get_llm(temperature=0.0))
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        workflow = StateGraph(AgentState)
        workflow.add_node("entity_resolver", self._entity_resolver_node)
        workflow.add_node("planner", self._planner_node)
        workflow.add_node("executor", self._executor_node)
        workflow.add_node("aggregator", self._aggregator_node)
        workflow.add_node("reflection", self._reflection_node)
        workflow.add_node("composer", self._composer_node)

        workflow.set_entry_point("entity_resolver")
        workflow.add_edge("entity_resolver", "planner")
        workflow.add_edge("planner", "executor")
        workflow.add_edge("executor", "aggregator")
        workflow.add_edge("aggregator", "reflection")
        workflow.add_conditional_edges(
            "reflection",
            lambda state: "planner" if state.get("_needs_retry") else "composer",
        )
        workflow.add_edge("composer", END)

        return workflow.compile()

    def _entity_resolver_node(self, state: AgentState) -> dict:
        from src.services.telemetry import tracer
        with tracer.start_as_current_span("entity_resolver_node") as span:
            query = state["query"]
            resolver = _build_resolver()
            ctx = resolver.resolve_full_context(query)
            span.set_attribute("entity_count", len(ctx.get("entities", [])))
            span.set_attribute("has_model_entities", ctx.get("has_model_entities", False))
            logger.info(f"Entity resolver: {len(ctx.get('entities', []))} entities, site={ctx.get('site_hint')}, cids={ctx.get('symptom_community_ids')}")
            return {
                "resolved_context": ctx,
                "steps": [{"node": "entity_resolver", "entity_count": len(ctx.get("entities", []))}],
            }

    def _planner_node(self, state: AgentState) -> dict:
        from src.services.telemetry import tracer
        with tracer.start_as_current_span("planner_node") as span:
            query = state["query"]
            ctx = state.get("resolved_context", {})
            retry = state.get("retry_count", 0)

            plan: QueryPlan = self.planner.plan(query, ctx)
            plan_dict = plan.model_dump()
            span.set_attribute("task_count", len(plan.tasks))
            span.set_attribute("is_combinational", plan.is_combinational)
            span.set_attribute("retry", retry)
            logger.info(f"Planner (retry={retry}): {len(plan.tasks)} tasks, combinational={plan.is_combinational}")
            return {
                "query_plan": plan_dict,
                "_needs_retry": False,
                "steps": [{"node": "planner", "tasks": [t["tool"] for t in plan_dict["tasks"]], "retry": retry}],
            }

    @staticmethod
    def _extract_context_from_result(result: dict, context_hint: str) -> tuple:
        answer = result.get("answer", "")
        if context_hint == "top_problems" and answer:
            # Handle bold (**...**) items with em-dash/en-dash separator
            # Format: "1. **Item name** — N kasus" or "1. Item name — N kasus"
            items = re.findall(r'\d+\.\s*(?:\*\*)?(.+?)(?:\*\*)?\s*[—–]\s*\d+', answer)
            if not items:
                # Fallback: numbered items without bold
                items = re.findall(r'\d+\.\s*(.*?)(?:\n|$)', answer)
                items = [i.strip() for i in items if i.strip() and not re.match(r'^\*\*|^Berdasarkan|^$', i.strip())]
            if items:
                cleaned = []
                for item in items[:10]:
                    item = item.strip(' *')
                    cleaned.append(item)
                problem_list = ', '.join(cleaned)
                return (f"Problems from graph analysis: {problem_list}", cleaned)
        return ("", [])

    def _executor_node(self, state: AgentState) -> dict:
        from src.services.telemetry import tracer
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with tracer.start_as_current_span("executor_node") as span:
            plan = state.get("query_plan", {})
            ctx = state.get("resolved_context", {})
            tasks = sorted(plan.get("tasks", []), key=lambda t: t.get("priority", 1))

            results = []
            
            def execute_task(task, context_override=None):
                tool_name = task["tool"]
                sub_q = task["sub_question"]
                tool_func = _find_tool(self.tools, tool_name)
                if tool_func is None:
                    logger.warning(f"Tool not found: {tool_name}")
                    return None
                task_ctx = context_override if context_override is not None else ctx
                try:
                    result = tool_func(query=sub_q, resolved_context=task_ctx)
                    result["_tool"] = tool_name
                    result["_sub_question"] = sub_q
                    logger.info(f"Executor: {tool_name} completed")
                    return result
                except Exception as exc:
                    logger.error(f"Executor: {tool_name} failed — {exc}")
                    return {"answer": f"Error: {exc}", "_tool": tool_name, "_sub_question": sub_q}

            # Separate tasks into independent and dependent
            independent = [t for t in tasks if not t.get("depends_on")]
            dependent = [t for t in tasks if t.get("depends_on")]

            # Run independent tasks in parallel
            if independent:
                with ThreadPoolExecutor(max_workers=len(independent) or 1) as pool:
                    futures = [pool.submit(execute_task, t) for t in independent]
                    for f in as_completed(futures):
                        res = f.result()
                        if res is not None:
                            results.append(res)

            # For dependent tasks, enrich sub_question from dependency results
            enriched_tasks = []
            for task in dependent:
                dep_tool = task["depends_on"]
                context_hint = task.get("context_hint")
                # Find the dependency result
                dep_result = next((r for r in results if r.get("_tool") == dep_tool), None)
                if dep_result and context_hint:
                    context_str, problem_names = self._extract_context_from_result(dep_result, context_hint)
                    task = dict(task)
                    if context_str:
                        task["sub_question"] = f"{task['sub_question']}. Context from {dep_tool}: {context_str}"
                        logger.info(f"Enriched {task['tool']} sub_question with context from {dep_tool}")
                    if problem_names:
                        task["_problem_names"] = problem_names
                enriched_tasks.append(task)

            # Run dependent tasks in parallel with custom context
            if enriched_tasks:
                with ThreadPoolExecutor(max_workers=len(enriched_tasks) or 1) as pool:
                    enriched_futures = []
                    for t in enriched_tasks:
                        task_ctx = ctx
                        problem_names = t.pop("_problem_names", None) if isinstance(t, dict) else None
                        if problem_names:
                            task_ctx = dict(ctx)
                            task_ctx["graph_problem_names"] = problem_names
                        enriched_futures.append(pool.submit(execute_task, t, task_ctx))
                    for f in as_completed(enriched_futures):
                        res = f.result()
                        if res is not None:
                            results.append(res)

            # Re-sort results by the original task priority
            tool_priorities = {t["tool"]: t.get("priority", 1) for t in tasks}
            results.sort(key=lambda r: tool_priorities.get(r.get("_tool"), 99))

            span.set_attribute("tasks_executed", len(results))
            return {
                "tool_results": results,
                "steps": [{"node": "executor", "tools_run": [r.get("_tool") for r in results]}],
            }

    def _aggregator_node(self, state: AgentState) -> dict:
        from src.services.telemetry import tracer
        with tracer.start_as_current_span("aggregator_node"):
            results = state.get("tool_results", [])

            sections = []
            sql_out = None
            sql_data_out = None
            graph_out = None
            smr_out = None
            ppi_out = None
            chunks_out = []

            for res in results:
                tool = res.get("_tool", "unknown")
                sub_q = res.get("_sub_question", "")
                answer = res.get("answer", "")
                if answer:
                    sections.append(f"[{tool}] {sub_q}\n{answer}")
                if res.get("sql") and not sql_out:
                    sql_out = res["sql"]
                if res.get("sql_data") and not sql_data_out:
                    sql_data_out = res["sql_data"]
                if res.get("graph_traversal") and not graph_out:
                    graph_out = res["graph_traversal"]
                if res.get("smr_data") and not smr_out:
                    smr_out = res["smr_data"]
                ppi_out = _merge_ppi(ppi_out, res.get("ppi_links"))
                if res.get("chunks"):
                    chunks_out.extend(res["chunks"])

            aggregated = "\n\n---\n\n".join(sections) if sections else ""

            return {
                "aggregated_context": aggregated,
                "sql": sql_out,
                "sql_data": sql_data_out,
                "graph_traversal": graph_out,
                "smr_data": smr_out,
                "ppi_links": ppi_out,
                "chunks": chunks_out,
                "steps": [{"node": "aggregator", "sections": len(sections)}],
            }

    def _reflection_node(self, state: AgentState) -> dict:
        from src.services.telemetry import tracer
        with tracer.start_as_current_span("reflection_node") as span:
            retry = state.get("retry_count", 0)
            plan = state.get("query_plan", {})
            checklist = plan.get("completeness_checklist", [])
            aggregated = state.get("aggregated_context", "")
            results = state.get("tool_results", [])

            if retry >= _MAX_REFLECTION_RETRIES:
                span.set_attribute("decision", "force_complete")
                return {"_needs_retry": False, "steps": [{"node": "reflection", "decision": "force_complete"}]}

            has_any_answer = any(
                bool(r.get("answer", "").strip()) and not r["answer"].startswith("Error")
                for r in results
            )

            if not has_any_answer or not aggregated.strip():
                span.set_attribute("decision", "retry")
                logger.info(f"Reflection: empty results, retry {retry + 1}")
                return {
                    "_needs_retry": True,
                    "retry_count": retry + 1,
                    "steps": [{"node": "reflection", "decision": "retry", "attempt": retry + 1}],
                }

            span.set_attribute("decision", "complete")
            return {"_needs_retry": False, "steps": [{"node": "reflection", "decision": "complete", "checklist_items": len(checklist)}]}

    def _composer_node(self, state: AgentState) -> dict:
        from src.services.telemetry import tracer
        with tracer.start_as_current_span("composer_node"):
            query = state["query"]
            context = state.get("aggregated_context", "")
            final_answer = state.get("final_answer")

            if final_answer:
                return {"steps": [{"node": "composer", "status": "passthrough"}]}

            sys_msg = SystemMessage(content=RAG_SYNTHESIZER_PROMPT)
            prompt = f"Question: {query}\n\nContext:\n{context}"
            return {
                "synthesizer_messages": [sys_msg, HumanMessage(content=prompt)],
                "steps": [{"node": "composer", "status": "prepared"}],
            }

    def get_response(self, query: str, chat_history: List[Dict] = None) -> Dict[str, Any]:
        from langchain_community.callbacks import get_openai_callback
        from src.services.token_monitor import global_token_monitor
        from src.services.telemetry import tracer
        from src.services.providers import invoke_with_failover
        from src.services.resilience import _is_rate_limit_error
        import time

        start_time = time.time()
        with tracer.start_as_current_span("agent_get_response") as span:
            span.set_attribute("user_query_length", len(query))
            initial_state = {
                "query": query,
                "chat_history": chat_history or [],
                "steps": [],
                "resolved_context": {},
                "query_plan": {},
                "tool_results": [],
                "retry_count": 0,
                "aggregated_context": "",
                "final_answer": None,
            }

            with get_openai_callback() as cb:
                final_state = self.graph.invoke(initial_state)

            if "synthesizer_messages" in final_state and not final_state.get("final_answer"):
                try:
                    response = invoke_with_failover(final_state["synthesizer_messages"], task_type="reasoning")
                    final_state["final_answer"] = response.content
                except Exception as exc:
                    final_state["final_answer"] = (
                        "Layanan sedang sibuk (Rate Limit). Silakan coba lagi nanti."
                        if _is_rate_limit_error(exc)
                        else "Mohon maaf, layanan LLM saat ini tidak tersedia."
                    )

            fa = final_state.get("final_answer", "")
            if fa and PROVENANCE_DIVIDER not in fa:
                logger.warning("LLM synthesizer omitted EVIDENCE/PROVENANCE section — appending fallback")
                final_state["final_answer"] = fa + _build_fallback_provenance(final_state)

            usage_meta = self._build_usage_meta(cb, query, final_state)
            global_token_monitor.add_usage(usage_meta["prompt_tokens"], usage_meta["completion_tokens"], usage_meta["estimated_cost_usd"])

            span.set_attribute("response_time_ms", (time.time() - start_time) * 1000)
            span.set_attribute("total_tokens", usage_meta.get("total_tokens", 0))

            return {
                "answer": final_state.get("final_answer", ""),
                "chunks": final_state.get("chunks", []),
                "sql": final_state.get("sql"),
                "sql_data": final_state.get("sql_data"),
                "graph_traversal": final_state.get("graph_traversal"),
                "steps": final_state.get("steps", []),
                "token_usage": usage_meta,
                "smr_data": final_state.get("smr_data"),
                "ppi_links": final_state.get("ppi_links"),
            }

    def stream_response(self, query: str, chat_history: List[Dict] = None):
        from langchain_community.callbacks import get_openai_callback
        from src.services.token_monitor import global_token_monitor
        from src.services.telemetry import tracer
        from src.services.providers import stream_with_failover
        from src.services.resilience import _is_rate_limit_error, CircuitBreakerOpenException
        import time

        start_time = time.time()
        with tracer.start_as_current_span("agent_stream_response") as span:
            span.set_attribute("user_query_length", len(query))
            initial_state = {
                "query": query,
                "chat_history": chat_history or [],
                "steps": [],
                "resolved_context": {},
                "query_plan": {},
                "tool_results": [],
                "retry_count": 0,
                "aggregated_context": "",
                "final_answer": None,
            }

            yield json.dumps({"type": "status", "content": "Menganalisis query..."}) + "\n"

            final_state = initial_state.copy()
            with get_openai_callback() as cb:
                for output in self.graph.stream(initial_state):
                    for node_name, node_state in output.items():
                        if node_name == "entity_resolver":
                            yield json.dumps({"type": "status", "content": "Mengidentifikasi entitas..."}) + "\n"
                        elif node_name == "planner":
                            tasks = node_state.get("query_plan", {}).get("tasks", [])
                            tools_planned = [t["tool"] for t in tasks]
                            yield json.dumps({"type": "status", "content": f"Merencanakan: {', '.join(tools_planned)}"}) + "\n"
                        elif node_name == "executor":
                            yield json.dumps({"type": "status", "content": "Mengambil data..."}) + "\n"
                        elif node_name == "aggregator":
                            tr = final_state
                            yield json.dumps({
                                "type": "tool_data",
                                "sql": tr.get("sql"),
                                "sql_data": tr.get("sql_data"),
                                "chunks": tr.get("chunks"),
                                "graph_traversal": tr.get("graph_traversal"),
                                "smr_data": tr.get("smr_data"),
                                "ppi_links": tr.get("ppi_links"),
                            }, default=str) + "\n"
                        elif node_name == "reflection":
                            needs_retry = node_state.get("_needs_retry", False)
                            if needs_retry:
                                yield json.dumps({"type": "status", "content": "Memperluas pencarian..."}) + "\n"
                        elif node_name == "composer":
                            yield json.dumps({"type": "status", "content": "Menyusun jawaban..."}) + "\n"

                        final_state.update(node_state)

                final_answer = final_state.get("final_answer") or ""
                if "synthesizer_messages" in final_state and not final_answer:
                    messages = final_state["synthesizer_messages"]
                    try:
                        iterator, active_breaker = stream_with_failover(messages, task_type="reasoning")
                        stream_failed = False
                        while True:
                            try:
                                chunk = next(iterator)
                                token = chunk.content
                                final_answer += token
                                yield json.dumps({"type": "token", "content": token}) + "\n"
                            except StopIteration:
                                if not stream_failed:
                                    active_breaker.record_success()
                                usage_meta = self._build_usage_meta(cb, str(messages), final_state)
                                global_token_monitor.add_usage(usage_meta["prompt_tokens"], usage_meta["completion_tokens"], usage_meta["estimated_cost_usd"])
                                span.set_attribute("total_tokens", usage_meta.get("total_tokens", 0))
                                span.set_attribute("response_time_ms", (time.time() - start_time) * 1000)
                                yield json.dumps({"type": "metadata", "content": {"token_usage": usage_meta}}) + "\n"
                                break
                            except Exception as exc:
                                stream_failed = True
                                if not _is_rate_limit_error(exc):
                                    active_breaker.record_failure()
                                yield json.dumps({"type": "error", "content": f"Streaming terputus: {exc}"}) + "\n"
                                break
                    except (CircuitBreakerOpenException, Exception) as exc:
                        span.record_exception(exc)
                        yield json.dumps({"type": "token", "content": "Layanan streaming tidak tersedia saat ini."}) + "\n"
                else:
                    yield json.dumps({"type": "token", "content": final_answer}) + "\n"

            if final_answer and PROVENANCE_DIVIDER not in final_answer:
                logger.warning("LLM synthesizer omitted EVIDENCE/PROVENANCE in stream — appending fallback")
                fallback = _build_fallback_provenance(final_state)
                yield json.dumps({"type": "token", "content": fallback}) + "\n"
                final_answer += fallback

            yield json.dumps({
                "type": "done",
                "steps": final_state.get("steps", []),
                "smr_data": final_state.get("smr_data"),
                "ppi_links": final_state.get("ppi_links"),
            }, default=str) + "\n"

    @staticmethod
    def _build_usage_meta(cb: Any, prompt_ref: Any, state: dict) -> dict:
        from src.services.token_monitor import global_token_monitor
        if cb.total_tokens > 0:
            return {
                "prompt_tokens": cb.prompt_tokens,
                "completion_tokens": cb.completion_tokens,
                "total_tokens": cb.total_tokens,
                "estimated_cost_usd": cb.total_cost,
                "estimation_method": "langchain_callback",
            }
        text = str(prompt_ref) + str(state.get("synthesizer_messages", ""))
        meta = global_token_monitor.estimate_fallback(text, state.get("final_answer", ""))
        meta["estimation_method"] = "fallback_heuristics"
        return meta
