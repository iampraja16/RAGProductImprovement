"""Local Search Retriever."""

from typing import List, Dict, Any
from .base import BaseRetriever, SearchResult
from .hybrid import HybridEntityRetriever

class LocalSearchRetriever(BaseRetriever):
    def __init__(self, graph_client, llm, embedder):
        super().__init__(graph_client, llm, embedder)
        self.hybrid_search = HybridEntityRetriever(graph_client)

    def search(self, query: str, top_k: int = 5) -> SearchResult:
        query_vector = self.embedder.embed_query(query)
        entities = self.hybrid_search.retrieve(query, query_vector, top_k=top_k)

        if not entities:
            return SearchResult(answer="No relevant entities found in graph.")

        entity_names = [e["name"] for e in entities]
        
        context_query = """
        MATCH (e) WHERE e.name IN $names
        OPTIONAL MATCH (e)-[r]-(neighbor)
        RETURN e.name AS entity, type(r) AS relation, neighbor.name AS neighbor, labels(neighbor)[0] AS n_label
        LIMIT 50
        """
        rows = self.graph_client.run_query(context_query, {"names": entity_names})
        
        context_str = "Graph Context:\n"
        for row in rows:
            if row["neighbor"]:
                context_str += f"- {row['entity']} [{row['relation']}] {row['neighbor']} ({row['n_label']})\n"
            else:
                context_str += f"- {row['entity']} (No relations found)\n"

        prompt = f"""
        You are an expert EMR maintenance analyzer. Answer the user's question using ONLY the provided graph context.
        
        Question: {query}
        
        {context_str}
        
        Answer concisely and highlight specific parts or actions if they exist in the context.
        
        CRITICAL: At the end of your response, you must explicitly list the Neo4j Node names used as Evidence Sources, formatted as:
        Evidence Sources: Neo4j Node(s) [{', '.join(entity_names)}].
        """
        
        from langchain_core.messages import HumanMessage
        response = self.llm.invoke([HumanMessage(content=prompt)])
        
        return SearchResult(
            answer=response.content,
            graph_context={"entities_found": entity_names, "raw_rows": rows}
        )
