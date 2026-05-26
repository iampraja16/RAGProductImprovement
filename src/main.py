import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from .agent import Agent

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="EMR Fault Analyzer API", version="2.0")

# Allow Streamlit frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Agent
agent = Agent()

# ===== Schemas =====

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    query: str
    chat_history: Optional[List[ChatMessage]] = []

class ChatResponse(BaseModel):
    answer: str
    chunks: Optional[List[str]] = []
    sql: Optional[str] = None
    token_usage: Optional[Dict[str, int]] = {}

# ===== Endpoints =====

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    try:
        # Convert Pydantic models to dicts for the agent
        chat_history = [msg.model_dump() for msg in request.chat_history] if request.chat_history else []
        
        response = agent.get_response(
            query=request.query,
            chat_history=chat_history
        )
        
        return ChatResponse(
            answer=response.get("answer", ""),
            chunks=response.get("chunks", []),
            sql=response.get("sql"),
            token_usage=response.get("token_usage", {})
        )
    except Exception as e:
        logger.error(f"Chat endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
