from src.graph.client import GraphClient
from src.config import settings

client = GraphClient(uri=settings.neo4j_uri, user=settings.neo4j_user, password=settings.neo4j_password)

nodes = client.driver.session().run('MATCH (n:ActionPattern) WHERE n.embedding IS NOT NULL RETURN n.embedding LIMIT 1').data()
if not nodes:
    print("No nodes found!")
    exit(1)
    
embedding = nodes[0]['n.embedding']

try:
    similar = client.driver.session().run(
        "CALL db.index.vector.queryNodes('action-embeddings', 10, $embedding) YIELD node, score RETURN score", 
        {'embedding': embedding}
    ).data()
    print("Success! Scores:", similar)
except Exception as e:
    print("Error querying vector index:", e)
