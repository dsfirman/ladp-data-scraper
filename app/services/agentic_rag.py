import json
import math
import re
import traceback
from datetime import datetime
from pathlib import Path
from langchain_experimental.text_splitter import SemanticChunker
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_openai import AzureOpenAIEmbeddings, AzureChatOpenAI
from chromadb import PersistentClient
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

import pandas as pd
from langchain_community.utilities.tavily_search import TavilySearchAPIWrapper

from app.config import settings
from app.core.prompts import EXTRACT_POI_PROMPT


def _clean_json(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Fix malformed openingHours values like "05/07/2026":"":"" → "05/07/2026": ""
    text = re.sub(r':\s*""\s*:\s*""', ': ""', text)
    return text


def _parse_json(text: str) -> dict:
    cleaned = _clean_json(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        col = int(re.search(r"column (\d+)", str(e)).group(1)) if re.search(r"column (\d+)", str(e)) else 0
        start = max(0, col - 100)
        end = min(len(cleaned), col + 100)
        print(f"[_parse_json] Failed to parse JSON (len={len(cleaned)}): {e}")
        print(f"[_parse_json] Around error column {col}: {cleaned[start:end]}")
        raise


CHROMA_DIR = Path(settings.data_dir) / "chroma_db"
DB_NAME = 'vectorstore'
COLLECTION_NAME = "places_of_interest"

POI_FIELDS = [
    "subjectName", "subjectType", "description",
    "locationName", "locationType", "address",
    "fee", "openingHours",
]
METRIC_FIELDS = ["description", "locationName"]

embeddings = AzureOpenAIEmbeddings(
    azure_deployment=settings.azure_openai_embedding_deployment,
    api_version=settings.azure_openai_embedding_api_version,
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
)

llm = AzureChatOpenAI(
    azure_deployment=settings.azure_openai_chat_deployment_name,
    api_version=settings.azure_openai_api_version,
    azure_endpoint=settings.azure_openai_endpoint,
    api_key=settings.azure_openai_api_key,
    temperature=0,
)


def create_vectorstore(chunks) -> Chroma:
    client = PersistentClient(path=DB_NAME)

    if COLLECTION_NAME in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION_NAME)

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DIR),
        collection_name=COLLECTION_NAME,
    )
    return vectorstore


def index_documents(data_filename: str | None = None) -> VectorStoreRetriever:
    data_dir = Path(settings.data_dir)
    if not data_dir.exists():
        raise FileNotFoundError(f"DATA_DIR '{data_dir}' does not exist")

    if data_filename:
        filepath = data_dir / data_filename
        if not filepath.exists():
            filepath = data_dir / f"{data_filename}.txt"
        if not filepath.exists():
            raise FileNotFoundError(f"Data file '{data_filename}' not found in {data_dir}")
        loader = TextLoader(str(filepath), encoding="utf-8")
        docs = loader.load()
    else:
        if CHROMA_DIR.exists():
            client = PersistentClient(path=str(CHROMA_DIR))
            if COLLECTION_NAME in [c.name for c in client.list_collections()]:
                existing = Chroma(
                    embedding_function=embeddings,
                    persist_directory=str(CHROMA_DIR),
                    collection_name=COLLECTION_NAME,
                )
                num_vectors = existing._collection.count()
                if num_vectors > 0:
                    print(f"Vector database already exists with {num_vectors} vectors, skipping indexing")
                    return existing.as_retriever(search_kwargs={"k": 3})
        loader = DirectoryLoader(
            str(data_dir), glob="*.txt", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"}
        )
        docs = loader.load()

    splitter = SemanticChunker(embeddings=embeddings, breakpoint_threshold_type="percentile", breakpoint_threshold_amount=70)
    all_chunks = []
    for doc in docs:
        text_chunks = splitter.split_text(doc.page_content)
        pos = 0
        for chunk_text in text_chunks:
            start_pos = doc.page_content.find(chunk_text, pos)
            if start_pos == -1:
                start_pos = pos
            start_line = doc.page_content[:start_pos].count('\n') + 1
            end_line = start_line + chunk_text.count('\n')
            pos = start_pos + len(chunk_text)
            chunk_doc = Document(
                page_content=chunk_text,
                metadata={**doc.metadata, "start_line": start_line, "end_line": end_line},
            )
            all_chunks.append(chunk_doc)
    chunks = all_chunks

    print(f"Number of chunks: {len(chunks)}")

    if data_filename and CHROMA_DIR.exists():
        client = PersistentClient(path=str(CHROMA_DIR))
        if COLLECTION_NAME in [c.name for c in client.list_collections()]:
            existing = Chroma(
                embedding_function=embeddings,
                persist_directory=str(CHROMA_DIR),
                collection_name=COLLECTION_NAME,
            )
            source_path = str(filepath.resolve())
            matching = existing._collection.get(where={"source": source_path})
            if matching and matching.get("ids"):
                print(f"Removing {len(matching['ids'])} existing chunks for '{data_filename}'")
                existing._collection.delete(ids=matching["ids"])
            existing.add_documents(chunks)
            print(f"Updated vectorstore with {len(chunks)} new chunks for '{data_filename}'")
            num_vectors = existing._collection.count()
            print(f"Total vectors: {num_vectors}")
            return existing.as_retriever(search_kwargs={"k": 3})

    vectorstore = create_vectorstore(chunks)
    collections = vectorstore._client.list_collections()
    print("Collections:", collections)

    num_vectors = vectorstore._collection.count()
    print(f"Number of vectors: {num_vectors}")

    vectorstore._collection.get(
    limit=1,
    include=["embeddings", "documents", "metadatas"],
    )

    return vectorstore.as_retriever(search_kwargs={"k": 3})


