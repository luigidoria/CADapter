"""Optional semantic recipe search. COM and MCP protocol handling live elsewhere."""
import json
import os
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config_env
config_env.load_module("rag")

INDEX_DIR = Path(os.environ.get("CADAPTER_RAG_INDEX", Path(__file__).parent / "index"))

# ── RAG (lazy init) ───────────────────────────────────────────────────────────
# chromadb/onnxruntime write to stdout during import; deferring the init avoids
# corrupting the MCP stdio JSON-RPC handshake.

# KNOWN partition names. Not every build has them all: the partitions derived from
# the SolidWorks documentation (api/const/guide/examples) are out of the product: they
# cannot be redistributed. Availability is resolved at runtime against the index,
# so restoring a backup exposes them again without touching the code.
_PARTITION_MAP = {
    "api":      "sw_api",
    "const":    "sw_const",
    "guide":    "sw_guide",
    "examples": "sw_examples",
    "recipe":   "sw_recipe",     # curated USAGE recipes / gotchas (how to use it right)
}
_rag_client = None
_rag_cache: dict = {}


def _get_rag_client():
    global _rag_client
    if _rag_client is None:
        if not (INDEX_DIR / "chroma.sqlite3").is_file():
            raise RuntimeError(
                "Recipe index is missing. Run python rag/build_rag_recipe.py first.")
        try:
            import chromadb as _chromadb
        except ModuleNotFoundError as exc:
            if exc.name != "chromadb":
                raise
            raise RuntimeError(
                "Recipe search requires Chroma: install rag/requirements.txt.") from exc
        _rag_client = _chromadb.PersistentClient(path=str(INDEX_DIR))
    return _rag_client


def _available_partitions() -> list:
    """Partitions actually present in the index, in _PARTITION_MAP order."""
    present = {c.name for c in _get_rag_client().list_collections()}
    return [key for key, coll in _PARTITION_MAP.items() if coll in present]


def _get_partition(name: str):
    if name not in _rag_cache:
        try:
            _rag_cache[name] = _get_rag_client().get_collection(_PARTITION_MAP[name])
        except Exception as exc:
            raise ValueError(
                f"partition '{name}' does not exist in this index. "
                f"Available: {_available_partitions() + ['all']}"
            ) from exc
    return _rag_cache[name]


def _rag_warmup():
    def _warmup():
        try:
            avail = _available_partitions()
            if not avail:
                return
            # warm up the preferred one (recipe) or the first that exists
            first = "recipe" if "recipe" in avail else avail[0]
            _get_partition(first).query(query_texts=["SldWorks"], n_results=1)
        except Exception as exc:
            print(f"[rag warmup] {exc}", file=sys.stderr)

    threading.Thread(target=_warmup, daemon=True).start()


def _format_result(partition: str, meta: dict, doc_text: str) -> dict:
    if partition == "api":
        return {
            "interface":   meta.get("interface", ""),
            "method":      meta.get("method", ""),
            "signature":   meta.get("signature", ""),
            "description": meta.get("description", ""),
        }
    if partition == "recipe":
        # curated recipe: return almost the whole text (it is actionable, not a doc snippet)
        return {
            "title":  meta.get("title", ""),
            "recipe": doc_text[:1600] if doc_text else "",
            "file":   meta.get("file", ""),
        }
    return {
        "title":   meta.get("title", ""),
        "content": doc_text[:400] if doc_text else "",
        "file":    meta.get("file", ""),
    }


# ── Generic tools ─────────────────────────────────────────────────────────────

def search(query: str, partition: str = "recipe", top_k: int = 3) -> str:
    """
    Semantic search over the local index. partition: "recipe"=curated USAGE recipes /
    gotchas (DEFAULT -- HOW to use it right, avoids sw_call mistakes), "all"=every
    partition present. Builds with the SolidWorks documentation indexed also accept
    "api"=COM methods, "const"=enums, "guide"=workflows, "examples"=PDM code; if the
    partition is not in the index, the error lists the available ones. For an EXACT
    signature use sw_api_signature. Query in ENGLISH (English-only embedder).
    """
    if partition == "all":
        results = []
        for part_key in _available_partitions():
            res = _get_partition(part_key).query(query_texts=[query], n_results=top_k)
            for i in range(len(res["ids"][0])):
                entry = {"partition": part_key}
                entry.update(_format_result(
                    part_key,
                    res["metadatas"][0][i],
                    res["documents"][0][i] if res.get("documents") else "",
                ))
                results.append(entry)
        return json.dumps(results, indent=2, ensure_ascii=False)

    if partition not in _PARTITION_MAP:
        raise ValueError(
            f"partition '{partition}' is invalid. Use: "
            f"{list(_PARTITION_MAP.keys()) + ['all']}"
        )

    res = _get_partition(partition).query(query_texts=[query], n_results=top_k)
    results = [
        _format_result(
            partition,
            res["metadatas"][0][i],
            res["documents"][0][i] if res.get("documents") else "",
        )
        for i in range(len(res["ids"][0]))
    ]
    return json.dumps(results, indent=2, ensure_ascii=False)
