"""Knowledge boundaries tested without COM, Chroma or repository-local secrets."""
from pathlib import Path
import importlib.util
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "knowledge.py"
spec = importlib.util.spec_from_file_location("knowledge_under_test", SOURCE)
knowledge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knowledge)


class KnowledgeTests(unittest.TestCase):
    def test_absent_rag_has_explicit_error_and_no_files_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = knowledge.ROOT
            try:
                knowledge.ROOT = Path(tmp)
                knowledge.warmup()
                with self.assertRaisesRegex(RuntimeError, "optional rag/"):
                    knowledge.search("mate")
                self.assertEqual(list(Path(tmp).iterdir()), [])
            finally:
                knowledge.ROOT = old

    def test_current_signature_cache_wins_over_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "rag_index" / "api_signatures.json"
            current = root / "mcp_server" / "cache" / "api_signatures.json"
            self.assertEqual(knowledge.signature_index_path(root), current)
            legacy.parent.mkdir()
            legacy.write_text("{}")
            self.assertEqual(knowledge.signature_index_path(root), legacy)
            current.parent.mkdir(parents=True)
            current.write_text("{}")
            self.assertEqual(knowledge.signature_index_path(root), current)


if __name__ == "__main__":
    unittest.main()
