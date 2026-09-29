"""Optional recipe integration and the signature cache location.

This module has no COM or embedding dependency. The MCP base can start without the
RAG directory, index or Chroma installation; only a search request needs them.
"""
from pathlib import Path
import importlib
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def signature_index_path(root: Path = ROOT) -> Path:
    """Prefer the MCP cache, retaining old installations until they regenerate it."""
    current = root / "mcp_server" / "cache" / "api_signatures.json"
    legacy = root / "rag_index" / "api_signatures.json"
    return current if current.is_file() or not legacy.is_file() else legacy


def _recipes():
    if not (ROOT / "rag" / "search.py").is_file():
        raise RuntimeError(
            "Recipe search requires the optional rag/ module. Restore it and install "
            "rag/requirements.txt, then build the recipe index. CAD tools remain available.")
    return importlib.import_module("rag.search")


def search(query: str, partition: str = "recipe", top_k: int = 3) -> str:
    """Delegate only when requested; preserve the public MCP search response."""
    return _recipes().search(query, partition, top_k)


def warmup() -> None:
    """Warm installed recipe search without making it a base startup requirement."""
    if ((ROOT / "rag" / "search.py").is_file()
            and importlib.util.find_spec("chromadb") is not None):
        _recipes()._rag_warmup()
