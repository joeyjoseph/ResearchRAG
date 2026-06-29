# Research RAG — Research Infrastructure Handoff Document

**Project:** Research RAG
**Date:** June 2026
**Prepared by:** Claude Sonnet 4.6 (Anthropic)
**Prepared for:** Mostly other robots, but a human may find this useful.
All filepaths are generic names in this document.

---

## Project Overview

This document describes the research infrastructure built to support an author writing a book. The system provides a searchable, indexed research corpus combined with an AI research assistant (Hermes) capable of answering questions, surfacing connections, and writing research memos from a 1GB+ collection of source documents and the author's own writing.

---

## System Architecture

The system consists of three layers:

```
Layer 1 — Raw Files
    Corpus/          research source material (books, articles, interviews, misc)
    Work/            author's own writing (drafts, manuscript, memos, notes)

Layer 2 — Index
    SQLite           metadata index (corpus.db)
    ChromaDB         vector/semantic index (chroma/)

Layer 3 — Agent
    Hermes           AI research assistant with corpus skill and soul document
```

These layers work together as a RAG (Retrieval Augmented Generation) system. Hermes uses the index to navigate the corpus rather than reading every file on every query.

---

## File and Folder Structure
The default folder structure is listed below. You may add or remove new categories in the Corpus and Work folder.

```
/Users/User/Documents/Project Name/
    Corpus/
            Articles Journals Websites/
            Books/
            Interviews/
            Misc/
    Work/
        Drafts/
        Manuscript/
        Notes/
        Research Memos/
    Index/
        corpus.db              SQLite metadata database
        chroma/                ChromaDB vector index
        ingest.log             nightly ingest run log
        summarize.log          nightly summarize run log
        currentstatus.log      nightly manuscript status log
    Scripts/
        autoadd.py             add to corpus script
        ingest.py              corpus indexing script
        PathUpdate.py          update config with custom paths
        summarize.py           abstract generation script
        currentstatus.py       manuscript status updater
        config.ini             all project settings
        install.sh             environment setup script
    corpus-env/                Python virtual environment
    PROJECT_CONTEXT.md         project context for Hermes
    .manuscript_status_cache.json  cache for currentstatus.py
```

---

## Databases

### SQLite — corpus.db

Stores metadata for every document in the corpus and Work folder.

**Schema:**
```sql
CREATE TABLE documents (
    file_path        TEXT PRIMARY KEY,
    filename         TEXT,
    source_type      TEXT,      -- top-level folder name
    subfolder        TEXT,      -- immediate parent if nested
    document_role    TEXT,      -- 'source' (Corpus) or 'author' (Work)
    word_count       INTEGER,
    last_modified    REAL,      -- OS timestamp for change detection
    ingest_date      TEXT,
    chunk_count      INTEGER,   -- number of ChromaDB chunks
    abstract         TEXT,      -- structured 12-field research abstract
    notes            TEXT,      -- manually added annotations
    status           TEXT,      -- 'ingested', 'skipped', 'failed'
    year_published   TEXT,
    author           TEXT,
    names_mentioned  TEXT        -- JSON array of person names (spaCy NER)
);
```

**Abstract field structure** (each field on its own line):
```
DOCUMENT_TYPE:
TOPIC:
ARGUMENT_OR_THESIS:
STANCE:
TIME_PERIOD:
KEY_PEOPLE:
EVIDENCE_TYPE:
CONTRADICTIONS:
QUOTABLE_MATERIAL:
RELEVANCE_TAGS:
SOURCE_RELIABILITY:
CORPUS_CONNECTIONS:
```

