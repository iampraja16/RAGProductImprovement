"""Neo4j Index Management. Setup script for Vector and Fulltext indexes."""

import logging
from src.config import settings
from src.graph.client import GraphClient

logger = logging.getLogger(__name__)

class IndexManager:
    def __init__(self, client: GraphClient):
        self.client = client

    def setup_all_indexes(self):
        logger.info("Setting up Neo4j Vector and Fulltext Indexes...")
        
        self._create_fulltext_index(
            index_name=settings.neo4j_fulltext_index_entity,
            labels=["SymptomPattern", "ProblemCluster", "RootCausePattern", "ActionPattern", "Part"],
            properties=["name", "description", "label", "part_no", "raw_symptom", "raw_cause", "raw_action"]
        )

        self._create_vector_index("symptom-embeddings", "SymptomPattern", "embedding")
        self._create_vector_index("cluster-embeddings", "ProblemCluster", "embedding")
        self._create_vector_index("rootcause-embeddings", "RootCausePattern", "embedding")
        self._create_vector_index("action-embeddings", "ActionPattern", "embedding")    
        self._create_vector_index(settings.neo4j_vector_index_community, "Community", "embedding")
        self.ensure_ppi_indexes()
        self._create_constraints()
        
        logger.info("All Neo4j indexes setup successfully.")

    def ensure_ppi_indexes(self):
        self._create_vector_index("ppi-embeddings", "PPI", "embedding")
        self._create_fulltext_index(
            "ppi-fulltext",
            ["PPI"],
            ["external_id", "improvement_name", "phenomenon", "corrective_action", "symptom", "component", "summary_text"]
        )
        try:
            self.client.run_query("""
                CREATE CONSTRAINT ppi_external_id_unique IF NOT EXISTS
                FOR (p:PPI) REQUIRE p.external_id IS UNIQUE
            """)
            logger.info("PPI uniqueness constraint ensured.")
        except Exception as e:
            logger.warning(f"PPI uniqueness constraint failed: {e}")

    def _create_constraints(self):
        try:
            self.client.run_query("""
                CREATE CONSTRAINT community_unique IF NOT EXISTS
                FOR (c:Community)
                REQUIRE (c.communityId, c.level) IS UNIQUE
            """)
            logger.info("Community uniqueness constraint ensured.")
        except Exception as e:
            logger.warning(f"Community uniqueness constraint failed: {e}")

    def _create_vector_index(self, index_name: str, label: str, property_name: str):
        query = f"""
        CREATE VECTOR INDEX `{index_name}` IF NOT EXISTS
        FOR (n:{label})
        ON (n.{property_name})
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: $dimensions,
                `vector.similarity_function`: 'cosine'
            }}
        }}
        """
        try:
            self.client.run_query(query, {"dimensions": settings.embedding_dimension})
            logger.info(f"Vector index '{index_name}' ensured on :{label}({property_name})")
        except Exception as e:
            logger.error(f"Error creating vector index {index_name}: {e}")

    def _create_fulltext_index(self, index_name: str, labels: list[str], properties: list[str]):
        labels_str = "|".join(labels)
        props_str = ", ".join([f"n.{p}" for p in properties])
        query = f"""
        CREATE FULLTEXT INDEX `{index_name}` IF NOT EXISTS
        FOR (n:{labels_str})
        ON EACH [{props_str}]
        """
        try:
            self.client.run_query(query)
            logger.info(f"Fulltext index '{index_name}' ensured on labels {labels}")
        except Exception as e:
            logger.error(f"Error creating fulltext index {index_name}: {e}")
