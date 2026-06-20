import logging
import threading
from typing import Dict, Any

logger = logging.getLogger(__name__)

class TokenMonitor:
    """Centralized token usage tracking and cost estimation."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost = 0.0
        
    def add_usage(self, prompt_tokens: int, completion_tokens: int, cost: float):
        """Thread-safe update of global usage statistics."""
        with self._lock:
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.total_cost += cost
            
        logger.info(f"Token Usage Added: {prompt_tokens} prompt | {completion_tokens} completion | Cost: ${cost:.5f}")
            
    def get_session_stats(self) -> Dict[str, Any]:
        """Retrieve aggregated token usage for the entire session."""
        with self._lock:
            return {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
                "estimated_cost_usd": self.total_cost
            }
            
    def estimate_fallback(self, prompt_text: str, completion_text: str, model: str = "gpt-4o") -> Dict[str, Any]:
        """Fallback heuristics if LangChain callback fails to capture streaming tokens."""
        from src.agent.prompts import estimate_tokens
        p_tokens = estimate_tokens(prompt_text)
        c_tokens = estimate_tokens(completion_text)
        
        # Approximate Azure OpenAI pricing (e.g., $5.00/1M input, $15.00/1M output for gpt-4o)
        cost = (p_tokens * 0.000005) + (c_tokens * 0.000015)
        
        return {
            "prompt_tokens": p_tokens,
            "completion_tokens": c_tokens,
            "total_tokens": p_tokens + c_tokens,
            "estimated_cost_usd": cost
        }

# Global singleton for cross-request tracking
global_token_monitor = TokenMonitor()
