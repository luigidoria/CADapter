"""
build_rag_recipe.py
Builds the **sw_recipe** RAG partition -- curated USE recipes / gotchas (how to use each
function correctly), out of the markdown files in `rag/recipe/*.md`.

Unlike the other partitions (HTML scraped from the SolidWorks doc), this one is written
BY HAND: each .md is one actionable recipe. Title/keywords in English (Chroma's default
embedder is English-only); the body is English as well.

The format of each recipe:
    # <title in English, with method names>
    keywords: <keywords in English>
    <actionable body>

Run it (CADapter venv): `python rag/build_rag_recipe.py`. The upsert is idempotent
(id = md5 of the file name), so re-running reindexes without duplicating.
"""

import hashlib
import os
import sys
import unicodedata
from pathlib import Path
import chromadb

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.resolve().parent))
import config_env
config_env.load_module("rag")
INDEX_DIR = Path(os.environ.get("CADAPTER_RAG_INDEX", BASE / "index"))
RECIPE_DIR = BASE / "recipe"

# recipes are normalized to ASCII (it avoids mojibake over MCP/console transport).
_ASCII_REPL = {
    "—": "--", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", " ": " ",
    "º": "o", "ª": "a",
    "→": "->", "←": "<-", "↔": "<->", "⟶": "->",
}


def _to_ascii(s: str) -> str:
    for k, v in _ASCII_REPL.items():
        s = s.replace(k, v)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _parse_recipe(path: Path) -> tuple[str, str]:
    """Return (title, full_text) in ASCII. The full text (title+keywords+body) is what
    goes into the embedding AND what the tool returns."""
    text = _to_ascii(path.read_text(encoding="utf-8"))
    title = path.stem
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return title, text.strip()


def main() -> None:
    INDEX_DIR.mkdir(exist_ok=True)
    client = chromadb.PersistentClient(path=str(INDEX_DIR))
    col = client.get_or_create_collection("sw_recipe")

    if not RECIPE_DIR.exists():
        print(f"[ERROR] folder not found: {RECIPE_DIR}")
        return

    files = sorted(RECIPE_DIR.glob("*.md"))
    if not files:
        print(f"[SKIP] no .md recipe in {RECIPE_DIR}")
        return

    documents, ids, metadatas = [], [], []
    for path in files:
        title, body = _parse_recipe(path)
        documents.append(body)
        ids.append(hashlib.md5(f"recipe:{path.name}".encode()).hexdigest()[:16])
        metadatas.append({"title": title[:200], "source": "recipe", "file": path.name})
        print(f"  + {path.name}  ({title})")

    col.upsert(documents=documents, ids=ids, metadatas=metadatas)
    print(f"\nsw_recipe: {col.count()} recipes indexed in {INDEX_DIR}")


if __name__ == "__main__":
    main()
