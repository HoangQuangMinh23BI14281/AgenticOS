"""LCMv3 embedding layer — optional vector embedding and hybrid RRF search."""

from __future__ import annotations
import os
import sys
from typing import Optional

_fastembed_cache: dict[str, "TextEmbedding"] = {}


def detect_provider(cfg: dict) -> Optional[str]:
    if not cfg.get("embeddingEnabled", False):
        return None
    provider = cfg.get("embeddingProvider", "local")
    if provider == "local":
        try:
            import fastembed; return "fastembed"  # noqa: E702
        except ImportError:
            pass
        try:
            import numpy; return "numpy"  # noqa: E702
        except ImportError:
            return None
    if provider == "openai":
        try:
            import openai  # noqa: F401
            if os.environ.get("OPENAI_API_KEY"):
                return "openai"
        except ImportError:
            pass
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
            if os.environ.get("ANTHROPIC_API_KEY"):
                return "anthropic"
        except ImportError:
            pass
    return None


def embed_texts(texts: list[str], cfg: dict) -> list[Optional[list[float]]]:
    provider = detect_provider(cfg)
    model = cfg.get("embeddingModel", "BAAI/bge-small-en-v1.5")
    try:
        if provider == "fastembed":
            from fastembed import TextEmbedding
            if model not in _fastembed_cache:
                _fastembed_cache[model] = TextEmbedding(model_name=model)
            return [[float(v) for v in vec] for vec in _fastembed_cache[model].embed(texts)]
        if provider == "openai":
            from openai import OpenAI
            base_url = cfg.get("openaiBaseUrl") or os.environ.get("OPENAI_BASE_URL")
            api_key = os.environ.get("OPENAI_API_KEY") or ("not-needed" if base_url else None)
            client = OpenAI(base_url=base_url, api_key=api_key) if api_key else OpenAI()
            results = []
            for i in range(0, len(texts), 32):
                resp = client.embeddings.create(model=model, input=texts[i:i + 32])
                results.extend([[float(v) for v in item.embedding] for item in resp.data])
            return results
    except Exception:
        pass
    return [None] * len(texts)


def vec_to_blob(vec: list[float]) -> bytes:
    try:
        import numpy as np
        arr = np.array(vec, dtype=np.float32)
        n = np.linalg.norm(arr)
        return (arr / n if n > 0 else arr).tobytes()
    except ImportError:
        import struct
        mag = sum(v * v for v in vec) ** 0.5
        if mag > 0:
            vec = [v / mag for v in vec]
        return struct.pack(f"{len(vec)}f", *vec)


def blob_to_vec(raw: bytes) -> list[float]:
    try:
        import numpy as np
        return np.frombuffer(raw, dtype=np.float32).tolist()
    except ImportError:
        import struct
        n = len(raw) // 4
        return list(struct.unpack(f"{n}f", raw))


def embed_messages_batch(db_conn, cfg: dict, session_id: Optional[str] = None) -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db as _db
    provider = detect_provider(cfg)
    if provider is None or provider == "numpy":
        return 0
    model = cfg.get("embeddingModel", "BAAI/bge-small-en-v1.5")
    rows = _db.get_unembed_messages(model, session_id)
    if not rows:
        return 0
    vecs = embed_texts([r["content"] for r in rows], cfg)
    stored = 0
    for row, vec in zip(rows, vecs):
        if vec is None:
            continue
        try:
            _db.upsert_embedding(_db.get_db(), row["id"], model, vec_to_blob(vec))
            stored += 1
        except Exception:
            pass
    if stored > 0:
        cur = _db.load_config()
        cur["lastEmbeddingModel"] = model
        _db.save_config(cur)
    return stored


def hybrid_search(query: str, cfg: dict, limit: int = 20) -> dict:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db as _db
    fts_msgs = _db.search_messages(query, limit=limit)
    fts_sums = _db.search_summaries(query, limit=limit)
    if not cfg.get("embeddingEnabled", False):
        return {"messages": fts_msgs, "summaries": fts_sums}
    model = cfg.get("embeddingModel", "BAAI/bge-small-en-v1.5")
    last = cfg.get("lastEmbeddingModel")
    if last and last != model:
        return {"messages": fts_msgs, "summaries": fts_sums}
    provider = detect_provider(cfg)
    if provider is None or provider == "numpy":
        return {"messages": fts_msgs, "summaries": fts_sums}
    qv = embed_texts([query], cfg)
    if not qv or qv[0] is None:
        return {"messages": fts_msgs, "summaries": fts_sums}
    try:
        vec_results = _vector_search_numpy(qv[0], model, limit)
    except Exception:
        return {"messages": fts_msgs, "summaries": fts_sums}
    if not vec_results:
        return {"messages": fts_msgs, "summaries": fts_sums}
    w_fts, w_vec, k = float(cfg.get("ftsWeight", 1.0)), float(cfg.get("vectorWeight", 1.0)), 60
    scores: dict[int, float] = {}
    for rank, msg in enumerate(fts_msgs, 1):
        scores[int(msg["id"])] = scores.get(int(msg["id"]), 0.0) + w_fts / (k + rank)
    for rank, (mid, _) in enumerate(vec_results, 1):
        scores[mid] = scores.get(mid, 0.0) + w_vec / (k + rank)
    top_ids = sorted(scores, key=lambda x: scores[x], reverse=True)[:limit]
    fts_id_set = {m["id"] for m in fts_msgs}
    extra = _db.get_messages_by_ids([mid for mid in top_ids if mid not in fts_id_set]) if top_ids else []
    msg_map = {m["id"]: m for m in fts_msgs + extra}
    return {"messages": [msg_map[mid] for mid in top_ids if mid in msg_map], "summaries": fts_sums, "hybrid": True}


def _vector_search_numpy(query_vec, model_name, limit):
    try:
        import numpy as np
    except ImportError:
        return []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import db as _db
    rows = _db.get_all_embeddings(model_name)
    if not rows:
        return []
    dims = len(query_vec)
    q = np.array(query_vec, dtype=np.float32)
    n = np.linalg.norm(q)
    if n == 0:
        return []
    q = q / n
    scored = [(r["message_id"], float(np.dot(q, np.frombuffer(r["vector"], dtype=np.float32))))
              for r in rows if len(r["vector"]) == dims * 4]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
