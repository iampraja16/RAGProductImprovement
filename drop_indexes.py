from src.graph.client import GraphClient
from src.config import settings

client = GraphClient(uri=settings.neo4j_uri, user=settings.neo4j_user, password=settings.neo4j_password)

indexes = [
    'symptom-embeddings', 
    'cluster-embeddings', 
    'rootcause-embeddings', 
    'action-embeddings', 
    settings.neo4j_vector_index_community
]

for i in indexes:
    try:
        client.driver.session().run(f"DROP INDEX `{i}` IF EXISTS")
        print(f"Dropped index: {i}")
    except Exception as e:
        print(f"Failed to drop {i}: {e}")

print("Done dropping old vector indexes!")
