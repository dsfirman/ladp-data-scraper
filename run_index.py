import os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ["DATA_DIR"] = os.path.join(os.path.dirname(__file__), "data")

from app.services.agentic_rag import index_documents

retriever = index_documents()
print(f"\nRetriever type: {type(retriever).__name__}")
