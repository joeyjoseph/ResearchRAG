"""
summarize.py — Corpus Summarization Script
Generates structured research abstracts for documents in the corpus index,
writing results back to the SQLite database.

Backend routing is automatic based on document_role:
    source  (Corpus files) → LLM_BACKEND   (default: anthropic)
    author  (Work files)   → LOCAL_BACKEND (default: lmstudio)

This ensures the author's unpublished writing never leaves the local machine.

Usage:
    python summarize.py --config config.ini
    python summarize.py --config config.ini --folder Interviews
    python summarize.py --config config.ini --folder Books --regenerate
    python summarize.py --config config.ini --model claude-sonnet-4-6

Scheduled usage (cron example, runs nightly at 3am after ingest):
    0 3 * * * /path/to/corpus-env/bin/python3 /path/to/Scripts/summarize.py --config /path/to/Scripts/config.ini
"""

import os
import sys
import time
import random
import sqlite3
import logging
import argparse
import traceback
import configparser
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Generate abstracts for corpus documents.")
parser.add_argument("--config",     required=True,  help="Path to config.ini")
parser.add_argument("--folder",     default=None,   help="Only process this source_type (e.g. 'Interviews')")
parser.add_argument("--regenerate", action="store_true", help="Re-generate abstracts even if one already exists")
parser.add_argument("--model",      default=None,   help="Override Anthropic model at runtime")
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

config = configparser.ConfigParser()
config.read(args.config)

DB_PATH  = Path(config["paths"]["DB_PATH"])
LOG_PATH = Path(config["paths"]["LOG_PATH"].replace("ingest.log", "summarize.log"))

# Anthropic backend — for Corpus/source documents
LLM_BACKEND       = config["summarize"]["LLM_BACKEND"].strip().lower()
LLM_MODEL         = args.model or config["summarize"]["LLM_MODEL"].strip()
ANTHROPIC_API_KEY = config["summarize"].get("ANTHROPIC_API_KEY", "").strip() \
                    or os.environ.get("ANTHROPIC_API_KEY", "")

# Local backend — for Work/author documents
LOCAL_BACKEND     = config["summarize"].get("LOCAL_BACKEND", "lmstudio").strip().lower()
LMSTUDIO_BASE_URL = config["summarize"].get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").strip()

BATCH_SIZE  = int(config["summarize"]["BATCH_SIZE"])
DELAY       = float(config["summarize"]["DELAY_BETWEEN_REQUESTS"])
MAX_RETRIES = int(config["summarize"].get("MAX_RETRIES", "5"))