**Browse with:** DB Browser for SQLite (https://sqlitebrowser.org)

**Query from terminal:**
```bash
sqlite3 "/Users/User/Documents/Project Name/Index/corpus.db" "SELECT COUNT(*) FROM documents WHERE status='ingested';"
```

### ChromaDB — chroma/

Stores vector embeddings of every document chunk for semantic search.

- **Collection name:** `research_corpus`
- **Embedding model:** `all-MiniLM-L6-v2` (sentence-transformers, runs locally)
- **Chunk size:** 400 words with 50-word overlap
- **Metadata per chunk:** file_path, source_type, subfolder, document_role, author, year_published, chunk_number

**Semantic search from terminal:**
```bash
/Users/User/Documents/Project Name/corpus-env/bin/python3 -c "
import chromadb
from sentence_transformers import SentenceTransformer
query = 'YOUR QUERY HERE'
chroma = chromadb.PersistentClient(path='/Users/User/Documents/Project Name/Index/chroma')
collection = chroma.get_collection('research_corpus')
embedder = SentenceTransformer('all-MiniLM-L6-v2')
results = collection.query(query_embeddings=embedder.encode([query]).tolist(), n_results=5, include=['documents','metadatas','distances'])
for i,(doc,meta,dist) in enumerate(zip(results['documents'][0],results['metadatas'][0],results['distances'][0])):
    print(f'[{round((1-dist)*100,1)}%] {meta[\"file_path\"]}\n{doc[:200]}\n')
"
```

---

## Scripts

### ingest.py

Scans Corpus and Work folders for new or changed `.txt` files, extracts text, chunks and embeds into ChromaDB, and writes metadata to SQLite.

**Key behaviors:**
- Incremental — skips files already in the index with unchanged timestamps
- Detects changes via `last_modified` OS timestamp
- Derives `source_type` from top-level category folder
- Derives `subfolder` from immediate parent folder
- Sets `document_role = 'source'` for Corpus files, `'author'` for Work files
- Extracts `year_published` and `author` from filename and document header
- Extracts `names_mentioned` using spaCy NER (en_core_web_sm model)
- Logs all activity to `Index/ingest.log`

**Usage:**
```bash
cd "/Users/User/Documents/Project Name"
source corpus-env/bin/activate
python3 Scripts/ingest.py --config Scripts/config.ini

# Repair source_type/subfolder/document_role metadata without re-embedding:
python3 Scripts/ingest.py --config Scripts/config.ini --repair
```

**Adding new top-level folders:** Create the folder under `Corpus/` or `Work/`, then either add a line for it under `[corpus_categories]` / `[work_categories]` in `config.ini` by hand, or run `Scripts/PathUpdate.py` to detect and add it automatically (with a blank description, ready for you to fill in).

---

### summarize.py

Generates structured 12-field research abstracts for ingested documents and writes them to the `abstract` field in SQLite.

**Key behaviors:**
- Processes documents where `abstract` is empty (or all documents with `--regenerate`)
- Routes automatically by `document_role`:
  - `source` documents → Anthropic API (`claude-sonnet-4-6`)
  - `author` documents → LM Studio local server (keeps unpublished writing off the API)
- Processes first 3,000 words of each document for the abstract
- Configurable batch size, delay between requests, and retry logic
- Respects Anthropic rate limits with exponential backoff

**Usage:**
```bash
python3 Scripts/summarize.py --config Scripts/config.ini
python3 Scripts/summarize.py --config Scripts/config.ini --folder Interviews
python3 Scripts/summarize.py --config Scripts/config.ini --folder Books --regenerate
```

**LM Studio requirement:** Must be running with a model loaded before processing Work/author documents.

---

### autoadd.py

Adds files from a predefined folder to the corpus.

**Key behaviors:**
- Searches for documents in a predefinied [Add To Corpus] folder.
- Converts them into txt files
- Valid categories and their classification guidance are both read from `config.ini`'s `[corpus_categories]` section at startup — never hardcoded. A category with a blank description still works as a valid filing target, but gets a generic fallback line in the classification prompt instead of tailored guidance; fill in a real description in `config.ini` to improve accuracy for that category.
- Warns at startup if no `Misc`-named (or similarly-named) catch-all category exists, since low-confidence documents have nowhere safe to land without one.
- Deletes originals in [Add To Corpus] when complete.

**Usage:**
```bash
cd "/Users/YourUserName/ProjectRoot"
source corpus-env/bin/activate
python3 Scripts/autoadd.py --config Scripts/config.ini

# To preview what would happen without writing files or deleting originals:
# python3 Scripts/autoadd.py --config Scripts/config.ini --dry-run
```
**Anthropic or other model must be loaded to convert and categorize**

### PathUpdate.py

Scans `CORPUS_ROOT` and `WORK_ROOT` for whatever top-level subfolders actually exist and additively merges them into `config.ini`'`[corpus_categories]` and `[work_categories]` sections — any folder not already listed gets added with a blank description; existing entries (and any descriptions you've written) are never modified or removed. Folders that have an entry in `config.ini` but no longer exist on disk produce a warning instead of being deleted automatically. Neither
`ingest.py` nor `autoadd.py` is ever edited by this script — both read `config.ini` directly at runtime.

