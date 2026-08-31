"""把 knowledge_base/ 目录下的 txt/md 文档批量导入向量知识库。

导入的文档进入全局作用域（GLOBAL_SCOPE），对所有会话的检索可见。
用法：uv run python -m app.rag.build_knowledge_base
"""

import logging
from pathlib import Path

from app.rag import retriever
from app.rag.config import config

KB_DIR = Path(__file__).resolve().parent / "knowledge_base"
SUPPORTED_SUFFIXES = {".txt", ".md"}


def main() -> None:
    files = (
        sorted(p for p in KB_DIR.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
        if KB_DIR.exists()
        else []
    )
    if not files:
        print(f"knowledge_base/ 目录为空，请先放入 txt/md 文档：{KB_DIR}")
        return

    documents = [
        (path.name, path.read_text(encoding="utf-8", errors="replace"))
        for path in files
    ]
    chunks = retriever.ingest_documents(retriever.GLOBAL_SCOPE, documents)
    print(f"已导入 {len(files)} 个文档，共 {chunks} 个文本块 → {config.chroma_dir}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
