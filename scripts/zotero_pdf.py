#!/usr/bin/env python3
"""Attach a verified PDF and organize its Zotero item through Zotero 10 Local API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE = "http://127.0.0.1:23119"
API = {"Zotero-API-Version": "3"}
MAX_RATE_LIMIT_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 15.0


class ZoteroError(RuntimeError):
    pass


def request(method: str, path: str, body=None, headers=None, timeout=60):
    data = body
    req_headers = dict(headers or {})
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    url = path if path.startswith("http://") or path.startswith("https://") else BASE + path
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
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


def header_value(headers, name: str, default=None):
    target = name.casefold()
    for key, value in (headers or {}).items():
        if key.casefold() == target:
            return value
    return default


def normalize_title(value) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(value.split()).casefold()


def normalize_doi(value) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return value.casefold()


def creator_name(creator: dict) -> str:
    if creator.get("name"):
        return str(creator["name"]).strip()
    return " ".join(
        part.strip()
        for part in (creator.get("firstName"), creator.get("lastName"))
        if part and part.strip()
    )


def creator_variants(creator: dict) -> set[str]:
    first = str(creator.get("firstName") or "").strip()
    last = str(creator.get("lastName") or "").strip()
    variants = {creator_name(creator)}
    if first and last:
        variants.update({f"{last} {first}", f"{last}, {first}", f"{first} {last}"})
    if last:
        variants.add(last)
    return {normalize_title(value) for value in variants if value.strip()}


def author_requirements_match(item: dict, authors: list[str] | None) -> bool:
    requested = [value.strip() for value in (authors or []) if value and value.strip()]
    if not requested:
        return True
    actual = {
        variant
        for creator in item.get("creators") or []
        if creator.get("creatorType", "author") == "author"
        for variant in creator_variants(creator)
    }
    for author in requested:
        normalized = normalize_title(author)
        variants = {normalized}
        if "," in author:
            last, first = (part.strip() for part in author.split(",", 1))
            if last and first:
                variants.update(
                    {normalize_title(f"{last} {first}"), normalize_title(f"{first} {last}")}
                )
        if not variants.intersection(actual):
            return False
    return True


def item_evidence(item: dict):
    return {
        "itemType": item.get("itemType"),
        "title": item.get("title"),
        "doi": item.get("DOI"),
        "url": item.get("url"),
        "date": item.get("date"),
        "publicationTitle": item.get("publicationTitle"),
        "proceedingsTitle": item.get("proceedingsTitle"),
        "conferenceName": item.get("conferenceName"),
        "publisher": item.get("publisher"),
        "language": item.get("language"),
        "abstractNote": item.get("abstractNote"),
        "extra": item.get("extra"),
        "creators": [
            {
                "creatorType": creator.get("creatorType"),
                "name": creator_name(creator),
                "firstName": creator.get("firstName"),
                "lastName": creator.get("lastName"),
            }
            for creator in (item.get("creators") or [])
        ],
    }


def retry_after_seconds(headers, default: float = 1.0) -> float:
    raw = header_value(headers, "Retry-After")
    if not raw:
        return min(default, MAX_RETRY_AFTER_SECONDS)
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(str(raw))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - retry_at.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = default
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


def response_detail(raw: bytes) -> str:
    return raw[:500].decode("utf-8", errors="replace")


def server_context():
    _, root_headers = checked("GET", "/api/", expected=(200,))
    try:
        zotero_version = header_value(root_headers, "X-Zotero-Version", "0")
        version = tuple(int(part) for part in zotero_version.split(".")[:2])
    except ValueError:
        version = (0, 0)
    if version < (10, 0):
        raise ZoteroError(
            "本 helper 要求 Zotero 10 或更高版本；检测到 "
            f"{header_value(root_headers, 'X-Zotero-Version', '未知')}"
        )
    server_id = header_value(root_headers, "Zotero-Server-ID")
    if not server_id:
        raise ZoteroError("本地 API 未返回 Zotero-Server-ID")
    common = {**API, "Zotero-Server-ID": server_id}
    return server_id, common


def authorize_write(server_id: str):
    retries = 0
    while True:
        status, raw, headers = request(
            "POST",
            "/api/local/authorize",
            {"appName": "Codex Zotero PDF"},
            {"Content-Type": "application/json", "Zotero-Server-ID": server_id},
        )
        if status == 429 and retries < MAX_RATE_LIMIT_RETRIES:
            time.sleep(retry_after_seconds(headers))
            retries += 1
            continue
        if status != 200:
            detail = response_detail(raw)
            if status == 403 or '"denied":true' in detail.replace(" ", "").casefold():
                raise ZoteroError("Zotero 本地写入授权被拒绝")
            raise ZoteroError(
                f"Zotero 本地写入授权返回 HTTP {status}"
                + (f": {detail}" if detail else "")
            )
        result = as_json(raw)
        if result.get("denied"):
            raise ZoteroError("Zotero 本地写入授权被拒绝")
        key = str(result.get("key") or "").strip()
        if not key:
            raise ZoteroError("Zotero 本地写入授权未返回 API key")
        return key, result.get("remember") is True


class WriteAuthorization:
    """Track single-use versus remembered Zotero local write authorization."""

    def __init__(self, server_id: str, common):
        self.server_id = server_id
        self.common = dict(common)
        self.key = None
        self.remember = False

    def renew(self):
        result = authorize_write(self.server_id)
        if isinstance(result, tuple):
            self.key, self.remember = result
        else:
            self.key, self.remember = result, False
        if not self.key:
            raise ZoteroError("Zotero 本地写入授权未返回 API key")

    def headers(self, extra=None):
        if not self.key:
            self.renew()
        return {
            **self.common,
            **dict(extra or {}),
            "Zotero-API-Key": self.key,
        }

    def consume(self):
        if not self.remember:
            self.key = None

    def checked(
        self,
        method: str,
        path: str,
        body=None,
        headers=None,
        expected=(200,),
        timeout=60,
    ):
        auth_retries = 0
        rate_limit_retries = 0
        while True:
            write_headers = self.headers(headers)
            status, raw, response_headers = request(
                method,
                path,
                body,
                write_headers,
                timeout=timeout,
            )
            if status in expected:
                self.consume()
                return raw, response_headers
            if status == 401:
                self.key = None
                if auth_retries < 1:
                    auth_retries += 1
                    continue
            if status == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                time.sleep(retry_after_seconds(response_headers))
                rate_limit_retries += 1
                auth_retries = 0
                continue
            self.consume()
            detail = response_detail(raw)
            raise ZoteroError(
                f"{method} {path} 返回 HTTP {status}"
                + (f": {detail}" if detail else "")
            )


def verified_parent(item_key: str, common, *, doi: str | None, title: str | None):
    if not doi and not title:
        raise ZoteroError("必须提供 --doi 或 --title 来核对目标条目")
    parent_path = f"/api/users/0/items/{urllib.parse.quote(item_key)}"
    parent_raw, _ = checked("GET", parent_path, headers=common)
    parent = as_json(parent_raw)
    item = parent.get("data", {})
    if item.get("itemType") == "attachment":
        raise ZoteroError("指定 key 是附件条目，不是论文父条目")
    if doi and normalize_doi(item.get("DOI")) != normalize_doi(doi):
        raise ZoteroError(f"DOI 不匹配：目标条目为 {item.get('DOI') or '(空)'}")
    if title and normalize_title(item.get("title")) != normalize_title(title):
        raise ZoteroError(f"标题不匹配：目标条目为 {item.get('title') or '(空)'}")
    return parent_path, parent, item


def match_items(items, *, doi=None, title=None, authors=None):
    matches = []
    for entry in items:
        item = entry.get("data", {})
        if item.get("deleted") or item.get("itemType") in {
            "attachment",
            "note",
            "annotation",
        }:
            continue
        if doi and normalize_doi(item.get("DOI")) != normalize_doi(doi):
            continue
        if title and normalize_title(item.get("title")) != normalize_title(title):
            continue
        if not author_requirements_match(item, authors):
            continue
        matches.append(entry)
    return matches


def resolve_item(args):
    if not args.doi and not args.title:
        raise ZoteroError("resolve 必须提供 --doi 或 --title")
    _, common = server_context()
    query = args.doi or args.title
    search_path = "/api/users/0/items?" + urllib.parse.urlencode(
        {"q": query, "qmode": "everything"}
    )
    candidates = fetch_all(search_path, common)
    matches = match_items(
        candidates,
        doi=args.doi,
        title=args.title,
        authors=args.author,
    )
    if not matches:
        raise ZoteroError("Zotero 中未找到与 DOI、精确标题和作者条件完全匹配的父条目")
    if len(matches) > 1:
        keys = ", ".join(entry.get("key") or entry.get("data", {}).get("key", "") for entry in matches)
        raise ZoteroError(f"存在多个完全匹配的父条目，请人工消歧：{keys}")
    entry = matches[0]
    item = entry.get("data", {})
    key = entry.get("key") or item.get("key")
    if not key:
        raise ZoteroError("匹配条目没有返回 item key")
    print(
        json.dumps(
            {
                "result": "success",
                "itemKey": key,
                "matchedBy": "doi" if args.doi else "title",
                "evidence": item_evidence(item),
                "version": entry.get("version", item.get("version")),
            },
            ensure_ascii=True,
        )
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_without_redirect(method: str, path: str, headers=None):
    url = path if path.startswith("http://") or path.startswith("https://") else BASE + path
    req = urllib.request.Request(url, method=method, headers=dict(headers or {}))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=60) as response:
            return response.status, response.read(), dict(response.headers.items())
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items())
    except (urllib.error.URLError, TimeoutError) as error:
        raise ZoteroError(f"无法读取 Zotero 附件文件：{error}") from error


def file_uri_to_path(uri: str) -> Path:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme.casefold() != "file":
        raise ZoteroError(f"Zotero 附件返回了非本地文件地址：{uri}")
    path = urllib.request.url2pathname(parsed.path)
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    if os.name == "nt" and re.match(r"^\\[A-Za-z]:", path):
        path = path[1:]
    return Path(path)


def digest_file(path: Path):
    digest = hashlib.md5()
    prefix = b""
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            if len(prefix) < 5:
                prefix += chunk[: 5 - len(prefix)]
            size += len(chunk)
            digest.update(chunk)
    return prefix, size, digest.hexdigest()


def validate_attachment_facts(
    prefix: bytes,
    size: int,
    actual_md5: str,
    *,
    metadata_md5=None,
    expected_md5=None,
    expected_bytes=None,
):
    if prefix != b"%PDF-":
        raise ZoteroError("Zotero 附件文件头不是 PDF")
    metadata_hash = str(metadata_md5 or "").strip().casefold()
    expected_hash = str(expected_md5 or "").strip().casefold()
    actual_hash = str(actual_md5).strip().casefold()
    if expected_hash and actual_hash != expected_hash:
        raise ZoteroError(
            f"附件 MD5 与期望值不匹配：{actual_hash} != {expected_hash}"
        )
    if metadata_hash and actual_hash != metadata_hash:
        raise ZoteroError(
            f"附件文件 MD5 与 Zotero 元数据不匹配：{actual_hash} != {metadata_hash}"
        )
    if not metadata_hash and not expected_hash:
        raise ZoteroError("附件元数据缺少 MD5，且未提供 --expected-md5")
    if expected_bytes is not None and size != expected_bytes:
        raise ZoteroError(f"附件大小与期望值不匹配：{size} != {expected_bytes}")


def attachment_file(attachment_key: str, common):
    path = f"/api/users/0/items/{urllib.parse.quote(attachment_key)}/file"
    status, raw, headers = request_without_redirect("GET", path, headers=common)
    if status == 200 and raw:
        return None, raw
    if status in {301, 302, 303, 307, 308}:
        location = header_value(headers, "Location")
        if not location:
            raise ZoteroError("Zotero 附件重定向缺少 Location")
        local_path = file_uri_to_path(location)
        if not local_path.is_file():
            raise ZoteroError("Zotero 附件文件不存在或不可读取")
        return local_path, None
    detail = response_detail(raw)
    raise ZoteroError(
        f"读取 Zotero 附件文件返回 HTTP {status}"
        + (f": {detail}" if detail else "")
    )


def verify_attachment(args):
    _, common = server_context()
    parent_path, _, _ = verified_parent(
        args.item_key, common, doi=args.doi, title=args.title
    )
    children_raw, _ = checked("GET", parent_path + "/children", headers=common)
    children = as_json(children_raw)
    pdfs = [
        child
        for child in children
        if child.get("data", {}).get("itemType") == "attachment"
        and child.get("data", {}).get("contentType") == "application/pdf"
    ]
    if args.attachment_key:
        selected = [
            child
            for child in pdfs
            if (child.get("key") or child.get("data", {}).get("key"))
            == args.attachment_key
        ]
        if not selected:
            raise ZoteroError("指定 key 不是该父条目下的 PDF 附件")
    elif len(pdfs) == 1:
        selected = pdfs
    elif not pdfs:
        raise ZoteroError("父条目下没有 PDF 附件")
    else:
        keys = ", ".join(
            child.get("key") or child.get("data", {}).get("key", "") for child in pdfs
        )
        raise ZoteroError(f"父条目有多个 PDF 附件，请用 --attachment-key 指定：{keys}")

    child = selected[0]
    data = child.get("data", {})
    attachment_key = child.get("key") or data.get("key")
    local_path, direct_bytes = attachment_file(attachment_key, common)
    if local_path:
        prefix, size, actual_md5 = digest_file(local_path)
        file_source = "local_file"
    else:
        prefix = direct_bytes[:5]
        size = len(direct_bytes)
        actual_md5 = hashlib.md5(direct_bytes).hexdigest()
        file_source = "response_body"
    validate_attachment_facts(
        prefix,
        size,
        actual_md5,
        metadata_md5=data.get("md5"),
        expected_md5=args.expected_md5,
        expected_bytes=args.expected_bytes,
    )
    print(
        json.dumps(
            {
                "result": "success",
                "verified": True,
                "parentKey": args.item_key,
                "attachmentKey": attachment_key,
                "filename": data.get("filename"),
                "linkMode": data.get("linkMode"),
                "fileSource": file_source,
                "pdfBytes": size,
                "md5": actual_md5,
                "md5Verified": True,
            },
            ensure_ascii=True,
        )
    )


def attach(args):
    pdf_path = Path(args.pdf).expanduser().resolve(strict=True)
    if not pdf_path.is_file():
        raise ZoteroError("--pdf 必须指向一个文件")
    prefix, pdf_bytes, md5 = digest_file(pdf_path)
    if prefix != b"%PDF-":
        raise ZoteroError("文件头不是 PDF（缺少 %PDF-）")
    if pdf_bytes >= 4 * 1024**3:
        raise ZoteroError("Zotero 本地 API 只接受小于 4 GB 的文件")

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

    _, version_headers = checked(
        "GET", "/api/users/0/items/top?limit=1", headers=common
    )
    library_version = header_value(version_headers, "Last-Modified-Version")
    if not library_version:
        raise ZoteroError("无法读取文库版本号，停止写入")

    writer = WriteAuthorization(server_id, common)

    filename = pdf_path.name
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
        raw, _ = writer.checked(
            "POST",
            "/api/users/0/items",
            [attachment],
            {"If-Unmodified-Since-Version": library_version},
        )
        created = as_json(raw).get("successful", {}).get("0", {})
        created_key = created.get("key")
        if not created_key:
            raise ZoteroError("Zotero 未返回新附件 key")

        file_form = urllib.parse.urlencode(
            {
                "md5": md5,
                "filename": filename,
                "filesize": pdf_bytes,
                "mtime": str(int(os.path.getmtime(pdf_path) * 1000)),
            }
        ).encode()
        path = f"/api/users/0/items/{created_key}/file"
        raw, _ = writer.checked(
            "POST",
            path,
            file_form,
            {
                "If-None-Match": "*",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            expected=(200, 201),
        )
        upload = as_json(raw)
        if upload.get("exists") != 1:
            upload_url = upload.get("url")
            if not upload_url:
                raise ZoteroError("Zotero 未返回文件上传地址")
            if upload_url.startswith("/"):
                upload_url = BASE + upload_url
            with pdf_path.open("rb") as upload_stream:
                checked(
                    "POST",
                    upload_url,
                    upload_stream,
                    {
                        "Content-Type": "application/pdf",
                        "Content-Length": str(pdf_bytes),
                    },
                    expected=(201,),
                    timeout=300,
                )
            final = urllib.parse.urlencode({"upload": upload["uploadKey"]}).encode()
            writer.checked(
                "POST",
                path,
                final,
                {
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
                    "pdfBytes": pdf_bytes,
                    "md5": md5,
                    "md5Verified": True,
                    "source": args.source_url,
                },
                ensure_ascii=True,
            )
        )
    except Exception as error:
        cleanup_succeeded = False
        if created_key and writer.key:
            try:
                current_raw, _ = checked(
                    "GET", f"/api/users/0/items/{created_key}", headers=common
                )
                current = as_json(current_raw)
                current_version = current.get("version")
                if current_version is not None:
                    writer.checked(
                        "DELETE",
                        f"/api/users/0/items/{created_key}",
                        headers={"If-Unmodified-Since-Version": str(current_version)},
                        expected=(204,),
                    )
                    cleanup_succeeded = True
            except Exception:
                pass
        if created_key and not cleanup_succeeded:
            raise ZoteroError(
                f"{error}；新附件 {created_key} 可能处于不完整状态。"
                "为避免一次授权触发额外写入，helper 未再次请求授权删除；请在 Zotero 中检查。"
            ) from error
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
        total = int(header_value(headers, "Total-Results", len(results)))
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
    parent_path, _, item = verified_parent(
        args.item_key, common, doi=args.doi, title=args.title
    )
    children_raw, _ = checked("GET", parent_path + "/children", headers=common)
    children = as_json(children_raw)
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
                "evidence": item_evidence(item),
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
                "attachments": [
                    {
                        "key": child.get("key") or child.get("data", {}).get("key"),
                        "title": child.get("data", {}).get("title"),
                        "itemType": child.get("data", {}).get("itemType"),
                        "linkMode": child.get("data", {}).get("linkMode"),
                        "contentType": child.get("data", {}).get("contentType"),
                        "filename": child.get("data", {}).get("filename"),
                        "md5": child.get("data", {}).get("md5"),
                    }
                    for child in children
                    if child.get("data", {}).get("itemType") == "attachment"
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
        "evidenceLevel": getattr(args, "evidence_level", None),
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

    parent_version = parent.get("version")
    if parent_version is None:
        raise ZoteroError("无法读取目标条目版本号，停止写入")

    writer = WriteAuthorization(server_id, common)
    writer.checked(
        "PATCH",
        parent_path,
        body,
        {
            "If-Unmodified-Since-Version": str(parent_version),
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

    resolve_parser = subparsers.add_parser(
        "resolve", help="resolve one existing parent item by DOI or exact title"
    )
    resolve_parser.add_argument("--doi", help="DOI to match exactly")
    resolve_parser.add_argument("--title", help="exact Zotero title to match")
    resolve_parser.add_argument(
        "--author",
        action="append",
        default=[],
        help="author used as an additional exact-match guard; repeatable",
    )

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
    classify_parser.add_argument(
        "--evidence-level",
        choices=("full_text", "html", "metadata"),
        required=True,
        help="required evidence level used to choose tags",
    )
    collection = classify_parser.add_mutually_exclusive_group()
    collection.add_argument("--collection-name", help="existing collection name")
    collection.add_argument("--collection-key", help="existing collection key")
    classify_parser.add_argument(
        "--dry-run", action="store_true", help="show pending changes without writing"
    )

    verify_parser = subparsers.add_parser(
        "verify-attachment",
        help="read one PDF attachment through Zotero and verify its header, size, and MD5",
    )
    verify_parser.add_argument("--item-key", required=True)
    verify_parser.add_argument("--attachment-key")
    verify_parser.add_argument("--doi", help="expected DOI; checked against the parent item")
    verify_parser.add_argument(
        "--title", help="expected exact parent title; checked against the parent item"
    )
    verify_parser.add_argument("--expected-md5")
    verify_parser.add_argument("--expected-bytes", type=int)

    args = parser.parse_args()
    try:
        if args.command == "resolve":
            resolve_item(args)
        elif args.command == "attach":
            attach(args)
        elif args.command == "inspect":
            inspect_item(args)
        elif args.command == "classify":
            classify(args)
        elif args.command == "verify-attachment":
            verify_attachment(args)
    except (OSError, ZoteroError) as error:
        print(json.dumps({"result": "failed", "detail": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
