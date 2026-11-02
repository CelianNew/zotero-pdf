#!/usr/bin/env python3
"""Attach a verified PDF and organize its Zotero item through Zotero 10 Local API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:23119"
API = {"Zotero-API-Version": "3"}


class ZoteroError(RuntimeError):
    pass


def request(method: str, path: str, body=None, headers=None):
    data = body
    req_headers = dict(headers or {})
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    url = path if path.startswith("http://") or path.startswith("https://") else BASE + path
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())
    except (urllib.error.URLError, TimeoutError) as error:
        raise ZoteroError(f"无法连接 Zotero 本地 API：{error}") from error


def checked(method: str, path: str, body=None, headers=None, expected=(200,)):
    status, raw, response_headers = request(method, path, body, headers)
    if status not in expected:
        detail = raw[:500].decode("utf-8", errors="replace")
        raise ZoteroError(f"{method} {path} 返回 HTTP {status}: {detail}")
    return raw, response_headers


def as_json(raw: bytes):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ZoteroError("Zotero API 返回了无法解析的 JSON") from error


def server_context():
    _, root_headers = checked("GET", "/api/", expected=(200,))
    try:
        zotero_version = root_headers.get("X-Zotero-Version", "0")
        version = tuple(int(part) for part in zotero_version.split(".")[:2])
    except ValueError:
        version = (0, 0)
    if version < (10, 0):
        raise ZoteroError(f"本 helper 要求 Zotero 10 或更高版本；检测到 {root_headers.get('X-Zotero-Version', '未知')}")
    server_id = root_headers.get("Zotero-Server-ID")
    if not server_id:
        raise ZoteroError("本地 API 未返回 Zotero-Server-ID")
    common = {**API, "Zotero-Server-ID": server_id}
    return server_id, common


def authorize_write(server_id: str):
    raw, _ = checked(
        "POST",
        "/api/local/authorize",
        {"appName": "Codex Zotero PDF"},
        {"Content-Type": "application/json", "Zotero-Server-ID": server_id},
    )
    result = as_json(raw)
    if result.get("denied") or not result.get("key"):
        raise ZoteroError("Zotero 本地写入授权被拒绝")
    return result["key"]


def verified_parent(item_key: str, common, *, doi: str | None, title: str | None):
    if not doi and not title:
        raise ZoteroError("必须提供 --doi 或 --title 来核对目标条目")
    parent_path = f"/api/users/0/items/{urllib.parse.quote(item_key)}"
    parent_raw, _ = checked("GET", parent_path, headers=common)
    parent = as_json(parent_raw)
    item = parent.get("data", {})
    if doi and item.get("DOI", "").strip().lower() != doi.strip().lower():
        raise ZoteroError(f"DOI 不匹配：目标条目为 {item.get('DOI') or '(空)'}")
    if title and item.get("title", "").strip().casefold() != title.strip().casefold():
        raise ZoteroError(f"标题不匹配：目标条目为 {item.get('title') or '(空)'}")
    if item.get("itemType") == "attachment":
        raise ZoteroError("指定 key 是附件条目，不是论文父条目")
    return parent_path, parent, item


def attach(args):
    pdf_path = Path(args.pdf).expanduser().resolve(strict=True)
    if not pdf_path.is_file():
        raise ZoteroError("--pdf 必须指向一个文件")
    pdf = pdf_path.read_bytes()
    if len(pdf) < 8 or not pdf.startswith(b"%PDF-"):
        raise ZoteroError("文件头不是 PDF（缺少 %PDF-）")

    server_id, common = server_context()
    parent_path, parent, item = verified_parent(
        args.item_key, common, doi=args.doi, title=args.title
    )

    children_raw, _ = checked("GET", parent_path + "/children", headers=common)
    children = as_json(children_raw)
    for child in children:
        child_data = child.get("data", {})
        if child_data.get("itemType") == "attachment" and child_data.get("contentType") == "application/pdf":
            raise ZoteroError(f"目标条目已有 PDF 附件：{child_data.get('key')}")

    version_raw, version_headers = checked(
        "GET", "/api/users/0/items/top?limit=1", headers=common
    )
    library_version = version_headers.get("Last-Modified-Version")
    if not library_version:
        raise ZoteroError("无法读取文库版本号，停止写入")

    api_key = authorize_write(server_id)
    write_headers = {**common, "Zotero-API-Key": api_key}

    filename = pdf_path.name
    md5 = hashlib.md5(pdf).hexdigest()
    attachment = {
        "itemType": "attachment",
        "parentItem": args.item_key,
        "linkMode": "imported_file",
        "title": "Full Text PDF",
        "accessDate": "",
        "url": args.source_url,
        "note": "",
        "tags": [],
        "relations": {},
        "contentType": "application/pdf",
        "charset": "",
        "filename": filename,
        "md5": None,
        "mtime": None,
    }
    created_key = None
    try:
        raw, _ = checked(
            "POST",
            "/api/users/0/items",
            [attachment],
            {**write_headers, "If-Unmodified-Since-Version": library_version},
        )
        created = as_json(raw).get("successful", {}).get("0", {})
        created_key = created.get("key")
        if not created_key:
            raise ZoteroError("Zotero 未返回新附件 key")

        file_form = urllib.parse.urlencode(
            {
                "md5": md5,
                "filename": filename,
                "filesize": len(pdf),
                "mtime": str(int(os.path.getmtime(pdf_path) * 1000)),
            }
        ).encode()
        path = f"/api/users/0/items/{created_key}/file"
        raw, _ = checked(
            "POST",
            path,
            file_form,
            {
                **write_headers,
                "If-None-Match": "*",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        upload = as_json(raw)
        if upload.get("exists") != 1:
            upload_url = upload.get("url")
            if not upload_url:
                raise ZoteroError("Zotero 未返回文件上传地址")
            if upload_url.startswith("/"):
                upload_url = BASE + upload_url
            checked(
                "POST",
                upload_url,
                pdf,
                {"Content-Type": "application/pdf", "Content-Length": str(len(pdf))},
                expected=(201,),
            )
            final = urllib.parse.urlencode({"upload": upload["uploadKey"]}).encode()
            checked(
                "POST",
                path,
                final,
                {
                    **write_headers,
                    "If-None-Match": "*",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                expected=(204,),
            )

        saved_raw, _ = checked("GET", f"/api/users/0/items/{created_key}", headers=common)
        saved = as_json(saved_raw).get("data", {})
        if saved.get("md5") != md5 or saved.get("contentType") != "application/pdf":
            raise ZoteroError("附件已创建，但 Zotero 中的 MD5 或 MIME 类型核验失败")
        print(
            json.dumps(
                {
                    "result": "success",
                    "parentKey": args.item_key,
                    "attachmentKey": created_key,
                    "pdfBytes": len(pdf),
                    "md5Verified": True,
                    "source": args.source_url,
                },
                ensure_ascii=True,
            )
        )
    except Exception:
        if created_key:
            try:
                current_raw, _ = checked(
                    "GET", f"/api/users/0/items/{created_key}", headers=common
                )
                current = as_json(current_raw)
                current_version = current.get("version")
                if current_version is not None:
                    checked(
                        "DELETE",
                        f"/api/users/0/items/{created_key}",
                        headers={
                            **write_headers,
                            "If-Unmodified-Since-Version": str(current_version),
                        },
                        expected=(204,),
                    )
            except Exception:
                pass
        raise


def tag_name(tag) -> str:
    if isinstance(tag, str):
        return tag.strip()
    if isinstance(tag, dict):
        return str(tag.get("tag") or "").strip()
    return ""


def fetch_all(path: str, common, page_size: int = 100):
    start = 0
    results = []
    while True:
        separator = "&" if "?" in path else "?"
        raw, headers = checked(
            "GET",
            f"{path}{separator}limit={page_size}&start={start}",
            headers=common,
        )
        page = as_json(raw)
        if not isinstance(page, list):
            raise ZoteroError(f"{path} 未返回列表")
        results.extend(page)
        total = int(headers.get("Total-Results", len(results)))
        if not page or len(results) >= total:
            return results
        start += len(page)


def merge_tags(existing, requested: list[str]):
    merged = []
    seen = set()
    for tag in existing or []:
        name = tag_name(tag)
        if not name or name.casefold() in seen:
            continue
        merged.append(dict(tag) if isinstance(tag, dict) else {"tag": name})
        seen.add(name.casefold())

    added = []
    for raw_name in requested:
        name = raw_name.strip()
        if not name or name.casefold() in seen:
            continue
        merged.append({"tag": name})
        seen.add(name.casefold())
        added.append(name)
    return merged, added


def resolve_collection(args, common):
    if not args.collection_key and not args.collection_name:
        return None

    by_key = None
    if args.collection_key:
        raw, _ = checked(
            "GET",
            f"/api/users/0/collections/{urllib.parse.quote(args.collection_key)}",
            headers=common,
        )
        by_key = as_json(raw)

    by_name = None
    if args.collection_name:
        matches = [
            collection
            for collection in fetch_all("/api/users/0/collections", common)
            if collection.get("data", {}).get("name", "").strip().casefold()
            == args.collection_name.strip().casefold()
        ]
        if not matches:
            raise ZoteroError(f"未找到名为“{args.collection_name}”的 collection")
        if len(matches) > 1:
            keys = ", ".join(collection.get("key", "") for collection in matches)
            raise ZoteroError(f"存在多个同名 collection，请改用 --collection-key：{keys}")
        by_name = matches[0]

    if by_key and by_name and by_key.get("key") != by_name.get("key"):
        raise ZoteroError("--collection-key 与 --collection-name 指向不同 collection")
    collection = by_key or by_name
    if not collection or not collection.get("key"):
        raise ZoteroError("无法解析 collection")
    return collection


def inspect_item(args):
    _, common = server_context()
    _, _, item = verified_parent(
        args.item_key, common, doi=args.doi, title=args.title
    )
    all_collections = fetch_all("/api/users/0/collections", common)
    collections = {
        collection.get("key"): collection.get("data", {}).get("name")
        for collection in all_collections
    }
    parents = {
        collection.get("key"): collection.get("data", {}).get("parentCollection")
        for collection in all_collections
    }

    def collection_path(key: str):
        names = []
        seen = set()
        current = key
        while current and current not in seen:
            seen.add(current)
            name = collections.get(current)
            if name:
                names.append(name)
            current = parents.get(current)
        return " / ".join(reversed(names))

    collection_keys = list(item.get("collections") or [])
    library_tags = sorted(
        {
            tag_name(tag)
            for tag in fetch_all("/api/users/0/tags", common)
            if tag_name(tag)
        },
        key=str.casefold,
    )
    print(
        json.dumps(
            {
                "itemKey": args.item_key,
                "title": item.get("title"),
                "doi": item.get("DOI"),
                "tags": [
                    tag_name(tag) for tag in (item.get("tags") or []) if tag_name(tag)
                ],
                "collections": [
                    {"key": key, "name": collections.get(key)} for key in collection_keys
                ],
                "libraryTags": library_tags,
                "libraryCollections": [
                    {
                        "key": collection.get("key"),
                        "name": collection.get("data", {}).get("name"),
                        "path": collection_path(collection.get("key")),
                        "parentKey": collection.get("data", {}).get("parentCollection"),
                    }
                    for collection in all_collections
                ],
            },
            ensure_ascii=True,
        )
    )


def classify(args):
    requested_tags = [tag for tag in (args.tag or []) if tag and tag.strip()]
    if not requested_tags and not args.collection_key and not args.collection_name:
        raise ZoteroError("至少提供 --tag、--collection-name 或 --collection-key")

    server_id, common = server_context()
    parent_path, parent, item = verified_parent(
        args.item_key, common, doi=args.doi, title=args.title
    )
    collection = resolve_collection(args, common)

    merged_tags, added_tags = merge_tags(item.get("tags") or [], requested_tags)
    existing_collections = list(item.get("collections") or [])
    collection_key = collection.get("key") if collection else None
    collection_name = collection.get("data", {}).get("name") if collection else None
    added_collections = []
    if collection_key and collection_key not in existing_collections:
        existing_collections.append(collection_key)
        added_collections.append(collection_key)

    changes = False
    body = {}
    if added_tags:
        body["tags"] = merged_tags
        changes = True
    if added_collections:
        body["collections"] = existing_collections
        changes = True

    result = {
        "result": "dry-run" if args.dry_run else "success",
        "parentKey": args.item_key,
        "title": item.get("title"),
        "collection": {"key": collection_key, "name": collection_name},
        "addedTags": added_tags,
        "addedCollections": added_collections,
        "changed": changes,
    }
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=True))
        return
    if not changes:
        result["verified"] = True
        print(json.dumps(result, ensure_ascii=True))
        return

    api_key = authorize_write(server_id)
    checked(
        "PATCH",
        parent_path,
        body,
        {
            **common,
            "Zotero-API-Key": api_key,
            "If-Unmodified-Since-Version": str(parent.get("version", "")),
        },
        expected=(200, 204),
    )

    saved_raw, _ = checked("GET", parent_path, headers=common)
    saved = as_json(saved_raw).get("data", {})
    saved_tags = {tag_name(tag).casefold() for tag in saved.get("tags") or []}
    missing_tags = [tag for tag in added_tags if tag.casefold() not in saved_tags]
    missing_collections = [
        key for key in added_collections if key not in (saved.get("collections") or [])
    ]
    if missing_tags or missing_collections:
        raise ZoteroError(
            "写入后回读失败："
            f"missingTags={missing_tags}, missingCollections={missing_collections}"
        )
    result["verified"] = True
    print(json.dumps(result, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    attach_parser = subparsers.add_parser("attach", help="attach a verified local PDF to an existing Zotero item")
    attach_parser.add_argument("--item-key", required=True)
    attach_parser.add_argument("--pdf", required=True, help="local PDF path")
    attach_parser.add_argument("--source-url", required=True, help="public or authorized PDF source URL")
    attach_parser.add_argument("--doi", help="expected DOI; checked against the Zotero item")
    attach_parser.add_argument("--title", help="expected exact title; checked against the Zotero item")

    inspect_parser = subparsers.add_parser(
        "inspect", help="show an item's current tags and collection membership"
    )
    inspect_parser.add_argument("--item-key", required=True)
    inspect_parser.add_argument("--doi", help="expected DOI; checked against the Zotero item")
    inspect_parser.add_argument("--title", help="expected exact title; checked against the Zotero item")

    classify_parser = subparsers.add_parser(
        "classify", help="add evidence-based tags and collection membership to an existing item"
    )
    classify_parser.add_argument("--item-key", required=True)
    classify_parser.add_argument("--doi", help="expected DOI; checked against the Zotero item")
    classify_parser.add_argument("--title", help="expected exact title; checked against the Zotero item")
    classify_parser.add_argument("--tag", action="append", default=[], help="tag to add; repeatable")
    collection = classify_parser.add_mutually_exclusive_group()
    collection.add_argument("--collection-name", help="existing collection name")
    collection.add_argument("--collection-key", help="existing collection key")
    classify_parser.add_argument(
        "--dry-run", action="store_true", help="show pending changes without writing"
    )
    args = parser.parse_args()
    try:
        if args.command == "attach":
            attach(args)
        elif args.command == "inspect":
            inspect_item(args)
        elif args.command == "classify":
            classify(args)
    except (OSError, ZoteroError) as error:
        print(json.dumps({"result": "failed", "detail": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