RESEARCH_THEMES = [t.strip() for t in config["summarize"].get("RESEARCH_THEMES", "").split(",") if t.strip()]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def get_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    for col, definition in [
        ("abstract",      "TEXT DEFAULT ''"),
        ("notes",         "TEXT DEFAULT ''"),
        ("document_role", "TEXT DEFAULT 'source'"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {definition}")
            log.info(f"Added '{col}' column to documents table.")
    conn.commit()
    return conn


def get_pending(conn: sqlite3.Connection, folder: str | None, regenerate: bool, limit: int) -> list:
    if regenerate:
        where = "status = 'ingested'"
    else:
        where = "status = 'ingested' AND (abstract IS NULL OR abstract = '')"

    if folder:
        where += f" AND source_type = '{folder}'"

    rows = conn.execute(
        f"SELECT file_path, filename, source_type, document_role, "
        f"word_count, author, year_published "
        f"FROM documents WHERE {where} LIMIT ?",
        (limit,)
    ).fetchall()
    return rows

# ---------------------------------------------------------------------------
# Text reading
# ---------------------------------------------------------------------------

def read_text(file_path: Path) -> str:
    for enc in ["utf-8", "latin-1"]:
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="ignore")

# ---------------------------------------------------------------------------
# Abstract prompt
# ---------------------------------------------------------------------------

THEMES_LINE = (
    f"Relevant research themes to watch for: {', '.join(RESEARCH_THEMES)}."
    if RESEARCH_THEMES else
    "Tag 3-5 keywords that best describe the document's research relevance."
)

def build_prompt(text: str, filename: str, source_type: str, word_count: int,
                 author: str, year_published: str, document_role: str) -> str:
    excerpt = " ".join(text.split()[:3000])

    # Adjust framing for author's own work vs. external source material
    if document_role == "author":
        role_context = (
            "This document was written by the author of the book. "
            "Treat it as the author's own developing work — drafts, notes, "
            "memos, or finished chapters — rather than external source material."
        )
    else:
        role_context = (
            "This document is external source material — a book, article, "
            "interview, or other research document."
        )

    return f"""You are a research assistant helping a journalist and author build a searchable corpus index.

Analyze the following document excerpt and produce a structured abstract. Be concise but specific — this abstract will be used by an AI agent to decide whether to open and read the full document when answering research questions.

{role_context}

Document metadata:
- Filename: {filename}
- Folder / Source type: {source_type}
- Document role: {document_role}
- Author (if known): {author or 'Unknown'}
- Year (if known): {year_published or 'Unknown'}
- Word count: {word_count}

Document excerpt (first 3,000 words):
---
{excerpt}
---

Produce the abstract in exactly this format, with each field on its own line. Do not add extra commentary before or after.

DOCUMENT_TYPE: [e.g. book, research paper, interview transcript, draft chapter, research memo, outline, notes, etc.]
TOPIC: [One sentence: what is this document fundamentally about?]
ARGUMENT_OR_THESIS: [One to two sentences: what is the main argument, claim, or finding? If a draft or notes, summarize the core content or direction.]
STANCE: [One of: Critical / Supportive / Neutral / Mixed — followed by one sentence explaining the stance toward its subject]
TIME_PERIOD: [The historical or contemporary period the document addresses]
KEY_PEOPLE: [Comma-separated list of principal figures discussed, quoted, or interviewed]
EVIDENCE_TYPE: [e.g. firsthand testimony, statistical data, declassified documents, secondary sources, author's analysis, draft narrative, field notes, etc.]
CONTRADICTIONS: [Any internal tensions or contradictions with known accounts. Write 'None apparent' if none.]
QUOTABLE_MATERIAL: [Yes or No — does the document contain direct quotes, testimony, or directly citable passages?]
RELEVANCE_TAGS: [{THEMES_LINE}]
SOURCE_RELIABILITY: [Brief note on credibility and provenance — who wrote it, when, for what purpose, and any caveats about bias or reliability]
CORPUS_CONNECTIONS: [People, events, claims, or themes likely to appear in other corpus documents. Write 'Unknown' if unclear.]"""

# ---------------------------------------------------------------------------
# LLM backends
# ---------------------------------------------------------------------------

def call_anthropic(prompt: str) -> str:
    """Call the Anthropic Messages API with exponential backoff on rate limits."""
    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=LLM_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )
            return message.content[0].text

        except anthropic.RateLimitError as e:
            retry_after = getattr(e, "retry_after", None)
            wait = retry_after if retry_after else (2 ** attempt + random.uniform(0, 1))
            log.warning(f"  Rate limit hit (attempt {attempt + 1}/{MAX_RETRIES}). "
                        f"Waiting {wait:.1f}s before retry...")
            time.sleep(wait)

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 2 ** attempt + random.uniform(0, 2)
                log.warning(f"  API overloaded (529, attempt {attempt + 1}/{MAX_RETRIES}). "
                            f"Waiting {wait:.1f}s...")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"Anthropic API failed after {MAX_RETRIES} attempts.")