def create_custom_rag_prompt() -> PromptTemplate:
    return PromptTemplate.from_template(EXTRACT_POI_PROMPT)


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


def create_rag_chain(data_filename: str | None = None):
    index_documents(data_filename)
    prompt = create_custom_rag_prompt()

    vectorstore = Chroma(
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DIR),
        collection_name=COLLECTION_NAME,
    )

    def process_by_source(inputs):
        batch_size = inputs.get("batchSize")
        raw = vectorstore.get(include=["documents", "metadatas"])
        docs_by_source = {}
        for doc, meta in zip(raw["documents"], raw["metadatas"]):
            source = Path(meta.get("source", "unknown")).stem
            docs_by_source.setdefault(source, []).append(doc)

        tavily = None
        if settings.tavily_api_key:
            try:
                tavily = TavilySearchAPIWrapper(tavily_api_key=settings.tavily_api_key)
            except Exception:
                pass

        FIELD_QUERIES = {
            "subjectName": "name",
            "subjectType": "type category",
            "description": "description about",
            "locationName": "location venue name",
            "locationType": "venue type indoor outdoor",
            "address": "address location",
            "fee": "admission fee price cost",
            "startingDate": "start date event date",
            "endingDate": "end date event date",
            "openingHours": "opening hours operating hours",
        }

        def fill_missing_from_web(poi):
            if not tavily:
                return
            name = poi.get("subjectName") or poi.get("locationName") or ""
            if not name:
                return
            for key in POI_FIELDS:
                if poi.get(key) is not None and str(poi[key]).strip():
                    continue
                query = f"{name} {FIELD_QUERIES.get(key, key)}"
                try:
                    raw = tavily.raw_results(query, max_results=3, include_answer=True)
                    answer = raw.get("answer") or ""
                    results = raw.get("results", [])
                    content = answer or (results[0]["content"] if results else "")
                    if content.strip():
                        if key == "address":
                            poi[key] = [{"text": content.strip()}]
                        else:
                            poi[key] = content.strip()
                except Exception:
                    pass

        search_cache = {}

        def get_provenance(value_str):
            key = str(value_str)
            if key in search_cache:
                return search_cache[key]
            try:
                results = vectorstore.similarity_search_with_relevance_scores(key, k=3)
            except Exception as e:
                traceback.print_exc()
                print(f"[get_provenance] similarity_search failed: {e}")
                return None
            if results:
                chunk, score = results[0]
                info = {
                    "similarity_score": round(float(max(0, score)), 4),
                    "line_number": f"{chunk.metadata.get('start_line', '?')} - {chunk.metadata.get('end_line', '?')}",
                    "source": Path(chunk.metadata.get("source", "unknown")).stem,
                }
                search_cache[key] = info
                return info
            return None

        all_pois = []
        for source, docs in docs_by_source.items():
            context = "\n\n".join(docs)
            answer = (prompt | llm | StrOutputParser()).invoke({
                "context": context,
                "batchSize": batch_size if batch_size is not None else 999,
                "currentDate": datetime.now().strftime("%d/%m/%Y"),
            })
            data = _parse_json(answer)
            for poi in data.get("pointsOfInterest", []):
                fill_missing_from_web(poi)
                poi["source"] = source
                provenance = {}
                all_sims = []
                for key in METRIC_FIELDS:
                    value = poi.get(key)
                    str_val = str(value) if value is not None else ""
                    if str_val.strip():
                        info = get_provenance(str_val)
                        if info:
                            provenance[key] = info
                            sim = info["similarity_score"]
                            poi[f"{key}_sim"] = sim
                            all_sims.append(sim)
                            continue
                    poi[f"{key}_sim"] = None
                if provenance:
                    poi["_provenance"] = provenance
                n = len(all_sims)
                poi["sim_overall_geomean"] = round(float(abs(math.prod(all_sims)) ** (1 / n)), 4) if n else None
            all_pois.extend(data.get("pointsOfInterest", []))

        return {"pointsOfInterest": all_pois}

    return RunnablePassthrough.assign(result=process_by_source)


def run_extraction(batch_size: int | None = None, data_filename: str | None = None) -> dict:
    try:
        chain = create_rag_chain(data_filename)
        result = chain.invoke({"batchSize": batch_size})
        data = result["result"]
    except Exception as e:
        traceback.print_exc()
        print(f"[run_extraction] Fatal error: {e}")
        raise
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(settings.data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"extraction_{ts}.json"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved JSON to {json_path}")

    if pois := data.get("pointsOfInterest"):
        flat = []
        for poi in pois:
            row = {}
            for k, v in poi.items():
                if k == "_provenance":
                    continue
                row[k] = json.dumps(v, ensure_ascii=False) if not isinstance(v, (str, int, float, type(None))) else v
            flat.append(row)
        csv_path = out_dir / f"extraction_{ts}.csv"
        pd.DataFrame(flat).to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"Saved CSV to {csv_path}")

    return data
