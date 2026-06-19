from typing import List, Dict, Any
from pydantic import BaseModel, Field

# 1. NODE TYPES & DEFINITIONS
# Tuning Guide: If the LLM misses entities, add examples in the descriptions.
ALLOWED_NODES = {
    "SymptomPattern": "A specific failure, error code, or physical complaint observed (e.g., 'Oli Bocor', 'Error E03').",
    "MachineModel": "The specific heavy equipment or machine model (e.g., 'PC200-8', 'HD785').",
    "Part": "A physical component or spare part mentioned (e.g., 'O-Ring', 'Hose').",
    "ActionPattern": "A maintenance action or repair performed (e.g., 'Ganti Hose', 'Cleaning').",
    "RootCausePattern": "The underlying technical reason for the failure (e.g., 'Seal Aus', 'Korsleting').",
    "ProblemCluster": "A high-level system category (e.g., 'Hydraulic System', 'Engine').",
    "Component": "A major sub-system, machine component, or techcare category (e.g., 'FINAL DRIVE', 'SWING MOTOR', 'CABIN')."
}

# 2. RELATIONSHIP TYPES & DEFINITIONS
# Tuning Guide: Define strict rules for what can connect to what.
ALLOWED_RELATIONSHIPS = {
    "EXHIBITED": "MachineModel EXHIBITED a SymptomPattern.",
    "CAUSED_BY": "SymptomPattern was CAUSED_BY a RootCausePattern.",
    "RESOLVED_BY": "SymptomPattern or RootCausePattern was RESOLVED_BY an ActionPattern.",
    "INVOLVES_PART": "ActionPattern or RootCausePattern INVOLVES_PART a Part.",
    "BELONGS_TO": "Part or SymptomPattern BELONGS_TO a ProblemCluster.",
    "AFFECTS_COMPONENT": "SymptomPattern or RootCausePattern AFFECTS_COMPONENT a Component.",
    "PART_OF": "Part PART_OF a Component."
}

_NODE_DESC = "Type of the node. Must be exactly one of the following:\n" + "\n".join([f"- {k}: {v}" for k, v in ALLOWED_NODES.items()])
_REL_DESC = "Type of relationship. Must be exactly one of the following:\n" + "\n".join([f"- {k}: {v}" for k, v in ALLOWED_RELATIONSHIPS.items()])

class ExtractedNode(BaseModel):
    label: str = Field(description=_NODE_DESC)
    name: str = Field(description="The exact canonical name or identifier of the entity. Normalize text by removing extra spaces and standardizing casing. E.g., 'Oli Bocor', 'PC200-8'")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Additional properties like description, part_no. Leave empty if none.")

class ExtractedRelationship(BaseModel):
    source: str = Field(description="The exact 'name' of the source ExtractedNode")
    target: str = Field(description="The exact 'name' of the target ExtractedNode")
    type: str = Field(description=_REL_DESC)
    properties: Dict[str, Any] = Field(default_factory=dict, description="Additional relationship properties. Leave empty if none.")

class GraphExtraction(BaseModel):
    """The complete graph extracted from a text chunk."""
    nodes: List[ExtractedNode] = Field(description="List of all unique entities found in the text")
    relationships: List[ExtractedRelationship] = Field(description="List of all valid relationships connecting the extracted nodes")
