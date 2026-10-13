import re
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
_vectorstore: Any = None


def _policy_chunks() -> list[dict[str, Any]]:
    chunks = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = next((line.lstrip("# ") for line in text.splitlines() if line.startswith("#")), path.stem)
        section = "Overview"
        current_lines = []
        paragraphs = []
        for line in text.splitlines():
            if line.startswith("#"):
                if current_lines:
                    paragraphs.append((section, " ".join(current_lines).strip()))
                    current_lines = []
                if line.startswith("##"):
                    section = line.lstrip("# ").strip()
                continue
            if line.strip():
                current_lines.append(line.strip())
            elif current_lines:
                paragraphs.append((section, " ".join(current_lines).strip()))
                current_lines = []
        if current_lines:
            paragraphs.append((section, " ".join(current_lines).strip()))
        splitter = RecursiveCharacterTextSplitter(chunk_size=650, chunk_overlap=90, add_start_index=True)
        for section, paragraph in paragraphs:
            source_chunks = splitter.create_documents([paragraph])
            for chunk in source_chunks:
                chunks.append({
                    "content": chunk.page_content,
                    "metadata": {
                        "document_id": path.stem,
                        "title": title,
                        "source": path.name,
                        "section": section,
                        "version": "1.0",
                        "page": len(chunks) + 1,
                        "category": path.stem.split("-")[0],
                        "effective_date": "2026-01-01",
                        "start_index": chunk.metadata["start_index"],
                    },
                })
    return chunks


def _get_vectorstore() -> Any:
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore
    from langchain_chroma import Chroma
    from langchain_openai import OpenAIEmbeddings

    embeddings = OpenAIEmbeddings(api_key=settings.openai_api_key)
    if settings.chroma_url:
        from chromadb import HttpClient
        host, _, port_text = settings.chroma_url.removeprefix("http://").removeprefix("https://").partition(":")
        client = HttpClient(host=host, port=int(port_text or "8000"))
        _vectorstore = Chroma(
            client=client,
            collection_name="fulfillment_policies",
            embedding_function=embeddings,
        )
    else:
        _vectorstore = Chroma(
            collection_name="fulfillment_policies",
            embedding_function=embeddings,
            persist_directory="./.chroma",
        )
    if _vectorstore._collection.count() == 0:
        chunks = _policy_chunks()
        if chunks:
            _vectorstore.add_texts(
                texts=[item["content"] for item in chunks],
                metadatas=[item["metadata"] for item in chunks],
            )
    return _vectorstore


def retrieve_policy_context(query: str, category: str = "") -> tuple[str, list[dict[str, Any]]]:
    candidates = _policy_chunks()
    category_filter = {"shipment": "carrier", "fulfillment": "warehouse"}.get(category.lower(), category.lower())
    if category_filter:
        filtered = [item for item in candidates if item["metadata"].get("category") == category_filter]
        if filtered:
            candidates = filtered
    lexical = sorted(
        candidates,
        key=lambda item: len(set(re.findall(r"[a-z0-9]+", query.lower())) & set(re.findall(r"[a-z0-9]+", (item["metadata"].get("title", "") + " " + item["content"]).lower()))),
        reverse=True,
    )
    fused: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(lexical[:12], start=1):
        fused[item["content"]] = {**item, "score": 1 / (60 + rank)}
    if settings.openai_api_key:
        try:
            store = _get_vectorstore()
            vector_filter = {"category": category_filter} if category_filter else None
            try:
                matches = store.max_marginal_relevance_search(query, k=8, fetch_k=20, filter=vector_filter)
            except (TypeError, ValueError):
                matches = store.max_marginal_relevance_search(query, k=8, fetch_k=20)
            for rank, item in enumerate(matches, start=1):
                content = item.page_content
                existing = fused.get(content, {"content": content, "metadata": item.metadata, "score": 0})
                existing["score"] += 1 / (60 + rank)
                fused[content] = existing
        except Exception:
            pass
    query_terms = set(re.findall(r"[a-z0-9]+", query.lower()))
    chunks = sorted(
        fused.values(),
        key=lambda item: (item["score"], len(query_terms & set(re.findall(r"[a-z0-9]+", (item["metadata"].get("title", "") + " " + item["content"]).lower())))),
        reverse=True,
    )[:4]
    for item in chunks:
        sentences = re.split(r"(?<=[.!?])\s+", item["content"])
        relevant = [sentence for sentence in sentences if query_terms & set(re.findall(r"[a-z0-9]+", sentence.lower()))]
        item["content"] = " ".join(relevant[:3]) or item["content"][:320]
    citations = [
        {**item["metadata"], "quote": item["content"][:320]}
        for item in chunks
    ]
    context = "\n\n".join(
        f"[{item['metadata']['title']} / {item['metadata']['section']}] {item['content']}"
        for item in chunks
    )
    return context, citations