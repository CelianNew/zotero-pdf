from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
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


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.item = {
            "key": "ITEM1",
            "data": {
                "key": "ITEM1",
                "itemType": "journalArticle",
                "title": "  SPOT!   Tracking  ",
                "DOI": "https://doi.org/10.1000/ABC",
                "creators": [
                    {
                        "creatorType": "author",
                        "firstName": "Alice",
                        "lastName": "Smith",
                    },
                    {
                        "creatorType": "author",
                        "firstName": "Bob",
                        "lastName": "Jones",
                    },
                ],
            },
        }

    def test_normalization_accepts_doi_url_and_title_whitespace(self):
        matches = zotero_pdf.match_items(
            [self.item],
            doi="doi:10.1000/abc",
            title="SPOT! Tracking",
            authors=["Smith, Alice"],
        )

        self.assertEqual(matches, [self.item])

    def test_author_guard_rejects_wrong_author(self):
        matches = zotero_pdf.match_items(
            [self.item],
            doi="10.1000/abc",
            authors=["Carol White"],
        )

        self.assertEqual(matches, [])

    def test_exact_duplicates_remain_visible_for_manual_disambiguation(self):
        duplicate = json.loads(json.dumps(self.item))
        duplicate["key"] = "ITEM2"
        duplicate["data"]["key"] = "ITEM2"

        matches = zotero_pdf.match_items(
            [self.item, duplicate],
            title="SPOT! Tracking",
        )

        self.assertEqual([item["key"] for item in matches], ["ITEM1", "ITEM2"])


class AuthorizationTests(unittest.TestCase):
    def test_single_use_authorization_is_renewed_for_each_write(self):
        seen_keys = []

        def fake_request(method, path, body=None, headers=None, **_kwargs):
            seen_keys.append(headers["Zotero-API-Key"])
            return 200, b"{}", {}

        writer = zotero_pdf.WriteAuthorization("server", {"Zotero-Server-ID": "server"})
        with (
            patch.object(
                zotero_pdf,
                "authorize_write",
                side_effect=[("one-shot-1", False), ("one-shot-2", False)],
            ) as authorizer,
            patch.object(zotero_pdf, "request", side_effect=fake_request),
        ):
            writer.checked("POST", "/write/1")
            writer.checked("POST", "/write/2")

        self.assertEqual(seen_keys, ["one-shot-1", "one-shot-2"])
        self.assertEqual(authorizer.call_count, 2)

    def test_always_allow_reuses_key_within_process(self):
        seen_keys = []

        def fake_request(method, path, body=None, headers=None, **_kwargs):
            seen_keys.append(headers["Zotero-API-Key"])
            return 200, b"{}", {}

        writer = zotero_pdf.WriteAuthorization("server", {"Zotero-Server-ID": "server"})
        with (
            patch.object(
                zotero_pdf,
                "authorize_write",
                return_value=("remembered-key", True),
            ) as authorizer,
            patch.object(zotero_pdf, "request", side_effect=fake_request),
        ):
            writer.checked("POST", "/write/1")
            writer.checked("POST", "/write/2")

        self.assertEqual(seen_keys, ["remembered-key", "remembered-key"])
        self.assertEqual(authorizer.call_count, 1)

    def test_401_reauthorizes_once(self):
        statuses = iter(
            [
                (401, b"", {}),
                (200, b"{}", {}),
            ]
        )

        writer = zotero_pdf.WriteAuthorization("server", {"Zotero-Server-ID": "server"})
        with (
            patch.object(
                zotero_pdf,
                "authorize_write",
                side_effect=[("expired", True), ("renewed", True)],
            ) as authorizer,
            patch.object(
                zotero_pdf,
                "request",
                side_effect=lambda *args, **kwargs: next(statuses),
            ),
        ):
            writer.checked("POST", "/write")

        self.assertEqual(authorizer.call_count, 2)

    def test_429_honors_retry_after_and_retries(self):
        statuses = iter(
            [
                (429, b"", {"Retry-After": "0"}),
                (200, b"{}", {}),
            ]
        )
        seen_keys = []

        def fake_request(method, path, body=None, headers=None, **_kwargs):
            seen_keys.append(headers["Zotero-API-Key"])
            return next(statuses)

        writer = zotero_pdf.WriteAuthorization("server", {"Zotero-Server-ID": "server"})
        with (
            patch.object(
                zotero_pdf,
                "authorize_write",
                return_value=("single-use", False),
            ) as authorizer,
            patch.object(
                zotero_pdf,
                "request",
                side_effect=fake_request,
            ),
            patch.object(zotero_pdf.time, "sleep") as sleeper,
        ):
            writer.checked("POST", "/write")

        sleeper.assert_called_once_with(0.0)
        self.assertEqual(seen_keys, ["single-use", "single-use"])
        self.assertEqual(authorizer.call_count, 1)

    def test_429_stops_after_bounded_retries(self):
        writer = zotero_pdf.WriteAuthorization("server", {"Zotero-Server-ID": "server"})
        with (
            patch.object(
                zotero_pdf,
                "authorize_write",
                return_value=("remembered", True),
            ),
            patch.object(
                zotero_pdf,
                "request",
                return_value=(429, b"busy", {"Retry-After": "0"}),
            ),
            patch.object(zotero_pdf.time, "sleep") as sleeper,
        ):
            with self.assertRaisesRegex(zotero_pdf.ZoteroError, "HTTP 429"):
                writer.checked("POST", "/write")

        self.assertEqual(sleeper.call_count, zotero_pdf.MAX_RATE_LIMIT_RETRIES)

    def test_retry_after_is_capped(self):
        self.assertEqual(
            zotero_pdf.retry_after_seconds({"Retry-After": "999"}),
            zotero_pdf.MAX_RETRY_AFTER_SECONDS,
        )