**Usage**
```bash
cd "/Users/YourUserName/ProjectRoot"
source corpus-env/bin/activate
python3 Scripts/PathUpdate.py --config Scripts/config.ini
```

### currentstatus.py

Scans `Work/Manuscript/` and updates the `## Current Manuscript Status` section in `PROJECT_CONTEXT.md` with a 1-2 sentence summary of each manuscript file.

**Key behaviors:**
- Uses LM Studio only — manuscript content never sent to external API
- Caches summaries in `.manuscript_status_cache.json` — only re-summarizes changed files
- Replaces the entire status section on each run for accuracy
- Groups entries by subfolder within Manuscript
- Logs to `Index/currentstatus.log`

**Usage:**
```bash
python3 Scripts/currentstatus.py --config Scripts/config.ini
```
---

### config.ini

All project settings in one file. Edit this rather than the scripts.

```ini
[paths]
CORPUS_ROOT     = /Users/User Name/Documents/Project Name/Corpus
WORK_ROOT       = /Users/User/Documents/Project Name/Work
PROJECT_ROOT    = /Users/User/Documents/Project Name
DB_PATH         = /Users/User/Documents/Project Name/Index/corpus.db
CHROMA_PATH     = /Users/User/Documents/Project Name/Index/chroma
LOG_PATH        = /Users/User/Documents/Project Name/Index/ingest.log

[chunking]
CHUNK_SIZE      = 400
CHUNK_OVERLAP   = 50
MIN_WORDS_SKIP  = 50
MIN_WORDS_SINGLE_CHUNK = 200

[embedding]
EMBEDDING_MODEL = all-MiniLM-L6-v2

[chromadb]
COLLECTION_NAME = research_corpus

[corpus_categories]
Books = full-length books or book-length manuscripts.
Articles Journals Websites = journal articles, magazine or newspaper articles, blog posts, or website content.
Interviews = oral histories, interview transcripts, or Q&A-format documents...
Misc = anything else, or anything that cannot be confidently classified...

[work_categories]
Proposal =
Drafts =
Manuscript =
Research Memos =
Notes =

[summarize]
LLM_BACKEND     = anthropic
LOCAL_BACKEND   = lmstudio
LLM_MODEL       = claude-sonnet-4-6
ANTHROPIC_API_KEY =          # leave blank — set via environment variable
LMSTUDIO_BASE_URL = http://localhost:1234/v1
BATCH_SIZE      = 50
DELAY_BETWEEN_REQUESTS = 3
MAX_RETRIES     = 5
RESEARCH_THEMES = [comma-separated list of book research themes]
```

*Work/ categories are listed for consistency, but have no description field in active use at this time.*

---

### install.sh

One-command environment setup for a new machine. Creates virtual environment, installs all packages, downloads embedding and NER models.

```bash
cd "/Users/User/Documents/Project Name"
bash Scripts/install.sh
```

**Packages installed:** chromadb, sentence-transformers, spacy (+ en_core_web_sm), anthropic, requests

---

## Cron Schedule

Five nightly jobs run automatically:

