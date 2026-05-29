import json
import logging
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
    token_usage: Dict[str, int]

class Agent:
    def __init__(self):
        # We don't bind tools to the LLM directly here if using local Ollama.
        # Instead, we will pass the schemas to the Ollama client in call_llm
        self.llm = get_llm(temperature=0.0)
        self.tools = {t.__name__: t for t in REGISTERED_TOOLS}
        self.tool_schemas = get_tool_schemas()
        
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
            client = ollama.Client(host=settings.ollama_base_url)
            
            response = client.chat(
                model=settings.ollama_model,
                messages=ollama_msgs,
                tools=self.tool_schemas,
                options={"temperature": 0.0}
            )
            
            msg_dict = response.get('message', {})
            content = msg_dict.get('content', '')
            
            # Convert back to LangChain AIMessage
            ai_msg = AIMessage(content=content)
            
            # Extract tool calls if any
            if 'tool_calls' in msg_dict and msg_dict['tool_calls']:
                tool_calls = []
                for tc in msg_dict['tool_calls']:
                    function_def = tc.get('function', {})
                    tool_calls.append({
                        "name": function_def.get('name'),
                        "args": function_def.get('arguments', {}),
                        "id": f"call_{len(tool_calls)}" # Dummy ID
                    })
                ai_msg.tool_calls = tool_calls
                
            return {"messages": [ai_msg]}
            
        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            return {"messages": [AIMessage(content="I encountered an error while processing your request.")]}

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
        
        for tool_call in tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
            
            if tool_name not in self.tools:
                results.append(ToolMessage(
                    content=f"Error: Tool {tool_name} not found.", 
                    tool_call_id=tool_call.get("id", "unknown")
                ))
                continue
                
            try:
                tool_func = self.tools[tool_name]
                # Assuming the tool returns a dictionary with 'answer', 'chunks', 'sql'
                output = tool_func(**tool_args)
                
                tool_msg_content = str(output.get("answer", output))
                if output.get("chunks"):
                    chunks.extend(output.get("chunks", []))
                if output.get("sql"):
                    sql = output.get("sql")
                    
                results.append(ToolMessage(
                    content=tool_msg_content, 
                    name=tool_name, 
                    tool_call_id=tool_call.get("id", "unknown")
                ))
            except Exception as e:
                logger.error(f"Error executing tool {tool_name}: {e}")
                results.append(ToolMessage(
                    content=f"Error executing tool: {e}", 
                    name=tool_name, 
                    tool_call_id=tool_call.get("id", "unknown")
                ))

        state_update = {"messages": results}
        if chunks:
            state_update["chunks"] = chunks
        if sql:
            state_update["sql"] = sql
            
        return state_update

    def get_response(self, query: str, chat_history: List[Dict[str, str]] = None) -> Dict[str, Any]:
        """Entry point for the API."""
        messages = [SystemMessage(content=system_prompt)]
        
        if chat_history:
            for msg in chat_history:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("content")))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg.get("content")))
                    
        messages.append(HumanMessage(content=query))
        
        initial_state = {
            "messages": messages,
            "chunks": [],
            "sql": None,
            "token_usage": {}
        }
        
        try:
            # Run the graph
            result = self.graph.invoke(initial_state)
            
            # Extract final response
            final_msg = result['messages'][-1].content
            
            return {
                "answer": final_msg,
                "chunks": result.get("chunks", []),
                "sql": result.get("sql"),
                "token_usage": result.get("token_usage", {})
            }
        except Exception as e:
            logger.error(f"Error in graph execution: {e}")
            return {
                "answer": f"An error occurred: {str(e)}",
                "chunks": [],
                "sql": None,
                "token_usage": {}
            }
