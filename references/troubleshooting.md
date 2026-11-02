# Zotero PDF troubleshooting

Read this reference when local API access, authorization, attachment verification, or classification fails.

| Symptom | Meaning | Action |
| --- | --- | --- |
| Connection refused at `127.0.0.1:23119` | Zotero is closed, still starting, or the local API is disabled. | Start or focus Zotero if Computer Use is available, then enable **Settings → Advanced → Allow other applications on this computer to communicate with Zotero**. Retry discovery once. |
| Missing or older `X-Zotero-Version` | The desktop version does not provide the required local API contract. | Upgrade to Zotero 10+; do not attempt an SQLite workaround. |
| `403` or `denied: true` | The user denied the write authorization. | Stop. Do not re-open the dialog automatically. Report that no write occurred. |
| One `401` | A remembered key expired or the server changed. | The helper renews once. The user must decide the new GUI authorization request. |
| Repeated `401` | Authorization was revoked, the target library changed, or the key is not valid for the write. | Stop and report the request. Do not loop or fabricate a key. |
| `429` | Zotero is throttling authorization or requests. | Honor `Retry-After`; the helper retries at most twice and caps each wait at 15 seconds. If still throttled, stop and let the user retry later. |
| `412 Precondition Failed` | The item or library changed after inspection. | Discard the planned write, run `inspect` again, and recalculate the additions. |
| Multiple exact parent items | DOI/title matching did not yield one target. | Ask the user to choose one item key; do not select the newest or first result. |
| DOI or title conflict | The helper is pointed at the wrong item or local metadata is wrong. | Stop without attaching or classifying. Correct the item metadata or target key. |
| Parent already has a PDF | A second attachment would duplicate the paper. | Run `verify-attachment`. Do not overwrite or replace without a separate user decision. |
| No PDF is available | The legal full text may be HTML-only or unavailable. | Use `html` evidence when readable; otherwise use `metadata` only for high-confidence classification. If evidence is insufficient, write nothing and report `not_found`/`insufficient_evidence`. |
| File does not start with `%PDF-` | The download is an error page, HTML wrapper, or damaged file. | Reject it and obtain a real PDF. Do not upload it as a PDF attachment. |
| Header passes but MD5/size differs | The stored file differs from the planned or reported attachment. | Treat verification as failed; do not report success. Re-download from the verified source or inspect the Zotero attachment. |
| Multiple PDF attachments | Automatic selection is ambiguous. | List their keys and filenames from `inspect`; pass the intended `--attachment-key`. |
| Interrupted attachment flow | A newly created attachment may be empty or incomplete. | The error includes its key when known. Check in Zotero. The helper does not request an extra write authorization solely to delete it. |
| File is 4 GB or larger | Zotero's local file API does not accept it. | Stop; do not split or recompress unless the user explicitly requests another lawful workflow. |
| No GUI or Computer Use | The first local write cannot receive user authorization. | Use read-only resolution/verification, or ask the user to run the write in an interactive Zotero session. |

## Authorization boundaries

- **Allow** authorizes a single write and is consumed by that request.
- **Always Allow** produces a remembered key that can be reused within the current helper process.
- The helper does not persist authorization keys.
- **Clear Write Authorizations** in Zotero revokes remembered grants.
- `computer-use` may launch or focus the application and locate the prompt; it must not choose a security response.

## Evidence fallback

Use evidence in this order: publisher/repository PDF, lawful full-text HTML, then reliable bibliographic metadata with an abstract. A stable DOI and title help identify the paper but do not by themselves prove method, mechanism, or application tags. If classification relies only on metadata, report `"evidenceLevel": "metadata"` and keep tags conservative.
