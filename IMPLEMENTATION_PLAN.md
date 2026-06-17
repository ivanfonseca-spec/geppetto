# Geppetto 2 — Implementation Plan: Multi-Project KB Sync + Meeting Notes

*Plan only. No code is changed by this document. It describes how to add four capabilities to the existing real-time system.*

## Goals (from request)

1. **Scan & sync** — review documents in a project folder, detect new/modified/deleted files, and update that project's knowledge base accordingly.
2. **Trigger sync** — automatically when a project is selected (and re-runnable on demand).
3. **Project selection** — pick which project to work with *before* starting a call.
4. **Meeting notes** — on meeting end, generate structured notes (Current status, Key achievements, Risks & issues, Decisions made, Next actions), label anything uncertain with `[UNCERTAIN]`, and save to the project folder as `YYYY-MM-DD_<description>.md`.

## Confirmed decisions

- **Layout:** subfolders under one root, one project per subfolder.
- **Doc types to ingest:** PDF, Word (.docx), Markdown/text (.md/.txt), Excel/CSV (.xlsx/.csv).
- **Sync trigger:** auto-scan when a project is selected (a manual "re-sync now" reuses the same path at near-zero extra cost).

---

## 1. New on-disk structure

Each project is self-contained under a single projects root:

```
projects/
  <ProjectName>/
    docs/                     # user drops source documents here (PDF/docx/md/txt/xlsx/csv)
    kb/                       # ChromaDB store for THIS project (replaces the global chroma_data/)
    meetings/                 # saved reports + generated meeting notes for this project
    .geppetto_manifest.json   # sync state: per-file hash, mtime, and the chunk ids it produced
```

This isolates each project's knowledge base, history, and notes. The current global `chroma_data/` + `meetings/` become a one-time migration into a default project (e.g. `projects/Default/`).

---

## 2. New modules to build

**`project_registry.py`** — discovers projects by listing subfolders under the projects root; resolves a project's `docs/`, `kb/`, `meetings/`, and manifest paths; scaffolds the folders for a new project; sanitizes the project name into a valid Chroma collection name.

**`doc_loaders.py`** — extracts plain text from each supported format and returns text + metadata (filename, modified date):
- PDF → `pdfplumber` (or `pypdf`)
- Word → `python-docx`
- Markdown/text → direct read
- Excel → `openpyxl` (sheet/row flattening)
- CSV → stdlib `csv`

**`kb_sync.py`** — the incremental sync engine (detail in §3). Public call: `sync(project) -> {added, updated, removed, skipped}`.

**`meeting_notes.py`** — generates the structured notes from a finished meeting (detail in §6).

---

## 3. Document scan & incremental sync logic

Driven by `.geppetto_manifest.json`, which maps each relative file path to `{sha256, mtime, size, chunk_ids[], ingested_at}`.

On `sync(project)`:

1. **Enumerate** files in `docs/` matching the allowed extensions.
2. **Classify** each file:
   - not in manifest → **NEW**
   - in manifest, hash changed → **MODIFIED**
   - in manifest, hash unchanged → **SKIP** (fast-path on mtime+size, confirm with hash)
   - in manifest, missing on disk → **DELETED**
3. **For NEW / MODIFIED:** delete that file's old chunks from the collection (by stored `chunk_ids`), extract text via `doc_loaders`, chunk it (~800-char windows with overlap), embed and upsert into the project's Chroma collection with metadata `{source, project, chunk_index, modified_date}`, then record the new chunk ids in the manifest.
4. **For DELETED:** remove its chunks from the collection and drop the manifest entry.
5. **Return a summary** (counts) for the dashboard to display.

Only changed files are re-embedded, so repeated syncs are cheap. The `modified_date` metadata feeds the existing alert **freshness** field and supports the `OUTDATED` verdict.

---

## 4. Refactor: make the KB per-project

Today `phase2_validator.py` and `phase3_integration.py` hardcode `./chroma_data` and collection `project_knowledge`. Changes:

- Validator stops loading a global KB at import time; it accepts a **project's collection** (resolved from `project_registry`).
- `MeetingValidator` / `get_validator()` becomes project-aware — it can rebind to the active project's `kb/` collection.
- `phase3_storage.py` `base_dir` becomes the **active project's `meetings/`** instead of the global one.

This is the one change that touches existing files; everything else is additive.

---

## 5. Server + dashboard: project selection

New endpoints in `phase3_server_realtime.py`:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/projects` | List discovered projects |
| `POST` | `/api/projects/{name}/select` | Set active project → **auto-sync** → bind its KB + meetings dir; returns sync summary |
| `POST` | `/api/projects/{name}/sync` | Manual re-sync (same engine) |
| `GET` | `/api/projects/{name}/status` | Doc count, last sync time, last summary |

Flow & UI changes:
- **Dashboard** gains a **project picker** shown before "Start live meeting." Selecting a project calls `/select`, runs the sync in a threadpool, and shows the result (e.g. "3 added, 1 updated").
- **`/api/session/start`** requires an active project and binds the `LiveSession` to that project's KB and `meetings/` folder.
- **Concurrency guard:** block/queue a sync if a live session is using that KB, so validation isn't disrupted mid-call.

---

## 6. Meeting notes generation

New step at **session end**, after the report is saved.

- **Inputs:** the final rolling transcript + the validation alerts (e.g. `CONTRADICTED` claims naturally become Risks/Issues) + project name + date.
- **Generation:** a Claude prompt produces exactly five sections — **Current status, Key achievements, Risks & issues, Decisions made, Next actions** — grounded in the transcript and alerts. Any item the transcript doesn't clearly support is prefixed with **`[UNCERTAIN]`**.
- **Output file:** Markdown saved to the project's folder as **`YYYY-MM-DD_<short-description>.md`**, where the description is a slug of an LLM-generated short title (e.g. `2026-06-16_sprint-status-review.md`).
- **Wiring:** the `"ended"` WebSocket message includes the notes file path so the dashboard can link to it; the file also appears in the project's history.

---

## 7. New dependencies

`pdfplumber` (or `pypdf`), `python-docx`, `openpyxl`. (`chromadb`, `openai`, `anthropic` already present; CSV via stdlib.)

---

## 8. Suggested build order

1. **Foundation** — `project_registry.py` + per-project KB refactor (§2, §4); migrate existing data into a `Default` project.
2. **Sync** — `doc_loaders.py` + `kb_sync.py` + manifest (§3).
3. **Selection** — project endpoints + dashboard picker + auto-sync on select (§5).
4. **Notes** — `meeting_notes.py` + save + wire into session end (§6).
5. **Verify** — unit-test the chunker/manifest/loaders; end-to-end test: select project → edit a doc → re-select → confirm DB updates → run a call → confirm notes file written with correct name and `[UNCERTAIN]` tags.

---

## 9. Risks & considerations

- **Extraction quality** for PDF/Excel varies; flag low-yield files in the sync summary.
- **Collection naming** must be sanitized to Chroma's constraints.
- **Sync vs. live call** on the same KB — needs the concurrency guard (§5).
- **Backward compatibility** — migrate current `chroma_data/` and `meetings/` into the `Default` project so nothing is lost.
- **Notes grounding** — the `[UNCERTAIN]` rule depends on a disciplined prompt; worth a few test transcripts to tune.