```
# Research RAG — nightly automation
# Order of execution: autoadd -> ingest -> summarize -> currentstatus -> dashboard

MAILTO=""
ANTHROPIC_API_KEY=Your_API_Key

# 1:30am — files new documents from the iCloud "Add To Corpus" watch folder
# into the correct Corpus subfolder (Books / Articles Journals Websites / Interviews / Misc)
30 1 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /Users/YourUserName/ProjectRoot/corpus-env/bin/python3 /Users/YourUserName/ProjectRoot/Scripts/autoadd.py --config /Users/YourUserName/ProjectRoot/Scripts/config.ini

# 2:00am — scans Corpus and Work for new/changed files, chunks and embeds
# them into ChromaDB, and records metadata in SQLite (corpus.db)
0 2 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /Users/YourUserName/ProjectRoot/corpus-env/bin/python3 /Users/YourUserName/ProjectRoot/Scripts/ingest.py --config /Users/YourUserName/ProjectRoot/Scripts/config.ini

# 3:00am — generates structured research abstracts for newly ingested documents;
# source documents route to the Anthropic API, author documents route to LM Studio
0 3 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /Users/YourUserName/ProjectRoot/corpus-env/bin/python3 /Users/YourUserName/ProjectRoot/Scripts/summarize.py --config /Users/YourUserName/ProjectRoot/Scripts/config.ini

# 3:30am — summarizes each file in Work/Manuscript via LM Studio and updates
# the Current Manuscript Status section of PROJECT_CONTEXT.md
30 3 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /Users/YourUserName/ProjectRoot/corpus-env/bin/python3 /Users/YourUserName/ProjectRoot/Scripts/currentstatus.py --config /Users/YourUserName/ProjectRoot/Scripts/config.ini

# 4:00am — rebuilds dashboard.html from corpus.db and state.db
0 4 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /Users/YourUserName/ProjectRoot/corpus-env/bin/python3 /Users/YourUserName/ProjectRoot/Scripts/dashboard.py --config /Users/YourUserName/ProjectRoot/Scripts/config.ini
```

**Edit crontab:** `crontab -e`
**View crontab:** `crontab -l`
**Backup crontab:** `crontab -l > Scripts/crontab.backup.txt`

Cron output is usually delivered to local Unix mail, but  `MAILTO=""` disables this. Delete that line to receive Unix mail about cron runs. Check with `mail` in terminal.

---

## Hermes Agent Configuration

### SOUL.md
**Location:** `~/.hermes/SOUL.md`

Defines Hermes as a rigorous research assistant with expertise in:
- History of computing and technology (1945–1990)
- Silicon Valley business culture and venture capital
- Cold War science policy and defense funding
- Counterculture movements and their intersection with technology
- Science and Technology Studies (STS) frameworks
- Journalistic ethics and verification standards

Key behavioral rules: no sycophancy, always cite sources, surface contradictions, present both supporting and complicating evidence, distinguish documented/inferred/speculative, offer to save findings to Work/Notes/.

### eoc-search skill
**Location:** `~/.hermes/skills/research/eoc-search/SKILL.md`

Project-specific skill that gives Hermes:
- All project paths
- Full SQLite schema and query examples
- ChromaDB semantic search command
- Search decision guide (SQLite vs ChromaDB vs combined)
- Standard research workflow (7-step process)
- Web search rules (corpus first, always ask before web search)
- Note saving conventions and filename format
- Citation format for corpus documents, author writing, and web sources
- Session startup checklist

**Activate in session:** `/skill Project Name-search`
**Auto-triggers on:** "Project Name", "the book", "the corpus", "key words"
**Reload after changes:** `/reload-skills`

### PROJECT_CONTEXT.md
**Location:** `/Users/User/Documents/Project Name/PROJECT_CONTEXT.md`

