"""Base classes and types for retrieval modes."""

from typing import List, Dict, Any
from dataclasses import dataclass, field

@dataclass
class SearchResult:
    """Standardized output format for all retrieval modes."""
    answer: str
    chunks: List[str] = field(default_factory=list)
    graph_context: Dict[str, Any] = field(default_factory=dict)
    
class BaseRetriever:
    """Base class for all retrieval strategies."""
    def __init__(self, graph_client, llm, embedder):
        self.graph_client = graph_client
        self.llm = llm
        self.embedder = embedder

    def search(self, query: str, **kwargs) -> SearchResult:
        raise NotImplementedError("Subclasses must implement search()")
