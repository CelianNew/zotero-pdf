# Zotero PDF

[English](README.md) | [简体中文](README.zh-CN.md)

A Codex skill that obtains lawfully accessible paper PDFs, attaches them to existing Zotero 10 items, and then adds evidence-based tags and collection membership.

The helper verifies the target item before writing, refuses duplicate PDF attachments, preserves existing metadata and classifications, and verifies every classification change by reading it back from Zotero.

## Features

- Resolve and verify a Zotero parent item by DOI or exact title.
- Accept official open-access PDFs, repository copies, or files the user is authorized to use.
- Reject non-PDF files and duplicate PDF attachments.
- Reuse existing tag vocabulary and collection names.
- Read the full library tag vocabulary and collection hierarchy before classification.
- Add 3–6 evidence-based tags instead of repetitive metadata such as author or year.
- Append tags and collection membership without replacing existing values.
- Guard item writes with `If-Unmodified-Since-Version` and require a successful read-back.
- Locate, launch, or focus Zotero through Computer Use; authorization remains a user action.
- Keep temporary files and authorization material out of the published package.

## Requirements

- Codex with skill support.
- Zotero Desktop 10 or newer.
- Zotero's local API reachable at `http://127.0.0.1:23119`.
- Python 3.10 or newer.
- The `zotero` skill for item lookup and attachment verification.
- The `computer-use` skill when Zotero must be launched or an authorization window must be located.

No third-party Python packages are required.

## Install

Install globally with the Skills CLI:

```bash
npx skills add CelianNew/zotero-pdf -g
```

You can inspect the skills exposed by the repository without installing:

```bash
npx skills add CelianNew/zotero-pdf --list
```

For manual installation, clone the repository and place the repository root in your agent's skills directory. The directory containing `SKILL.md` must be named `zotero-pdf`.

## Use

Invoke the skill explicitly:

```text
Use $zotero-pdf to find and verify the open-access PDF for DOI 10.xxxx/example,
attach it to the matching Zotero item, and classify it using my existing tags
and the most specific collection.
```

The skill first uses the companion Zotero capability to identify the exact parent item. It then checks the PDF and calls:

```text
python scripts/zotero_pdf.py attach ...
python scripts/zotero_pdf.py inspect ...
python scripts/zotero_pdf.py classify ...
```

Run the helper from the skill directory. See `python scripts/zotero_pdf.py --help` for all arguments.

### Attach

```bash
python scripts/zotero_pdf.py attach \
  --item-key ITEM_KEY \
  --pdf /path/paper.pdf \
  --source-url "https://..." \
  --doi "10.xxxx/..." \
  --title "Exact Zotero title"
```

At least one of `--doi` or `--title` is required.

### Inspect

```bash
python scripts/zotero_pdf.py inspect \
  --item-key ITEM_KEY \
  --title "Exact Zotero title"
```

The result includes the item's current tags and collections, the full library tag vocabulary, and collection keys, names, parents, and hierarchy paths.

### Classify

```bash
python scripts/zotero_pdf.py classify \
  --item-key ITEM_KEY \
  --title "Exact Zotero title" \
  --collection-name "Target collection" \
  --tag "Research topic" \
  --tag "Method" \
  --tag "Method/role"
```

Add `--dry-run` to preview the exact additions. A successful write reports `"verified": true` only after Zotero returns the new tags and collection membership.

## Write Guarantees

The helper does not modify bibliographic fields, overwrite an existing PDF, create duplicate parent items, or create collections automatically. Classification is append-only:

- Existing tags are preserved.
- Existing collection memberships are preserved.
- Duplicate tags are ignored case-insensitively.
- Writes use Zotero's optimistic concurrency version header.
- The final state is read back from Zotero before success is reported.

## Authorization and Privacy

Zotero may ask for local write authorization. Computer Use may locate or focus that window, but the user must choose **Allow**, **Always Allow**, or **Deny**. The skill must not automate those security decisions.

The local API key is held only in process memory. It must not be written to command lines, logs, documentation, or temporary files.

## Scope

This skill attaches PDFs to existing Zotero records and organizes those records. It does not bypass paywalls, log in to publisher accounts on the user's behalf, fabricate bibliographic data, or replace the Zotero application's own library management tools.

## Validation

The package is checked with the Codex skill validator, Python syntax validation, Skills CLI discovery, and focused tests for tag merging, collection resolution, version-guarded writes, and post-write verification.

## License

MIT. See [LICENSE](LICENSE).