class AttachmentTests(unittest.TestCase):
    def test_digest_file_hashes_without_loading_whole_file(self):
        payload = b"%PDF-1.7\ncontent\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(payload)
            prefix, size, md5 = zotero_pdf.digest_file(path)

        self.assertEqual(prefix, b"%PDF-")
        self.assertEqual(size, len(payload))
        self.assertEqual(md5, hashlib.md5(payload).hexdigest())

    def test_attachment_facts_accept_matching_hash_and_size(self):
        payload = b"%PDF-1.7\ncontent\n"
        digest = hashlib.md5(payload).hexdigest()

        zotero_pdf.validate_attachment_facts(
            b"%PDF-",
            len(payload),
            digest,
            metadata_md5=digest,
            expected_md5=digest,
            expected_bytes=len(payload),
        )

    def test_attachment_hash_mismatch_fails(self):
        with self.assertRaisesRegex(zotero_pdf.ZoteroError, "MD5"):
            zotero_pdf.validate_attachment_facts(
                b"%PDF-",
                10,
                "actual",
                metadata_md5="expected",
            )

    def test_attachment_without_any_md5_fails(self):
        with self.assertRaisesRegex(zotero_pdf.ZoteroError, "缺少 MD5"):
            zotero_pdf.validate_attachment_facts(b"%PDF-", 10, "actual")


