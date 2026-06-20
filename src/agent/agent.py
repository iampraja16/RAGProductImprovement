"""Agent logic (Refactored for new tools)."""

import json
import logging
from typing import Dict, Any, List, TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from src.services.providers import get_llm
from src.agent.tools import get_tool_schemas, get_registered_tools
from src.agent.prompts import RAG_ROUTER_PROMPT, RAG_SYNTHESIZER_PROMPT

logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    query: str
    chat_history: list
    steps: Annotated[list, operator.add]
    tool_call: dict
    tool_result: dict
    final_answer: str
    synthesizer_messages: list
    chunks: list
    sql: str
    sql_data: list
    graph_traversal: dict

class Agent:
    def __init__(self):
        self.llm = get_llm()
        self.tools = get_registered_tools()
        self.tool_schemas = get_tool_schemas()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)
        workflow.add_node("router", self._router_node)
        workflow.add_node("tool_executor", self._tool_executor_node)
        workflow.add_node("synthesizer", self._synthesizer_node)
        
        workflow.set_entry_point("router")
        workflow.add_conditional_edges(
            "router",
            lambda state: "tool_executor" if state.get("tool_call") else "synthesizer"
        )
        workflow.add_edge("tool_executor", "synthesizer")
        workflow.add_edge("synthesizer", END)
        
        return workflow.compile()

    def _router_node(self, state: AgentState):
        query = state["query"]
        sys_msg = SystemMessage(content=RAG_ROUTER_PROMPT)
        
        # Tools are passed using bind_tools
        llm_with_tools = self.llm.bind_tools(self.tool_schemas)
        
        from src.services.resilience import cloud_llm_breaker, resilient_call_with_fallback
        fallback_msg = AIMessage(content="Layanan LLM router tidak stabil. Menjawab langsung.")
        response = resilient_call_with_fallback(
            cloud_llm_breaker,
            fallback_msg,
            lambda: llm_with_tools.invoke([sys_msg, HumanMessage(content=query)])
        )
        
        tool_call = None
        # LangChain populates tool_calls list if tools were triggered
        if hasattr(response, "tool_calls") and response.tool_calls:
            call = response.tool_calls[0]
            tool_call = {
                "name": call["name"],
                "arguments": call["args"]
            }
        elif "function_call" in response.additional_kwargs:
            func_call = response.additional_kwargs["function_call"]
            tool_call = {
                "name": func_call["name"],
                "arguments": json.loads(func_call["arguments"]) if isinstance(func_call["arguments"], str) else func_call["arguments"]
            }
        
        # Log reasoning step
        # Return only the updates to the state
        return {"steps": [{"node": "router", "tool_call": tool_call}], "tool_call": tool_call}

    def _tool_executor_node(self, state: AgentState):
        tool_call = state["tool_call"]
        tool_name = tool_call["name"]
        arguments = tool_call["arguments"]
        
        tool_func = next((t for t in self.tools if t.__name__ == tool_name), None)
        if tool_func:
            logger.info(f"Executing tool {tool_name} with args {arguments}")
            result = tool_func(**arguments)
            return {"tool_result": result, "steps": [{"node": "tool_executor", "tool": tool_name, "status": "success"}]}
        else:
            return {"tool_result": {"answer": f"Error: Tool {tool_name} not found."}, "steps": [{"node": "tool_executor", "tool": tool_name, "status": "error"}]}

    def _synthesizer_node(self, state: AgentState):
        query = state["query"]
        tool_result = state.get("tool_result", {})
        
        if tool_result:
            context = tool_result.get("answer", "")
            sys_msg = SystemMessage(content=RAG_SYNTHESIZER_PROMPT)
            prompt = f"Question: {query}\n\nContext:\n{context}"
            # Defer LLM invocation to the streaming/get_response method
            return {
                "synthesizer_messages": [sys_msg, HumanMessage(content=prompt)],
                "chunks": tool_result.get("chunks", []),
                "sql": tool_result.get("sql"),
                "sql_data": tool_result.get("sql_data"),
                "graph_traversal": tool_result.get("graph_traversal"),
                "steps": [{"node": "synthesizer", "status": "prepared"}]
            }
        else:
            return {
                "final_answer": "I could not find an appropriate tool to answer your query.",
                "steps": [{"node": "synthesizer", "status": "complete"}]
            }

    def get_response(self, query: str, chat_history: List[Dict] = None) -> Dict[str, Any]:
        initial_state = {
            "query": query,
            "chat_history": chat_history or [],
            "steps": [],
            "tool_call": None,
            "tool_result": None,
            "final_answer": None
        }
        
        final_state = self.graph.invoke(initial_state)
        
        # If synthesizer messages were prepared, generate the final answer now (sync)
        if "synthesizer_messages" in final_state and not final_state.get("final_answer"):
            from src.services.resilience import cloud_llm_breaker, resilient_call_with_fallback
            fallback_content = "Mohon maaf, layanan LLM (sintesis jawaban) saat ini tidak tersedia atau sedang mengalami gangguan. Silakan coba beberapa saat lagi."
            response = resilient_call_with_fallback(
                cloud_llm_breaker,
                AIMessage(content=fallback_content),
                lambda: self.llm.invoke(final_state["synthesizer_messages"])
            )
            final_state["final_answer"] = response.content
            
        return {
            "answer": final_state.get("final_answer", ""),
            "chunks": final_state.get("chunks", []),
            "sql": final_state.get("sql"),
            "sql_data": final_state.get("sql_data"),
            "graph_traversal": final_state.get("graph_traversal"),
            "steps": final_state.get("steps", [])
        }

    def stream_response(self, query: str, chat_history: List[Dict] = None):
        initial_state = {
            "query": query,
            "chat_history": chat_history or [],
            "steps": [],
            "tool_call": None,
            "tool_result": None,
            "final_answer": None
        }
        
        yield json.dumps({"type": "status", "content": "Agent is thinking..."}) + "\n"
        
        final_state = initial_state.copy()
        
        # Stream intermediate graph steps
        for output in self.graph.stream(initial_state):
            for node_name, node_state in output.items():
                if node_name == "router":
                    tool_call = node_state.get("tool_call")
                    if tool_call:
                        yield json.dumps({"type": "status", "content": f"Querying {tool_call['name']}..."}) + "\n"
                    else:
                        yield json.dumps({"type": "status", "content": "Answering directly..."}) + "\n"
                
                elif node_name == "tool_executor":
                    yield json.dumps({"type": "status", "content": "Analyzing results..."}) + "\n"
                    tr = node_state.get("tool_result", {})
                    # Yield tool data to update the UI expanders immediately
                    yield json.dumps({
                        "type": "tool_data", 
                        "sql": tr.get("sql"),
                        "sql_data": tr.get("sql_data"),
                        "chunks": tr.get("chunks"),
                        "graph_traversal": tr.get("graph_traversal")
                    }, default=str) + "\n"
                    
                final_state.update(node_state)
        
        # Now stream the final LLM synthesis token by token
        final_answer = final_state.get("final_answer") or ""
        if "synthesizer_messages" in final_state and not final_answer:
            messages = final_state["synthesizer_messages"]
            from src.services.resilience import cloud_llm_breaker, CircuitBreakerOpenException

            try:
                # Check circuit breaker before starting the stream
                cloud_llm_breaker.check_state()

                # Stream generator
                iterator = self.llm.stream(messages)
                stream_failed = False
                while True:
                    try:
                        chunk = next(iterator)
                        token = chunk.content
                        final_answer += token
                        yield json.dumps({"type": "token", "content": token}) + "\n"
                    except StopIteration:
                        # Record success once per completed stream, not per token
                        if not stream_failed:
                            cloud_llm_breaker.record_success()
                        break
                    except Exception as e:
                        stream_failed = True
                        cloud_llm_breaker.record_failure()
                        raise e

            except (CircuitBreakerOpenException, Exception) as e:
                logger.error(f"Streaming failed: {e}")
                fallback_err = "Mohon maaf, layanan streaming LLM saat ini tidak tersedia (API unavailable or rate limit exceeded)."
                yield json.dumps({"type": "token", "content": fallback_err}) + "\n"
        else:
            # Fallback if already answered
            yield json.dumps({"type": "token", "content": final_answer}) + "\n"
            
        yield json.dumps({
            "type": "done",
            "steps": final_state.get("steps", [])
        }, default=str) + "\n"
