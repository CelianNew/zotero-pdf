from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "zotero_pdf.py"
SPEC = importlib.util.spec_from_file_location("zotero_pdf", SCRIPT)
assert SPEC and SPEC.loader
zotero_pdf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(zotero_pdf)


class ClassificationTests(unittest.TestCase):
    def test_merge_tags_preserves_existing_values_and_deduplicates(self):
        merged, added = zotero_pdf.merge_tags(
            [{"tag": "Large Language Model", "type": 0}, {"tag": "Trajectory"}],
            ["large language model", "Decision Model", "Trajectory"],
        )

        self.assertEqual(
            merged,
            [
                {"tag": "Large Language Model", "type": 0},
                {"tag": "Trajectory"},
                {"tag": "Decision Model"},
            ],
        )
        self.assertEqual(added, ["Decision Model"])

    def test_fetch_all_reads_every_page(self):
        requested = []

        def fake_checked(method, path, **_kwargs):
            requested.append(path)
            start = int(path.rsplit("start=", 1)[1])
            pages = {
                0: ([{"tag": "A"}, {"tag": "B"}], {"Total-Results": "3"}),
                2: ([{"tag": "C"}], {"Total-Results": "3"}),
            }
            payload, headers = pages[start]
            return json.dumps(payload).encode(), headers

        with patch.object(zotero_pdf, "checked", side_effect=fake_checked):
            result = zotero_pdf.fetch_all("/api/users/0/tags", {}, page_size=2)

        self.assertEqual([entry["tag"] for entry in result], ["A", "B", "C"])
        self.assertEqual(
            requested,
            [
                "/api/users/0/tags?limit=2&start=0",
                "/api/users/0/tags?limit=2&start=2",
            ],
        )

    def test_classify_uses_version_guard_and_verifies_readback(self):
        state = {
            "key": "ITEM",
            "version": 7,
            "title": "Verified Title",
            "tags": [{"tag": "Existing"}],
            "collections": ["C1"],
        }
        captured = {}
        args = SimpleNamespace(
            item_key="ITEM",
            title="Verified Title",
            doi=None,
            tag=["Existing", "New"],
            collection_name="Target",
            collection_key=None,
            dry_run=False,
        )

        def fake_checked(method, _path, body=None, headers=None, **_kwargs):
            if method == "PATCH":
                captured["body"] = body
                captured["headers"] = headers
                state["tags"] = body["tags"]
                state["collections"] = body["collections"]
                return b"{}", {}
            if method == "GET":
                return json.dumps({"version": 8, "data": state}).encode(), {}
            raise AssertionError(f"unexpected request: {method}")

        with (
            patch.object(
                zotero_pdf,
                "server_context",
                return_value=("server", {"Zotero-API-Version": "3"}),
            ),
            patch.object(
                zotero_pdf,
                "verified_parent",
                return_value=(
                    "/api/users/0/items/ITEM",
                    {"version": 7},
                    state,
                ),
            ),
            patch.object(
                zotero_pdf,
                "resolve_collection",
                return_value={"key": "C2", "data": {"name": "Target"}},
            ),
            patch.object(zotero_pdf, "authorize_write", return_value="memory-only-key"),
            patch.object(zotero_pdf, "checked", side_effect=fake_checked),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                zotero_pdf.classify(args)

        result = json.loads(output.getvalue())
        self.assertTrue(result["verified"])
        self.assertEqual(result["addedTags"], ["New"])
        self.assertEqual(result["addedCollections"], ["C2"])
        self.assertEqual(
            captured["headers"]["If-Unmodified-Since-Version"],
            "7",
        )
        self.assertEqual(
            captured["headers"]["Zotero-API-Key"],
            "memory-only-key",
        )
        self.assertEqual(
            captured["body"],
            {
                "tags": [{"tag": "Existing"}, {"tag": "New"}],
                "collections": ["C1", "C2"],
            },
        )

    def test_classify_reports_missing_readback(self):
        state = {"tags": [{"tag": "Existing"}], "collections": []}
        args = SimpleNamespace(
            item_key="ITEM",
            title="Verified Title",
            doi=None,
            tag=["New"],
            collection_name=None,
            collection_key=None,
            dry_run=False,
        )

        def fake_checked(method, _path, body=None, _headers=None, **_kwargs):
            if method == "PATCH":
                return b"{}", {}
            if method == "GET":
                return json.dumps({"data": state}).encode(), {}
            raise AssertionError(f"unexpected request: {method}")

        with (
            patch.object(
                zotero_pdf,
                "server_context",
                return_value=("server", {"Zotero-API-Version": "3"}),
            ),
            patch.object(
                zotero_pdf,
                "verified_parent",
                return_value=(
                    "/api/users/0/items/ITEM",
                    {"version": 7},
                    {"tags": [], "collections": []},
                ),
            ),
            patch.object(zotero_pdf, "resolve_collection", return_value=None),
            patch.object(zotero_pdf, "authorize_write", return_value="memory-only-key"),
            patch.object(zotero_pdf, "checked", side_effect=fake_checked),
        ):
            with self.assertRaisesRegex(
                zotero_pdf.ZoteroError,
                "写入后回读失败",
            ):
                zotero_pdf.classify(args)


if __name__ == "__main__":
    unittest.main()