class AttachmentWriteFlowTests(unittest.TestCase):
    def test_attach_streams_file_and_uses_actual_size(self):
        payload = b"%PDF-1.7\ncontent\n"
        md5 = hashlib.md5(payload).hexdigest()
        calls = {}

        class FakeWriter:
            key = "remembered"
            remember = True

            def checked(
                self,
                method,
                path,
                body=None,
                headers=None,
                expected=(200,),
                timeout=60,
            ):
                calls.setdefault("writer", []).append((method, path, expected))
                if path == "/api/users/0/items":
                    calls["create_headers"] = headers
                    return (
                        json.dumps(
                            {"successful": {"0": {"key": "NEW"}}}
                        ).encode(),
                        {},
                    )
                if path == "/api/users/0/items/NEW/file" and b"md5=" in body:
                    calls["file_form"] = body
                    return (
                        json.dumps(
                            {
                                "url": "http://local/upload",
                                "uploadKey": "UPLOAD",
                            }
                        ).encode(),
                        {},
                    )
                if path == "/api/users/0/items/NEW/file" and b"upload=" in body:
                    calls["final_form"] = body
                    return b"", {}
                raise AssertionError(f"unexpected writer request: {method} {path}")

        def fake_checked(
            method,
            path,
            body=None,
            headers=None,
            expected=(200,),
            timeout=60,
        ):
            if method == "GET" and path.endswith("/children"):
                return b"[]", {}
            if method == "GET" and path == "/api/users/0/items/top?limit=1":
                return b"[]", {"Last-Modified-Version": "9"}
            if method == "POST" and path == "http://local/upload":
                calls["upload_prefix"] = body.read(5)
                calls["upload_headers"] = headers
                calls["upload_expected"] = expected
                return 201, b"", {}
            if method == "GET" and path == "/api/users/0/items/NEW":
                return (
                    json.dumps(
                        {
                            "data": {
                                "md5": md5,
                                "contentType": "application/pdf",
                            }
                        }
                    ).encode(),
                    {},
                )
            raise AssertionError(f"unexpected request: {method} {path}")

        args = SimpleNamespace(
            item_key="ITEM",
            pdf=None,
            source_url="https://example.test/paper.pdf",
            doi="10.1000/example",
            title="Verified Title",
        )

        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "paper.pdf"
            pdf_path.write_bytes(payload)
            args.pdf = str(pdf_path)
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
                        {"version": 8},
                        {},
                    ),
                ),
                patch.object(zotero_pdf, "checked", side_effect=fake_checked),
                patch.object(zotero_pdf, "WriteAuthorization", return_value=FakeWriter()),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    zotero_pdf.attach(args)

        form = zotero_pdf.urllib.parse.parse_qs(calls["file_form"].decode())
        self.assertEqual(form["filesize"], [str(len(payload))])
        self.assertEqual(form["md5"], [md5])
        self.assertEqual(calls["upload_prefix"], b"%PDF-")
        self.assertEqual(calls["upload_expected"], (201,))
        self.assertEqual(
            calls["upload_headers"]["Content-Length"],
            str(len(payload)),
        )
        self.assertEqual(
            calls["create_headers"]["If-Unmodified-Since-Version"],
            "9",
        )


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
            evidence_level="metadata",
            dry_run=False,
        )

        def fake_request(method, path, body=None, headers=None, **_kwargs):
            if method == "PATCH":
                captured["body"] = body
                captured["headers"] = headers
                state["tags"] = body["tags"]
                state["collections"] = body["collections"]
                state["version"] = 8
                return 200, b"{}", {}
            if method == "GET" and path == "/api/users/0/items/ITEM":
                return (
                    200,
                    json.dumps({"version": state["version"], "data": state}).encode(),
                    {},
                )
            raise AssertionError(f"unexpected request: {method} {path}")

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
            patch.object(
                zotero_pdf, "authorize_write", return_value=("memory-only-key", False)
            ),
            patch.object(zotero_pdf, "request", side_effect=fake_request),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                zotero_pdf.classify(args)

        result = json.loads(output.getvalue())
        self.assertTrue(result["verified"])
        self.assertEqual(result["evidenceLevel"], "metadata")
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
            evidence_level="metadata",
            dry_run=False,
        )

        def fake_request(method, _path, body=None, _headers=None, **_kwargs):
            if method == "PATCH":
                return 200, b"{}", {}
            if method == "GET":
                return (
                    200,
                    json.dumps({"version": 8, "data": state}).encode(),
                    {},
                )
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
            patch.object(
                zotero_pdf, "authorize_write", return_value=("memory-only-key", False)
            ),
            patch.object(zotero_pdf, "request", side_effect=fake_request),
        ):
            with self.assertRaisesRegex(
                zotero_pdf.ZoteroError,
                "写入后回读失败",
            ):
                zotero_pdf.classify(args)

    def test_classify_requires_a_parent_version_before_authorizing(self):
        args = SimpleNamespace(
            item_key="ITEM",
            title="Verified Title",
            doi=None,
            tag=["New"],
            collection_name=None,
            collection_key=None,
            evidence_level="metadata",
            dry_run=False,
        )

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
                    {},
                    {"tags": [], "collections": []},
                ),
            ),
            patch.object(zotero_pdf, "resolve_collection", return_value=None),
            patch.object(zotero_pdf, "authorize_write") as authorizer,
        ):
            with self.assertRaisesRegex(zotero_pdf.ZoteroError, "版本号"):
                zotero_pdf.classify(args)

        authorizer.assert_not_called()

    def test_metadata_only_classification_never_requires_a_pdf(self):
        state = {
            "key": "ITEM",
            "version": 7,
            "title": "Metadata Only",
            "tags": [],
            "collections": [],
        }
        requested_paths = []
        args = SimpleNamespace(
            item_key="ITEM",
            title="Metadata Only",
            doi=None,
            tag=["High Confidence"],
            collection_name=None,
            collection_key=None,
            evidence_level="metadata",
            dry_run=False,
        )

        def fake_request(method, path, body=None, headers=None, **_kwargs):
            requested_paths.append(path)
            if method == "PATCH":
                state["tags"] = body["tags"]
                state["version"] = 8
                return 200, b"{}", {}
            if method == "GET":
                return (
                    200,
                    json.dumps({"version": state["version"], "data": state}).encode(),
                    {},
                )
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
            patch.object(zotero_pdf, "resolve_collection", return_value=None),
            patch.object(
                zotero_pdf, "authorize_write", return_value=("memory-only-key", False)
            ),
            patch.object(zotero_pdf, "request", side_effect=fake_request),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                zotero_pdf.classify(args)

        self.assertFalse(any(path.endswith("/children") for path in requested_paths))


