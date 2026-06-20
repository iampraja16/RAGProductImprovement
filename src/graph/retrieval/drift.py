"""DRIFT Search Retriever (Dynamic Retrieval & Information Fetching Strategy)."""

from typing import List, Dict, Any
from .base import BaseRetriever, SearchResult
from .hybrid import HybridEntityRetriever

class DriftSearchRetriever(BaseRetriever):
    def __init__(self, graph_client, llm, embedder):
        super().__init__(graph_client, llm, embedder)
        self.hybrid_search = HybridEntityRetriever(graph_client)

    def search(self, query: str, top_k: int = 5) -> SearchResult:
        query_vector = self.embedder.embed_query(query)
        
        # 1. Hybrid search for seed entities
        entities = self.hybrid_search.retrieve(query, query_vector, top_k=top_k)
        if not entities:
            return SearchResult(answer="No relevant entities found to start DRIFT search.")

        entity_names = [e["name"] for e in entities]

        # 2. Expand to Communities
        # Get communities that these entities belong to, and their summaries
        drift_query = """
        MATCH (e) WHERE e.name IN $names
        MATCH (e)-[:IN_COMMUNITY]->(c:Community)
        RETURN DISTINCT c.communityId AS id, c.summary AS summary, c.level AS level
        ORDER BY c.level ASC
        LIMIT 5
        """
        communities = self.graph_client.run_query(drift_query, {"names": entity_names})

        # 3. Get immediate graph context for seed entities
        context_query = """
        MATCH (e) WHERE e.name IN $names
        OPTIONAL MATCH (e)-[r]-(neighbor)
        RETURN e.name AS entity, type(r) AS relation, neighbor.name AS neighbor, labels(neighbor)[0] AS n_label
        LIMIT 30
        """
        local_context = self.graph_client.run_query(context_query, {"names": entity_names})

        # 4. Format context for LLM
        context_str = "=== LOCAL ENTITY CONTEXT ===\n"
        for row in local_context:
            if row["neighbor"]:
                context_str += f"{row['entity']} [{row['relation']}] {row['neighbor']}\n"
        
        context_str += "\n=== BROADER COMMUNITY CONTEXT ===\n"
        for comm in communities:
            context_str += f"Community Level {comm['level']}:\n{comm['summary']}\n\n"

        # 5. LLM Synthesis
        prompt = f"""
        You are an expert EMR maintenance analyzer. 
        Answer the user's question using both the specific local entity relationships and the broader community summaries provided below.
        
        Question: {query}
        
        {context_str}
        
        Synthesize the specific details with the high-level patterns to provide a comprehensive answer.
        
        CRITICAL: At the end of your response, you must explicitly list the Neo4j Node names and Community IDs used as Evidence Sources, formatted as:
        Evidence Sources: Neo4j Node(s) [{', '.join(entity_names)}], Community ID(s) [{', '.join(str(c['id']) for c in communities)}].
        """
        
        from langchain_core.messages import HumanMessage
        response = self.llm.invoke([HumanMessage(content=prompt)])
        
        return SearchResult(
            answer=response.content,
            graph_context={
                "seed_entities": entity_names, 
                "communities_used": [c['id'] for c in communities],
                "raw_rows": local_context
            }
        )