Human-readable project context read by Hermes at session start. Contains:
- Book overview and central argument
- Chapter structure (# of chapters)
- Key figures and their roles
- Research themes
- Project folder structure
- SQL query for author's current writing
- Research gaps (to be filled by author)
- Current Manuscript Status (auto-updated by currentstatus.py)

**Update manually:** Research Gaps section
**Auto-updated:** Current Manuscript Status section (nightly via currentstatus.py)

---

## Python Environment

**Location:** `corpus-env/` in project root
**Python version:** 3.10+ required (installed via Homebrew)
**Activation:**
```bash
source "/Users/User/Documents/Project Name/corpus-env/bin/activate"
```

**Installed packages:**
| Package | Purpose |
|---|---|
| chromadb | Vector store |
| sentence-transformers | Local embedding model |
| spacy + en_core_web_sm | Named entity recognition |
| anthropic | Anthropic API client |
| requests | LM Studio API calls |

---

## API Keys and Secrets

**Anthropic API key:**
- Set permanently in `~/.zshrc`: `export ANTHROPIC_API_KEY=`
- Also set in crontab at the top of the file for nightly runs
- Left blank in `config.ini` — script reads from environment variable

**LM Studio:**
- No API key required
- Runs locally at `http://localhost:1234/v1`
- Must be running with a model loaded before summarize.py processes Work/author documents

---

## Adding New Content

### Manually Adding new corpus files
1. Place `.txt` files in the appropriate subfolder under `Corpus/Text/`
2. Either wait for the 2am cron job or run manually:
```bash
python3 Scripts/ingest.py --config Scripts/config.ini
```
3. Abstracts will be generated at 3am or run manually:
```bash
python3 Scripts/summarize.py --config Scripts/config.ini
```
### Automated Adding new Corpus files
1. Place documents in `Add To Corpus` folder
2. That's it. The autoadd.py script handles the rest every night.


### Adding a new top-level corpus folder
1. Create the folder directly under `Corpus/` — e.g. `Corpus/Photographs/`
2. Run `Scripts/PathUpdate.py` (or add a line under `[corpus_categories]` in `config.ini` by hand) — this registers the new category with a blank description
3. Open `config.ini` and write a one-line description for the new category under `[corpus_categories]`, so `autoadd.py` can classify into it accurately rather than falling back to a generic guess based on the name alone
4. Run ingest

### Adding a new top-level Work folder
Same procedure as the above, but with `Work/` forlder.

### Moving the project to a new location
1. Move the entire `Project Name/` folder
2. Update all paths in `config.ini`
3. Run the SQLite path migration:
```bash
sqlite3 "/new/path/Index/corpus.db" "UPDATE documents SET file_path = REPLACE(file_path, '/old/path', '/new/path');"
```
4. Run repair to update metadata:
```bash
python3 Scripts/ingest.py --config Scripts/config.ini --repair
```
5. Run full ingest to rebuild ChromaDB with correct paths:
```bash
python3 Scripts/ingest.py --config Scripts/config.ini
```
6. Update crontab paths

---

## Known Issues and Notes

- **Cron and virtual environment:** Cron runs with a minimal PATH. Always include `PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin` prefix in cron job lines.
- **Anthropic API key in cron:** Must be set explicitly in crontab — `~/.zshrc` is not sourced by cron.
- **LM Studio must be running** for summarize.py and currentstatus.py to process Work/author documents.
- **ChromaDB path references:** If the project is moved, ChromaDB chunk metadata still contains old paths. A full re-ingest is required after moving (SQLite migration handles the metadata database).
- **spaCy NER accuracy:** Person name extraction is approximate. Some names will be missed; some non-names will be incorrectly extracted. The `names_mentioned` field is a useful starting point, not a definitive index.
- **Encoding fallback:** Text files that are not UTF-8 are read using Latin-1 as a fallback. Files requiring this fallback are noted in the ingest log.
- **Category descriptions live only in `config.ini`.** Unlike folder structure, which is self-evident from the filesystem, the classification guidance you write for each category exists nowhere else. Back up `config.ini` along with the rest of the project — losing it loses every hand-written category description, not just paths.

---

## Future Work (Planned)

- **query.py** — standalone corpus query script for Hermes to call programmatically to test if it is more efficient that Hermes using its own Research Skill.

---

## Quick Reference — Common Commands

```bash
# Activate environment
source "/Users/User/Documents/Project Name/corpus-env/bin/activate"

# Navigate to project
cd "/Users/User/Documents/Project Name"

# Run ingest manually
python3 Scripts/ingest.py --config Scripts/config.ini

# Run summarize on one folder
python3 Scripts/summarize.py --config Scripts/config.ini --folder Books

# Update manuscript status
python3 Scripts/currentstatus.py --config Scripts/config.ini

# Repair metadata
python3 Scripts/ingest.py --config Scripts/config.ini --repair

# Quick corpus stats
sqlite3 "Index/corpus.db" "SELECT source_type, document_role, COUNT(*) FROM documents WHERE status='ingested' GROUP BY source_type, document_role;"

# Check recent ingest log
tail -50 Index/ingest.log
```
