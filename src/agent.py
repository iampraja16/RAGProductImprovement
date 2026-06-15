import json
import logging
import time
from typing import TypedDict, Annotated, Sequence, Union, Dict, Any, List
from operator import add

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
import ollama

from .prompt import system_prompt
from .tools import get_registered_tools, get_tool_schemas, REGISTERED_TOOLS
from .utils import get_llm

logger = logging.getLogger(__name__)

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add]
    chunks: List[str]
    sql: str
    sql_data: List[Dict[str, Any]]
    graph_traversal: Dict[str, Any]
    token_usage: Dict[str, int]
    # FASE 5: Reasoning trace steps
    steps: List[Dict[str, Any]]

class Agent:
    def __init__(self):
        # We don't bind tools to the LLM directly here if using local Ollama.
        # Instead, we will pass the schemas to the Ollama client in call_llm
        from .config import settings
        self.llm = get_llm(temperature=0.0)
        self.tools = {t.__name__: t for t in REGISTERED_TOOLS}
        self.tool_schemas = get_tool_schemas()
        self.ollama_client = ollama.Client(host=settings.ollama_base_url)
        
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgentState)

        workflow.add_node("agent", self.call_llm)
        workflow.add_node("action", self.take_action)

        workflow.set_entry_point("agent")

        workflow.add_conditional_edges(
            "agent",
            self.exists_action,
            {
                True: "action",
                False: END
            }
        )
        workflow.add_edge("action", "agent")

        return workflow.compile()

    def call_llm(self, state: AgentState) -> Dict:
        """Call the LLM with the current messages and available tools."""
        messages = state['messages']
        
        # Ensure system prompt is present
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt)] + list(messages)
            
        # We need to use the native ollama python client for robust tool calling with llama3
        # LangChain's ChatOllama tool calling is sometimes flaky depending on the version
        
        # Convert LangChain messages to Ollama format
        ollama_msgs = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                ollama_msgs.append({'role': 'system', 'content': msg.content})
            elif isinstance(msg, HumanMessage):
                ollama_msgs.append({'role': 'user', 'content': msg.content})
            elif isinstance(msg, AIMessage):
                d = {'role': 'assistant', 'content': msg.content}
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    d['tool_calls'] = []
                    for tc in msg.tool_calls:
                        d['tool_calls'].append({
                            'function': {
                                'name': tc['name'],
                                'arguments': tc['args']
                            }
                        })
                ollama_msgs.append(d)
            elif isinstance(msg, ToolMessage):
                ollama_msgs.append({'role': 'tool', 'content': msg.content})

        try:
            logger.info("Calling Ollama with tools")
            from .config import settings

            t0 = time.time()
            response = self.ollama_client.chat(
                model=settings.ollama_model,
                messages=ollama_msgs,
                tools=self.tool_schemas,
                options={
                    "temperature": 0.0,
                    "num_ctx": 4096
                }
            )
            llm_ms = round((time.time() - t0) * 1000, 1)

            msg_dict = response.get('message', {})
            content = msg_dict.get('content', '')

            # Convert back to LangChain AIMessage
            ai_msg = AIMessage(content=content)

            # Extract tool calls if any
            tool_calls_parsed = []
            if 'tool_calls' in msg_dict and msg_dict['tool_calls']:
                for tc in msg_dict['tool_calls']:
                    function_def = tc.get('function', {})
                    tool_calls_parsed.append({
                        "name": function_def.get('name'),
                        "args": function_def.get('arguments', {}),
                        "id": f"call_{len(tool_calls_parsed)}"
                    })
                ai_msg.tool_calls = tool_calls_parsed

            # Record reasoning step
            step = {
                "step": "llm_routing",
                "duration_ms": llm_ms,
                "model": settings.ollama_model,
                "tool_selected": tool_calls_parsed[0]["name"] if tool_calls_parsed else None,
                "tool_args": tool_calls_parsed[0]["args"] if tool_calls_parsed else None,
                "direct_answer": bool(content and not tool_calls_parsed),
            }

            return {"messages": [ai_msg], "steps": [step]}

        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            return {"messages": [AIMessage(content="I encountered an error while processing your request.")], "steps": []}

    def exists_action(self, state: AgentState) -> bool:
        """Determine if the agent needs to call a tool."""
        result = state['messages'][-1]
        return hasattr(result, 'tool_calls') and len(result.tool_calls) > 0

    def take_action(self, state: AgentState) -> Dict:
        """Execute the tool requested by the LLM."""
        last_message = state['messages'][-1]
        tool_calls = last_message.tool_calls

        results = []
        chunks = []
        sql = None
        sql_data = None
        graph_traversal = None
        tool_steps = []

        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            logger.info(f"Executing tool: {tool_name} with args: {tool_args}")

            if tool_name not in self.tools:
                results.append(ToolMessage(
                    content=f"Error: Tool {tool_name} not found.",
                    tool_call_id=tool_call.get("id", "unknown")
                ))
                tool_steps.append({"step": "tool_exec", "tool": tool_name, "status": "error", "error": "not found"})
                continue

            try:
                tool_func = self.tools[tool_name]
                t0 = time.time()
                output = tool_func(**tool_args)
                tool_ms = round((time.time() - t0) * 1000, 1)

                tool_msg_content = str(output.get("answer", output))
                if output.get("chunks"):
                    chunks.extend(output.get("chunks", []))
                if output.get("sql"):
                    sql = output.get("sql")
                if output.get("sql_data"):
                    sql_data = output.get("sql_data")
                if output.get("graph_traversal"):
                    graph_traversal = output.get("graph_traversal")

                results.append(ToolMessage(
                    content=tool_msg_content,
                    name=tool_name,
                    tool_call_id=tool_call.get("id", "unknown")
                ))

                # Record tool execution step
                tool_steps.append({
                    "step": "tool_exec",
                    "tool": tool_name,
                    "duration_ms": tool_ms,
                    "status": "ok",
                    "retrieved_chunks": len(output.get("chunks") or []),
                    "has_graph": bool(output.get("graph_traversal")),
                    "has_sql": bool(output.get("sql")),
                    "cache_hit": output.get("cache_hit"),
                })

            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}")
                results.append(ToolMessage(
                    content=f"Error executing tool: {e}",
                    name=tool_name,
                    tool_call_id=tool_call.get("id", "unknown")
                ))
                tool_steps.append({"step": "tool_exec", "tool": tool_name, "status": "error", "error": str(e)})

        state_update = {"messages": results, "steps": tool_steps}
        if chunks:
            state_update["chunks"] = chunks
        if sql:
            state_update["sql"] = sql
        if sql_data:
            state_update["sql_data"] = sql_data
        if graph_traversal:
            state_update["graph_traversal"] = graph_traversal

        return state_update

    def get_response(self, query: str, chat_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """Entry point for the API."""
        # FASE 5: Menggunakan sistem 1 pesan 1 konteks (stateless).
        # Mengabaikan chat_history untuk menghemat ruang memori konteks qwen2.5:3b (2048 token)
        # dan memastikan pemanggilan tool (SQL/Graph) selalu akurat tanpa terganggu obrolan masa lalu.
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ]
        
        initial_state = {
            "messages": messages,
            "chunks": [],
            "sql": None,
            "sql_data": None,
            "graph_traversal": None,
            "token_usage": {},
            "steps": [],
        }

        try:
            result = self.graph.invoke(initial_state)
            final_msg = result['messages'][-1].content

            return {
                "answer": final_msg,
                "chunks": result.get("chunks", []),
                "sql": result.get("sql"),
                "sql_data": result.get("sql_data"),
                "graph_traversal": result.get("graph_traversal"),
                "token_usage": result.get("token_usage", {}),
                "steps": result.get("steps", []),  # Reasoning trace
            }
        except Exception as e:
            logger.error(f"Error in graph execution: {e}")
            return {
                "answer": f"An error occurred: {str(e)}",
                "chunks": [],
                "sql": None,
                "sql_data": None,
                "graph_traversal": None,
                "token_usage": {},
                "steps": [],
            }

    def stream_response(self, query: str):
        """
        Yield JSON stream events for real-time frontend consumption.
        """
        from .config import settings

        # Step 1: Initialize state
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ]

        state = {
            "messages": messages,
            "chunks": [],
            "sql": None,
            "sql_data": None,
            "graph_traversal": None,
            "token_usage": {},
            "steps": [],
        }

        # Call initial routing agent
        yield json.dumps({"type": "status", "content": "Menganalisis kueri untuk rute pencarian..."}) + "\n"
        agent_res = self.call_llm(state)
        state["messages"].extend(agent_res["messages"])
        if agent_res.get("steps"):
            state["steps"].extend(agent_res["steps"])

        # Check if tool calling was triggered
        if self.exists_action(state):
            tool_name = state["messages"][-1].tool_calls[0]["name"]
            yield json.dumps({"type": "status", "content": f"Mengambil data menggunakan tool: {tool_name}..."}) + "\n"

            # Execute tool
            action_res = self.take_action(state)
            state["messages"].extend(action_res["messages"])
            if action_res.get("steps"):
                state["steps"].extend(action_res["steps"])
            if action_res.get("chunks"):
                state["chunks"].extend(action_res["chunks"])
            if action_res.get("sql"):
                state["sql"] = action_res["sql"]
            if action_res.get("sql_data"):
                state["sql_data"] = action_res["sql_data"]
            if action_res.get("graph_traversal"):
                state["graph_traversal"] = action_res["graph_traversal"]

            # Yield tool data (so UI can draw the graph immediately while LLM streams final answer)
            yield json.dumps({
                "type": "tool_data",
                "sql": state["sql"],
                "sql_data": state.get("sql_data"),
                "graph_traversal": state["graph_traversal"],
                "chunks": state["chunks"]
            }) + "\n"

        # Call LLM final synthesis with streaming enabled
        yield json.dumps({"type": "status", "content": "Menyusun tanggapan akhir..."}) + "\n"

        ollama_msgs = []
        for msg in state["messages"]:
            if isinstance(msg, SystemMessage):
                ollama_msgs.append({'role': 'system', 'content': msg.content})
            elif isinstance(msg, HumanMessage):
                ollama_msgs.append({'role': 'user', 'content': msg.content})
            elif isinstance(msg, AIMessage):
                d = {'role': 'assistant', 'content': msg.content}
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    d['tool_calls'] = []
                    for tc in msg.tool_calls:
                        d['tool_calls'].append({
                            'function': {
                                'name': tc['name'],
                                'arguments': tc['args']
                            }
                        })
                ollama_msgs.append(d)
            elif isinstance(msg, ToolMessage):
                ollama_msgs.append({'role': 'tool', 'content': msg.content})

        try:
            t0 = time.time()
            stream = self.ollama_client.chat(
                model=settings.ollama_model,
                messages=ollama_msgs,
                options={
                    "temperature": 0.0,
                    "num_ctx": 4096
                },
                stream=True  # Enable streaming mode
            )

            full_answer = ""
            for chunk in stream:
                token = chunk.get('message', {}).get('content', '')
                full_answer += token
                yield json.dumps({"type": "token", "content": token}) + "\n"

            llm_ms = round((time.time() - t0) * 1000, 1)

            final_step = {
                "step": "final_synthesis",
                "duration_ms": llm_ms,
                "model": settings.ollama_model,
            }
            state["steps"].append(final_step)

            # Signal end of stream with final payload details
            yield json.dumps({
                "type": "done",
                "answer": full_answer,
                "steps": state["steps"]
            }) + "\n"

        except Exception as e:
            logger.error(f"Error in streaming LLM response: {e}")
            yield json.dumps({"type": "error", "content": str(e)}) + "\n"
