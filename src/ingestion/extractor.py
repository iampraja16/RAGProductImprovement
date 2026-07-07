"""LLM-based Graph Extractor using LangChain Structured Outputs."""

import logging
from typing import Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from src.config import settings
from src.services.providers import get_llm
from src.ingestion.schema import GraphExtraction

logger = logging.getLogger(__name__)

class LLMGraphExtractor:
    def __init__(self, temperature: float = 0.0):
        self.llm = get_llm(temperature)
        self.parser = JsonOutputParser(pydantic_object=GraphExtraction)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", """You are an expert heavy machinery maintenance analyst.
Your task is to extract structured entities (nodes) and relationships (edges) from unstructured maintenance logs (EMR).

Rules:
1. ONLY extract nodes with the following labels: SymptomPattern, ProblemCluster, RootCausePattern, ActionPattern, Part, MachineModel, Component
2. ONLY extract relationships with the following types: EXHIBITED, CAUSED_BY, RESOLVED_BY, INVOLVES_PART, BELONGS_TO, AFFECTS_COMPONENT, PART_OF
3. VERY IMPORTANT: You MUST aggressively extract ActionPattern from the "Action Taken" field. Do not ignore it!
4. VERY IMPORTANT: You MUST aggressively extract Component nodes from the text, and link Symptoms/Causes to them using AFFECTS_COMPONENT.
5. Do NOT hallucinate. Only extract what is explicitly mentioned or strongly implied in the text.
6. Normalize the names slightly (e.g., lowercase, remove leading/trailing spaces) to help with deduplication.

Output FORMAT INSTRUCTIONS:
{format_instructions}
"""),
            ("user", "Extract graph data from this maintenance record:\n\n{text}")
        ])
        
        self.chain = self.prompt | self.llm | self.parser

    def extract(self, text: str) -> Optional[GraphExtraction]:
        """Extracts GraphExtraction objects from raw text."""
        try:
            result_dict = self.chain.invoke({
                "text": text,
                "format_instructions": self.parser.get_format_instructions()
            })
            return GraphExtraction(**result_dict)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse extraction result: {e}")
            return None
