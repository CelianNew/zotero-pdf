# Zotero PDF

[English](README.md) | [简体中文](README.zh-CN.md)

A Codex skill for attaching a lawfully accessible paper PDF to an existing Zotero 10 item and adding evidence-grounded tags and collection membership. A small standard-library Python helper calls Zotero Desktop's local API, checks the target bibliography before writing, verifies the stored PDF, and reads classification changes back from Zotero.

Version `1.0.1` adds direct item resolution, attachment verification, metadata-only classification, safer authorization handling, streaming uploads, and explicit fallbacks when no PDF can be found.

## What it handles

| Situation | Behavior |
| --- | --- |
| Lawful PDF found | Attach it, verify the stored file, then classify from the full text. |
| PDF already exists | Verify it instead of creating a duplicate. |
| Only HTML full text | Classify from HTML and mark the evidence level as `html`; do not create a placeholder PDF. |
| Reliable abstract and metadata only | Classify conservatively and mark the evidence level as `metadata`. |
| Insufficient evidence | Report `not_found` or `insufficient_evidence` without inventing a missing-PDF tag. |
| Ambiguous or conflicting metadata | Stop before writing and report the mismatch or duplicate item keys. |

## Features

- Resolve one parent item by DOI, exact title, and optional author checks.
- Read bibliographic evidence, existing tags, collection paths, and child attachments.
- Attach a PDF only after checking its header, size, target identity, and duplicate status.
- Stream PDF uploads and reject files of 4 GB or more.
- Verify the attachment through Zotero's file endpoint using its `%PDF-` header, MD5, and optional byte count.
- Reuse existing tag vocabulary and select an existing collection hierarchy.
- Append tags and collection membership without replacing existing classifications.
- Guard writes with Zotero version headers and require post-write read-back.
- Handle single-use and remembered authorization keys without persisting them.
- Retry one `401` after reauthorization and honor bounded `429` retries.

## Requirements

### Required

- Codex with skill support.
- Zotero Desktop **10 or newer**.
- Zotero's local API enabled and reachable at `http://127.0.0.1:23119`.
- Python **3.10 or newer**.
- Network and document retrieval capability for locating a lawful paper source.

The helper uses only the Python standard library. No third-party Python packages are required.

### Optional

- Node.js/npm, only when installing with `npx skills add`.
- `computer-use`, when a GUI agent needs to launch or focus Zotero.
- A separate `zotero` skill. This package performs its own item resolution and attachment verification.
- The Zotero browser connector. It is not required.

The first local **write** requires an interactive Zotero authorization dialog, so a fully headless first write is unsupported. Read-only resolution and attachment verification normally work without authorization.

## Install

```bash
npx skills add CelianNew/zotero-pdf -g
```

Inspect the repository without installing it:

```bash
npx skills add CelianNew/zotero-pdf --list
```

For a manual install, place the repository root in your agent's skills directory. The directory containing `SKILL.md` must be named `zotero-pdf`.

## Zotero setup

1. Start Zotero Desktop 10 or newer.
2. Open **Settings → Advanced**.
3. Enable **Allow other applications on this computer to communicate with Zotero**.
4. Confirm that `http://127.0.0.1:23119/api/` responds before running a write command.

Do not expose the local API port to other machines. Zotero local API reads are unauthenticated.

## Use

Invoke the skill with a concrete paper and target collection:

```text
Use $zotero-pdf to find a lawful PDF for DOI 10.xxxx/example,
attach it to the matching Zotero item, verify the stored file, and classify
it with my existing tags and the most specific collection.
```

The core command sequence is:

```text
resolve → inspect → attach → verify-attachment → classify
```

### Resolve and inspect

```bash
python scripts/zotero_pdf.py resolve \
  --doi "10.xxxx/example" \
  --author "Family, Given"

python scripts/zotero_pdf.py inspect \
  --item-key ITEM_KEY \
  --doi "10.xxxx/example"
```

`resolve` returns one item key or lists exact duplicates for manual disambiguation. `inspect` returns bibliographic evidence, the current tags and collections, the full library vocabulary, collection paths, and PDF attachment keys and hashes.

### Attach and verify

```bash
python scripts/zotero_pdf.py attach \
  --item-key ITEM_KEY \
  --pdf /path/paper.pdf \
  --source-url "https://..." \
  --doi "10.xxxx/example" \
  --title "Exact Zotero title"

python scripts/zotero_pdf.py verify-attachment \
  --item-key ITEM_KEY \
  --attachment-key ATTACHMENT_KEY \
  --expected-md5 MD5_FROM_ATTACH_RESULT
```

At least one of `--doi` or `--title` is required for `attach`. The helper refuses an existing PDF attachment, a non-PDF file, or a size of 4 GB or more.

### Classify

```bash
python scripts/zotero_pdf.py classify \
  --item-key ITEM_KEY \
  --doi "10.xxxx/example" \
  --evidence-level full_text \
  --collection-name "Target collection" \
  --tag "Research topic" \
  --tag "Method" \
  --tag "Method/role" \
  --dry-run
```

The required `--evidence-level` value is `full_text`, `html`, or `metadata`. Remove `--dry-run` to write. A successful write returns `"verified": true` only after read-back confirms every requested tag and collection.

## Authorization

Zotero may show **Allow**, **Always Allow**, and **Deny** when a local write begins.

- **Allow** is valid for one write. A multi-step attachment can require several prompts.
- **Always Allow** creates a remembered key reused within the current helper process.
- **Deny** stops the workflow.
- Zotero's **Clear Write Authorizations** action revokes remembered grants.

The helper keeps keys in process memory and never writes them to disk. Computer Use can locate or focus Zotero, but the user chooses whether to authorize the request.

## Write guarantees

The helper does not:

- Edit title, creators, DOI, or other bibliographic fields.
- Create a duplicate parent item or replace an existing PDF.
- Create a collection automatically.
- Claim success when attachment or classification verification fails.

Classification is append-only: existing tags and collection memberships are preserved, duplicate tags are ignored case-insensitively, writes use Zotero's concurrency version, and the final item is read back before success is reported.

## No-PDF behavior

If a lawful PDF cannot be found:

1. Use a readable publisher or repository full-text HTML when available and record `--evidence-level html`.
2. If only reliable metadata and an abstract are available, add conservative tags with `--evidence-level metadata`.
3. If the evidence does not support those tags, write nothing and report `not_found` or `insufficient_evidence`.

The default workflow does not attach HTML masquerading as a PDF, create an empty attachment, or add a failure tag. A missing-item ledger is opt-in.

## Troubleshooting

The common failure matrix covers local API startup, `401`, `403`, `429`, `412`, duplicates, metadata conflicts, corrupted PDFs, missing PDFs, multiple attachments, interrupted uploads, and headless authorization:

- [English and Chinese routing is included in the skill instructions.](SKILL.md)
- [Detailed troubleshooting reference.](references/troubleshooting.md)

## Compatibility and validation

- Windows is continuously tested with Python 3.10 and 3.12.
- The implementation uses portable Python and HTTP APIs, but other operating systems have not been certified.
- Release validation includes the Codex skill validator, CLI help checks, attachment and authorization tests, and Skills CLI discovery.

## License

MIT. See [LICENSE](LICENSE).
