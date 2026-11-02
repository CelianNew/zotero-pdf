---
name: zotero-pdf
description: Attach lawfully accessible PDFs to existing Zotero 10 items through the local API and add evidence-grounded tags and collection membership. Use for paper PDF attachment, attachment verification, or automatic Zotero classification. 为 Zotero 文献保存合法获取的论文 PDF，并依据证据自动打标签和归类。
license: MIT
---

# Zotero PDF

Use this skill to attach a lawfully accessible paper PDF to an existing Zotero item, verify the stored file, and classify the item from the evidence actually available. The helper calls Zotero Desktop's local API directly; the separate `zotero` skill, `computer-use` skill, and Zotero browser connector are optional.

In commands below, replace `<SKILL_DIR>` with the absolute directory containing this `SKILL.md`.

## Required Checks

- Use Zotero Desktop 10 or newer with **Settings → Advanced → Allow other applications on this computer to communicate with Zotero** enabled.
- Identify the parent item by DOI, or by an exact title plus author evidence. If several Zotero items still match exactly, stop and ask the user to disambiguate by item key.
- Prefer a publisher open-access PDF, author or institutional repository copy, arXiv, or PubMed Central. Never bypass a paywall, login, licence, or access control.
- Do not edit bibliographic fields, create duplicate parent items, replace an existing PDF, or create a collection.
- Never print, store, or put a Zotero API key in a command line, file, log, or final response.
- Treat no PDF as a valid research outcome. Do not create a failed or missing-PDF tag unless the user explicitly asks for a missing-item ledger.

## Resolve the Item

Use the helper for DOI/title/author matching before writing:

```powershell
python "<SKILL_DIR>\scripts\zotero_pdf.py" resolve `
  --doi "10.xxxx/example" `
  --author "Family, Given"
```

An exact title can replace `--doi`; add `--author` whenever available. Then inspect the item and its attachments:

```powershell
python "<SKILL_DIR>\scripts\zotero_pdf.py" inspect `
  --item-key ITEM_KEY `
  --doi "10.xxxx/example"
```

Continue only when title, creators, DOI/stable identifier, and requested bibliography agree. On a conflict, wrong DOI/title, or ambiguous duplicate, stop without writing.

## Choose the Evidence Path

Use the strongest available evidence and state that level in the result:

1. **PDF (`full_text`)**: Download to a temporary file, confirm the `%PDF-` header and bibliographic identity, then inspect existing PDF attachments.
2. **HTML (`html`)**: Read the lawful article or repository HTML when no PDF is available. Classify conservatively from methods, abstract, results, and explicit claims.
3. **Metadata (`metadata`)**: If there is neither readable full text nor HTML, classify only when a complete, reliable title, abstract, publication venue, and appropriate subject terms support the chosen tags.
4. **No reliable evidence**: Report `not_found` or `insufficient_evidence`; do not classify.

If a PDF already exists, verify it instead of attaching another:

```powershell
python "<SKILL_DIR>\scripts\zotero_pdf.py" verify-attachment `
  --item-key ITEM_KEY `
  --attachment-key ATTACHMENT_KEY `
  --doi "10.xxxx/example"
```

When no PDF exists but the item should still be organized, skip attachment and continue to classification with `--evidence-level html` or `--evidence-level metadata`.

## Attach a PDF

After verifying the item, legal source, local file, and absence of another PDF:

```powershell
python "<SKILL_DIR>\scripts\zotero_pdf.py" attach `
  --item-key ITEM_KEY `
  --pdf "C:\path\paper.pdf" `
  --source-url "https://..." `
  --doi "10.xxxx/example" `
  --title "Exact Zotero title"
```

Zotero authorizes local writes through a runtime dialog. Select **Always Allow** when the user is comfortable reusing the in-process authorization; **Allow** is single-use, so a multi-step attachment may show several prompts. The helper renews once after `401`, honors bounded `429` retries, and keeps remembered keys only in process memory.

`computer-use` can locate or focus Zotero and the authorization window when available. The user must make the security decision; do not click Allow, Always Allow, or Deny for them. Do not loop on a blocked dialog. Requests that need no API key, including initial local-API discovery and attachment verification, do not require Computer Use.

After attachment, verify through Zotero rather than trusting the upload response:

```powershell
python "<SKILL_DIR>\scripts\zotero_pdf.py" verify-attachment `
  --item-key ITEM_KEY `
  --attachment-key ATTACHMENT_KEY `
  --expected-md5 MD5_FROM_ATTACH_RESULT
```

Re-check the attachment returned by `inspect` when the attachment result does not include its key. Success requires a `%PDF-` header, a readable stored file, matching Zotero MD5, and any expected byte size. Remove the temporary download only after verification.

## Classify the Item

Read the current item, full library tag vocabulary, and collection hierarchy from `inspect`. Reuse existing tags and the most specific existing collection. Add usually 3–6 stable tags that identify what the paper studies, how it does it, its role or mechanism, and the relevant application. Do not infer tags from title similarity alone.

Preview the additions:

```powershell
python "<SKILL_DIR>\scripts\zotero_pdf.py" classify `
  --item-key ITEM_KEY `
  --doi "10.xxxx/example" `
  --evidence-level full_text `
  --collection-name "Target collection" `
  --tag "Research topic" `
  --tag "Method" `
  --tag "Method/role" `
  --dry-run
```

The `--evidence-level` option is required so every classification result records whether it used full text, HTML, or metadata evidence. Run the same command without `--dry-run` to write. Classification is append-only: existing tags and collection memberships remain, and the helper reads the item back before returning `"verified": true`.

Report success only after both operations used have passed:

- PDF path: `attach` succeeded and `verify-attachment` returned `verified: true`.
- No-PDF path: classification returned `verified: true`, with evidence level `html` or `metadata`.
- Read-only path: no writes occurred; report the missing attachment or insufficient evidence plainly.

## Authorization and Dependencies

The first local **write** requires the interactive Zotero authorization dialog. Read access and attachment verification normally need no key. A headless session can use read-only commands but cannot complete the first write authorization. Zotero's local API key is valid for the editable library and is not collection-scoped; rely on the helper's DOI/title checks and append-only writes for target safety.

Required:

- Codex with skill support.
- Zotero Desktop 10+ with the local API enabled and reachable at `http://127.0.0.1:23119`.
- Python 3.10+ with its standard library.
- Network and document retrieval capability for finding a lawful paper source.

Optional:

- Node.js/npm when installing through `npx skills add`.
- `computer-use` when launching or focusing Zotero on a GUI system.
- A separate `zotero` skill; this helper performs its own item resolution and attachment verification.
- Zotero browser connector; it is not needed by this workflow.

The local API is unauthenticated for reads. Do not expose port `23119` to other machines. If startup, authorization, API compatibility, or write verification fails, read [references/troubleshooting.md](references/troubleshooting.md) before retrying and report the exact failing stage.