class AttachmentVerificationTests(unittest.TestCase):
    def test_multiple_pdfs_require_an_attachment_key(self):
        args = SimpleNamespace(
            item_key="ITEM",
            attachment_key=None,
            doi=None,
            title="Verified Title",
            expected_md5=None,
            expected_bytes=None,
        )
        children = [
            {
                "key": "PDF1",
                "data": {
                    "itemType": "attachment",
                    "contentType": "application/pdf",
                },
            },
            {
                "key": "PDF2",
                "data": {
                    "itemType": "attachment",
                    "contentType": "application/pdf",
                },
            },
        ]

        with (
            patch.object(
                zotero_pdf,
                "server_context",
                return_value=("server", {"Zotero-API-Version": "3"}),
            ),
            patch.object(
                zotero_pdf,
                "verified_parent",
                return_value=("/api/users/0/items/ITEM", {}, {}),
            ),
            patch.object(
                zotero_pdf,
                "checked",
                return_value=(json.dumps(children).encode(), {}),
            ),
            patch.object(zotero_pdf, "attachment_file") as attachment_file,
        ):
            with self.assertRaisesRegex(
                zotero_pdf.ZoteroError,
                "多个 PDF 附件",
            ):
                zotero_pdf.verify_attachment(args)

        attachment_file.assert_not_called()

    def test_verification_reads_file_and_reports_no_local_path(self):
        args = SimpleNamespace(
            item_key="ITEM",
            attachment_key="PDF1",
            doi=None,
            title="Verified Title",
            expected_md5=None,
            expected_bytes=None,
        )
        payload = b"%PDF-1.7\ncontent\n"
        md5 = hashlib.md5(payload).hexdigest()
        children = [
            {
                "key": "PDF1",
                "data": {
                    "itemType": "attachment",
                    "contentType": "application/pdf",
                    "filename": "paper.pdf",
                    "linkMode": "imported_file",
                    "md5": md5,
                },
            }
        ]

        with (
            patch.object(
                zotero_pdf,
                "server_context",
                return_value=("server", {"Zotero-API-Version": "3"}),
            ),
            patch.object(
                zotero_pdf,
                "verified_parent",
                return_value=("/api/users/0/items/ITEM", {}, {}),
            ),
            patch.object(
                zotero_pdf,
                "checked",
                return_value=(json.dumps(children).encode(), {}),
            ),
            patch.object(
                zotero_pdf,
                "attachment_file",
                return_value=(None, payload),
            ),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                zotero_pdf.verify_attachment(args)

        result = json.loads(output.getvalue())
        self.assertTrue(result["verified"])
        self.assertEqual(result["md5"], md5)
        self.assertEqual(result["fileSource"], "response_body")
        self.assertNotIn("filePath", result)


if __name__ == "__main__":
    unittest.main()
