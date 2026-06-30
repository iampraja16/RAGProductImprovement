"""Community Summarization via LLM."""

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from src.config import settings
from src.graph.client import GraphClient
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

class CommunitySummarizer:
    def __init__(self, client: GraphClient, llm, embedder, max_workers: int = 4, timeout_per_community: int = 120):
        self.client = client
        self.llm = llm
        self.embedder = embedder
        self.max_workers = max_workers
        self.timeout_per_community = timeout_per_community

    def _summarize_one(self, community_id: str, level: int) -> None:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if level == 0:
                    self._summarize_level_0(community_id)
                else:
                    self._summarize_higher_level(community_id, level)
                return
            except Exception as e:
                is_last = attempt == max_retries - 1
                if is_last:
                    logger.error(
                        "Failed to summarize community %s after %d attempts: %s",
                        community_id, max_retries, e
                    )
                    return
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "Attempt %d failed for community %s: %s. Retrying in %.1fs...",
                    attempt + 1, community_id, e, wait
                )
                time.sleep(wait)

    def summarize_all(self):
        logger.info("Starting Hierarchical Community Summarization...")
        
        query_levels = "MATCH (c:Community) RETURN max(c.level) AS max_level"
        res = self.client.run_query(query_levels)
        max_level = res[0]['max_level'] if res and res[0]['max_level'] is not None else 0
        
        for current_level in range(max_level + 1):
            logger.info(f"Summarizing Level {current_level} communities...")
            query = "MATCH (c:Community) WHERE c.level = $level AND c.summary IS NULL RETURN c.communityId AS id"
            communities = self.client.run_query(query, {"level": current_level})
            
            if not communities:
                logger.info(f"All Level {current_level} communities already summarized.")
                continue

            logger.info(f"Found {len(communities)} Level {current_level} communities to summarize.")

            if self.max_workers <= 1:
                for comm in communities:
                    self._summarize_one(comm["id"], current_level)
            else:
                with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    futures = {
                        pool.submit(self._summarize_one, comm["id"], current_level): comm["id"]
                        for comm in communities
                    }
                    for future in as_completed(futures):
                        c_id = futures[future]
                        try:
                            future.result(timeout=self.timeout_per_community)
                        except TimeoutError:
                            logger.error(
                                "Community %s summarization timed out after %ds",
                                c_id, self.timeout_per_community
                            )
                        except Exception as e:
                            logger.error(
                                "Unexpected error in community %s summarization: %s",
                                c_id, e
                            )

        logger.info("Hierarchical Community Summarization completed.")

    def _summarize_level_0(self, community_id: str):
        query = """
        MATCH (n)-[:IN_COMMUNITY]->(c:Community {communityId: $id})
        RETURN n.name AS name, labels(n)[0] AS label
        """
        entities = self.client.run_query(query, {"id": community_id})
        
        if not entities:
            return

        entity_list = "\n".join([f"- {e['name']} ({e['label']})" for e in entities])
        
        prompt = f"""
        You are an expert EMR maintenance analyst. 
        The following entities have been grouped together by a community detection algorithm because they frequently occur together in maintenance records.
        
        Entities:
        {entity_list}
        
        Write a concise, professional summary (1-3 paragraphs) explaining the likely common theme, system, or failure pattern that connects these entities.
        """
        
        logger.info(f"Summarizing Level 0 community {community_id} ({len(entities)} entities)...")
        response = self.llm.invoke([HumanMessage(content=prompt)])
        summary_text = response.content
        
        embedding = self.embedder.embed_query(summary_text)
        
        save_query = """
        MATCH (c:Community {communityId: $id, level: 0})
        SET c.summary = $summary, c.embedding = $embedding
        """
        self.client.run_query(save_query, {"id": community_id, "summary": summary_text, "embedding": embedding})

    def _summarize_higher_level(self, community_id: str, level: int):
        query = """
        MATCH (c:Community {communityId: $id, level: $level})-[:PARENT_OF]->(child:Community)
        RETURN child.communityId AS child_id, child.summary AS summary
        """
        children = self.client.run_query(query, {"id": community_id, "level": level})
        
        if not children:
            return
            
        child_summaries = "\\n\\n".join([f"--- Sub-Community {child['child_id']} ---\\n{child['summary']}" for child in children if child['summary']])
        
        if not child_summaries.strip():
             child_summaries = "No detailed summaries available for sub-communities."
             
        prompt = f"""
        You are an expert EMR maintenance analyst. 
        You are looking at a high-level community (Level {level}) of maintenance records.
        This community is composed of several sub-communities. 
        
        Sub-Community Summaries:
        {child_summaries}
        
        Write a concise, professional executive summary (1-3 paragraphs) explaining the overarching theme, system, or global failure pattern that connects these sub-communities. Focus on the macro-level insights.
        """
        
        logger.info(f"Summarizing Level {level} community {community_id} ({len(children)} sub-communities)...")
        response = self.llm.invoke([HumanMessage(content=prompt)])
        summary_text = response.content
        
        embedding = self.embedder.embed_query(summary_text)
        
        save_query = """
        MATCH (c:Community {communityId: $id, level: $level})
        SET c.summary = $summary, c.embedding = $embedding
        """
        self.client.run_query(save_query, {"id": community_id, "level": level, "summary": summary_text, "embedding": embedding})
