"""Global Search Retriever (Community Map-Reduce)."""

from typing import List, Dict, Any
from .base import BaseRetriever, SearchResult

class GlobalSearchRetriever(BaseRetriever):
    def search(self, query: str, level: int = 2, top_k: int = 5) -> SearchResult:
        query_vector = self.embedder.embed_query(query)
        
        community_query = """
        CALL db.index.vector.queryNodes('community-embeddings', $k, $vector)
        YIELD node, score
        WHERE node.level = $level
        RETURN node.communityId AS id, node.summary AS summary, score
        """
        communities = self.graph_client.run_query(community_query, {
            "k": top_k, "vector": query_vector, "level": level
        })
        
        if not communities:
            return SearchResult(answer="No community summaries found at this level.")

        from langchain_core.messages import HumanMessage
        
        partial_answers = []
        for comm in communities:
            map_prompt = f"""
            Based on the following community summary, answer the user's question. 
            If the summary does not contain relevant information, reply exactly with "NO_RELEVANT_INFO".
            
            Summary: {comm['summary']}
            Question: {query}
            """
            resp = self.llm.invoke([HumanMessage(content=map_prompt)]).content
            if "NO_RELEVANT_INFO" not in resp:
                partial_answers.append(resp)

        if not partial_answers:
            return SearchResult(answer="I couldn't find a high-level answer to this question in the community data.")

        combined_partials = "\n---\n".join(partial_answers)
        reduce_prompt = f"""
        You are an expert EMR analyst looking at high-level trends.
        Synthesize the following partial findings into a cohesive, comprehensive answer to the user's question.
        
        Question: {query}
        
        Findings:
        {combined_partials}
        
        CRITICAL: At the end of your response, you must explicitly list the Community IDs used as Evidence Sources, formatted as:
        Evidence Sources: Community ID(s) [{', '.join(str(c['id']) for c in communities)}].
        """
        final_response = self.llm.invoke([HumanMessage(content=reduce_prompt)])

        return SearchResult(
            answer=final_response.content,
            graph_context={"communities_searched": len(communities), "partials_used": len(partial_answers)}
        )
