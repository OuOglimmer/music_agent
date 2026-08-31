import logging
from threading import Lock

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.config import config
from app.rag.embedding import get_embeddings

logger = logging.getLogger(__name__)

# 全局共享知识库的作用域，通过 build_knowledge_base.py 导入的文档对所有会话可见。
GLOBAL_SCOPE = "global"

_store: Chroma | None = None
_lock = Lock()


def get_vectorstore() -> Chroma:
    """返回持久化的 Chroma 向量库单例。"""
    global _store
    if _store is not None:
        return _store

    with _lock:
        if _store is None:
            _store = Chroma(
                collection_name=config.collection_name,
                embedding_function=get_embeddings(),
                persist_directory=str(config.chroma_dir),
            )
    return _store


def ingest_documents(
    session_id: str, documents: list[tuple[str, str]]
) -> int:
    """把 (文件名, 文本) 切块后写入向量库，返回写入的文本块数量。"""
    if not documents:
        return 0
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )
    docs: list[Document] = []
    for filename, text in documents:
        source_doc = Document(
            page_content=text,
            metadata={"source": filename, "session_id": session_id},
        )
        docs.extend(splitter.split_documents([source_doc]))
    if docs:
        get_vectorstore().add_documents(docs)
    logger.info(
        "Ingested %s chunks from %s document(s) into session %s",
        len(docs),
        len(documents),
        session_id,
    )
    return len(docs)


def retrieve_context(session_id: str, query: str, k: int | None = None) -> str:
    """检索与问题相关的片段（当前会话 + 全局知识库），拼接为参考资料。"""
    if not query.strip():
        return ""
    found = get_vectorstore().similarity_search(
        query,
        k=k or config.top_k,
        filter={
            "$or": [{"session_id": session_id}, {"session_id": GLOBAL_SCOPE}]
        },
    )
    if not found:
        return ""
    return "\n\n".join(
        f"[{doc.metadata.get('source', '文档')}] {doc.page_content}"
        for doc in found
    )