def call_lmstudio(prompt: str) -> str:
    """
    Call a local LM Studio instance via its OpenAI-compatible API.
    Uses whatever model is currently loaded in LM Studio.
    """
    try:
        import requests
    except ImportError:
        log.error("requests package not installed. Run: pip install requests")
        sys.exit(1)

    url = f"{LMSTUDIO_BASE_URL}/chat/completions"

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                url,
                json={
                    "model": "local-model",  # LM Studio ignores this, uses loaded model
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                    "temperature": 0.3,
                    "stream": False
                },
                timeout=180
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

        except Exception as e:
            wait = 2 ** attempt + random.uniform(0, 1)
            log.warning(f"  LM Studio error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                        f"Waiting {wait:.1f}s...")
            time.sleep(wait)

    raise RuntimeError(f"LM Studio failed after {MAX_RETRIES} attempts. "
                       "Ensure LM Studio is running and a model is loaded.")


def get_completion(prompt: str, document_role: str) -> str:
    """
    Route to the appropriate backend based on document_role.
    - source (Corpus files) → LLM_BACKEND (anthropic by default)
    - author (Work files)   → LOCAL_BACKEND (lmstudio by default)
    """
    if document_role == "author":
        backend = LOCAL_BACKEND
        log.info(f"  Routing to local backend ({backend}) — author document")
    else:
        backend = LLM_BACKEND
        log.info(f"  Routing to remote backend ({backend}) — source document")

    if backend in ("anthropic", "hermes"):
        return call_anthropic(prompt)
    elif backend == "lmstudio":
        return call_lmstudio(prompt)
    else:
        raise ValueError(f"Unknown backend: '{backend}'. Valid options: anthropic, hermes, lmstudio")

# ---------------------------------------------------------------------------
# Main summarize logic
# ---------------------------------------------------------------------------

def summarize_file(row: sqlite3.Row, conn: sqlite3.Connection, run_stats: dict):
    file_path     = Path(row["file_path"])
    filename      = row["filename"]
    source_type   = row["source_type"]
    document_role = row["document_role"] or "source"
    word_count    = row["word_count"]
    author        = row["author"] or ""
    year          = row["year_published"] or ""

    log.info(f"Summarizing: {filename} ({source_type}, {document_role}, {word_count} words)")

    try:
        text     = read_text(file_path)
        prompt   = build_prompt(text, filename, source_type, word_count,
                                author, year, document_role)
        abstract = get_completion(prompt, document_role)

        conn.execute(
            "UPDATE documents SET abstract = ? WHERE file_path = ?",
            (abstract.strip(), str(file_path))
        )
        conn.commit()

        run_stats["summarized"] += 1
        log.info(f"  OK — abstract written ({len(abstract)} chars)")

    except FileNotFoundError:
        log.error(f"  FAILED — file not found: {file_path}")
        run_stats["failed"] += 1

    except Exception as e:
        log.error(f"  FAILED — {filename}: {e}")
        log.debug(traceback.format_exc())
        run_stats["failed"] += 1

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 70)
    log.info(f"SUMMARIZE RUN STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Source backend  : {LLM_BACKEND} / {LLM_MODEL}")
    log.info(f"Author backend  : {LOCAL_BACKEND} (LM Studio — loaded model)")
    log.info(f"Folder          : {args.folder or 'all'}")
    log.info(f"Regenerate      : {args.regenerate}")
    log.info(f"Batch size      : {BATCH_SIZE}")
    log.info(f"Delay           : {DELAY}s between requests")
    log.info("=" * 70)

    run_stats = {"summarized": 0, "failed": 0, "total": 0}

    try:
        conn = get_db(DB_PATH)
        rows = get_pending(conn, args.folder, args.regenerate, BATCH_SIZE)
        run_stats["total"] = len(rows)

        if not rows:
            log.info("No documents pending summarization.")
        else:
            log.info(f"Found {len(rows)} document(s) to summarize.")

            for i, row in enumerate(rows):
                summarize_file(row, conn, run_stats)

                # Only delay between requests — skip after last document
                if i < len(rows) - 1:
                    # Use shorter delay for local backend
                    doc_role = row["document_role"] or "source"
                    delay = 1.0 if doc_role == "author" else DELAY
                    time.sleep(delay)

    except Exception as e:
        log.critical(f"SUMMARIZE ABORTED — unhandled error: {e}")
        log.critical(traceback.format_exc())
        sys.exit(1)

    finally:
        log.info("-" * 70)
        log.info("SUMMARIZE RUN COMPLETE")
        log.info(f"  Total processed : {run_stats['total']}")
        log.info(f"  Summarized      : {run_stats['summarized']}")
        log.info(f"  Failed          : {run_stats['failed']}")
        log.info("=" * 70)


if __name__ == "__main__":
    main()
