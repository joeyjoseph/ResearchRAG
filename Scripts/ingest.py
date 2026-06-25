"""
ingest.py — Corpus Ingest Script
Reads new or modified files from the Corpus and Work folders, chunks and
embeds them into ChromaDB, and records metadata in SQLite.

Project structure assumed:
    Project Root/
        Corpus/
            Text/
                Books/
                Interviews/
                Articles Journals Websites/
                Misc/
            Visuals/   (ignored)
        Work/
            Proposal/
            Drafts/
            Manuscript/
            Research Memos/
            Notes/

document_role:
    source  — files under Corpus/ (research material)
    author  — files under Work/ (the author's own writing)

source_type is derived from the top-level category folder
(e.g. Books, Interviews, Drafts, Research Memos).
subfolder is the immediate parent folder when files are nested deeper.

Usage:
    python ingest.py --config config.ini
    python ingest.py --config config.ini --repair
    python ingest.py --config config.ini --prune

    --repair: updates source_type, subfolder, and document_role for all
              existing records without re-embedding.
    --prune:  removes SQLite rows and ChromaDB chunks for any indexed file
              that no longer exists on disk (e.g. moved or deleted by hand).

Scheduled usage (cron example, runs nightly at 2am):
    0 2 * * * /path/to/corpus-env/bin/python3 /path/to/Scripts/ingest.py --config /path/to/Scripts/config.ini
"""

import os
import re
import sys
import json
import sqlite3
import logging
import argparse
import traceback
import configparser
from datetime import datetime
from pathlib import Path

import chromadb
import spacy
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Ingest corpus and work files into SQLite + ChromaDB.")
parser.add_argument("--config", required=True, help="Path to config.ini")
mode_group = parser.add_mutually_exclusive_group()
mode_group.add_argument("--repair", action="store_true",
                        help="Refresh source_type, subfolder, and document_role without re-embedding")
mode_group.add_argument("--prune", action="store_true",
                        help="Remove SQLite rows and ChromaDB chunks for indexed files no longer on disk")
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

config = configparser.ConfigParser()
config.read(args.config)

CORPUS_ROOT = Path(config["paths"]["CORPUS_ROOT"])
WORK_ROOT   = Path(config["paths"]["WORK_ROOT"])
DB_PATH     = Path(config["paths"]["DB_PATH"])
CHROMA_PATH = Path(config["paths"]["CHROMA_PATH"])
LOG_PATH    = Path(config["paths"]["LOG_PATH"])

CHUNK_SIZE             = int(config["chunking"]["CHUNK_SIZE"])
CHUNK_OVERLAP          = int(config["chunking"]["CHUNK_OVERLAP"])
MIN_WORDS_SKIP         = int(config["chunking"]["MIN_WORDS_SKIP"])
MIN_WORDS_SINGLE_CHUNK = int(config["chunking"]["MIN_WORDS_SINGLE_CHUNK"])

EMBEDDING_MODEL = config["embedding"]["EMBEDDING_MODEL"]
COLLECTION_NAME = config["chromadb"]["COLLECTION_NAME"]

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
# Source type and document role resolution
# ---------------------------------------------------------------------------

# Top-level category folders under Corpus/Text/
CORPUS_CATEGORY_FOLDERS = {
    "Books", "Interviews", "Articles Journals Websites", "Misc"
}

# Top-level category folders under Work/
WORK_CATEGORY_FOLDERS = {
    "Proposal", "Drafts", "Manuscript", "Research Memos", "Notes"
}


def resolve_metadata(file_path: Path) -> tuple[str, str, str]:
    """
    Determine source_type, subfolder, and document_role for a file.

    Returns (source_type, subfolder, document_role)
    - document_role: 'source' for Corpus files, 'author' for Work files
    - source_type: top-level category folder name
    - subfolder: immediate parent folder when nested deeper, else empty string
    """
    # Determine whether file is under Corpus or Work
    try:
        file_path.relative_to(WORK_ROOT)
        document_role = "author"
        root = WORK_ROOT
        category_folders = WORK_CATEGORY_FOLDERS
    except ValueError:
        document_role = "source"
        root = CORPUS_ROOT
        category_folders = CORPUS_CATEGORY_FOLDERS

    # Get path parts relative to root
    try:
        rel_parts = file_path.parts[len(root.parts):]
    except Exception:
        return file_path.parent.name, "", document_role

    # Find the top-level category folder
    source_type = ""
    for part in rel_parts:
        if part in category_folders:
            source_type = part
            break

    if not source_type:
        source_type = file_path.parent.name

    # subfolder is immediate parent when it differs from source_type
    immediate_parent = file_path.parent.name
    subfolder = immediate_parent if immediate_parent != source_type else ""

    return source_type, subfolder, document_role


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------

def get_db(path: Path) -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            file_path        TEXT PRIMARY KEY,
            filename         TEXT,
            source_type      TEXT,
            subfolder        TEXT,
            document_role    TEXT,
            word_count       INTEGER,
            last_modified    REAL,
            ingest_date      TEXT,
            chunk_count      INTEGER,
            abstract         TEXT,
            notes            TEXT,
            status           TEXT,
            year_published   TEXT,
            author           TEXT,
            names_mentioned  TEXT
        )
    """)
    # Migrate older schemas that are missing newer columns
    existing = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    for col, definition in [
        ("subfolder",     "TEXT DEFAULT ''"),
        ("document_role", "TEXT DEFAULT 'source'"),
        ("abstract",      "TEXT DEFAULT ''"),
        ("notes",         "TEXT DEFAULT ''"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {definition}")
            log.info(f"Added '{col}' column to documents table.")
    conn.commit()
    return conn

# ---------------------------------------------------------------------------
# Repair mode — metadata refresh only, no re-embedding
# ---------------------------------------------------------------------------

def repair_metadata(conn: sqlite3.Connection):
    """
    Walk both Corpus and Work roots. Update source_type, subfolder, and
    document_role for every existing record without re-embedding.
    """
    log.info("REPAIR MODE — refreshing metadata for all records.")
    all_files = sorted(CORPUS_ROOT.rglob("*.txt")) + sorted(WORK_ROOT.rglob("*.txt"))
    updated = 0
    missing = 0

    for file_path in all_files:
        rel_path = str(file_path)
        source_type, subfolder, document_role = resolve_metadata(file_path)

        result = conn.execute(
            "UPDATE documents SET source_type=?, subfolder=?, document_role=? WHERE file_path=?",
            (source_type, subfolder, document_role, rel_path)
        )
        if result.rowcount > 0:
            updated += 1
            log.info(f"  Updated: {file_path.name} → "
                     f"source_type='{source_type}', subfolder='{subfolder}', role='{document_role}'")
        else:
            missing += 1
            log.warning(f"  Not in DB (not yet ingested): {file_path.name}")

    conn.commit()
    log.info(f"Repair complete. Updated: {updated}, Not in DB: {missing}")

# ---------------------------------------------------------------------------
# Prune mode — remove records for files no longer on disk
# ---------------------------------------------------------------------------

def prune_missing(conn: sqlite3.Connection, collection):
    """
    Check every indexed file_path against the filesystem. For any record
    whose file no longer exists (moved, renamed outside the tool, or
    deleted by hand), remove its SQLite row and any matching ChromaDB
    chunks. Does not touch records whose files are still present.
    """
    log.info("PRUNE MODE — checking for indexed files that no longer exist on disk.")
    rows = conn.execute("SELECT file_path FROM documents").fetchall()

    checked = 0
    removed = 0
    chunks_removed = 0

    for row in rows:
        checked += 1
        file_path = row["file_path"]

        if Path(file_path).exists():
            continue

        log.info(f"  Missing on disk, removing: {file_path}")

        try:
            existing_ids = collection.get(where={"file_path": file_path})["ids"]
            if existing_ids:
                collection.delete(ids=existing_ids)
                chunks_removed += len(existing_ids)
                log.info(f"    Deleted {len(existing_ids)} ChromaDB chunk(s).")
        except Exception as e:
            log.warning(f"    Could not delete ChromaDB chunks for {file_path}: {e}")

        conn.execute("DELETE FROM documents WHERE file_path = ?", (file_path,))
        conn.commit()
        removed += 1

    log.info(f"Prune complete. Checked: {checked}, Removed: {removed}, "
             f"ChromaDB chunks deleted: {chunks_removed}")

# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def read_text(file_path: Path) -> tuple[str, str]:
    for enc in ["utf-8", "latin-1"]:
        try:
            return file_path.read_text(encoding=enc), enc
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="ignore"), "utf-8-ignore"

# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

YEAR_RE = re.compile(r'\b(1[5-9]\d{2}|20[0-2]\d)\b')

def extract_year(text: str, filename: str) -> str:
    for source in [filename, " ".join(text.split()[:500])]:
        match = YEAR_RE.search(source)
        if match:
            return match.group(1)
    return ""

AUTHOR_PATTERNS = [
    re.compile(r'(?i)^by\s+([A-Z][a-z]+(?: [A-Z][a-z]+)+)'),
    re.compile(r'(?i)author[:\s]+([A-Z][a-z]+(?: [A-Z][a-z]+)+)'),
    re.compile(r'(?i)written by\s+([A-Z][a-z]+(?: [A-Z][a-z]+)+)'),
]

def extract_author(text: str, filename: str) -> str:
    header = " ".join(text.split()[:200])
    for source in [filename, header]:
        for pat in AUTHOR_PATTERNS:
            match = pat.search(source)
            if match:
                return match.group(1).strip()
    return ""

def extract_names(text: str, nlp) -> str:
    sample = " ".join(text.split()[:5000])
    doc = nlp(sample)
    names = list(dict.fromkeys(
        ent.text.strip()
        for ent in doc.ents
        if ent.label_ == "PERSON" and len(ent.text.strip()) > 2
    ))
    return json.dumps(names) if names else "[]"

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += chunk_size - overlap
    return chunks

# ---------------------------------------------------------------------------
# Main ingest logic
# ---------------------------------------------------------------------------

def ingest_file(
    file_path: Path,
    conn: sqlite3.Connection,
    collection,
    nlp,
    embedding_model,
    run_stats: dict,
):
    rel_path      = str(file_path)
    filename      = file_path.name
    source_type, subfolder, document_role = resolve_metadata(file_path)
    last_modified = file_path.stat().st_mtime

    # Check if already ingested and unchanged
    row = conn.execute(
        "SELECT last_modified, status FROM documents WHERE file_path = ?",
        (rel_path,)
    ).fetchone()

    if row and row["last_modified"] == last_modified and row["status"] == "ingested":
        run_stats["skipped"] += 1
        return

    is_update = row is not None
    log.info(f"{'Updating' if is_update else 'Ingesting'}: {filename} "
             f"[{document_role} / {source_type}"
             f"{' / ' + subfolder if subfolder else ''}]")

    try:
        text, encoding_used = read_text(file_path)
        if encoding_used != "utf-8":
            log.warning(f"  Encoding fallback: used {encoding_used} for {filename}")

        word_count = len(text.split())

        if word_count < MIN_WORDS_SKIP:
            log.info(f"  Skipped (too short: {word_count} words): {filename}")
            conn.execute("""
                INSERT OR REPLACE INTO documents
                (file_path, filename, source_type, subfolder, document_role,
                 word_count, last_modified, ingest_date, chunk_count,
                 abstract, notes, status, year_published, author, names_mentioned)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (rel_path, filename, source_type, subfolder, document_role,
                  word_count, last_modified, datetime.now().isoformat(),
                  0, "", "", "skipped", "", "", "[]"))
            conn.commit()
            run_stats["skipped"] += 1
            return

        year_published  = extract_year(text, filename)
        author          = extract_author(text, filename)
        names_mentioned = extract_names(text, nlp)

        chunks = [text] if word_count <= MIN_WORDS_SINGLE_CHUNK \
                        else chunk_text(text, CHUNK_SIZE, CHUNK_OVERLAP)

        if is_update:
            existing_ids = collection.get(where={"file_path": rel_path})["ids"]
            if existing_ids:
                collection.delete(ids=existing_ids)

        chunk_ids = [f"{rel_path}__chunk_{i:04d}" for i in range(len(chunks))]
        metadatas = [
            {
                "file_path":      rel_path,
                "source_type":    source_type,
                "subfolder":      subfolder,
                "document_role":  document_role,
                "author":         author,
                "year_published": year_published,
                "chunk_number":   i,
            }
            for i in range(len(chunks))
        ]
        embeddings = embedding_model.encode(chunks, show_progress_bar=False).tolist()

        collection.add(
            ids=chunk_ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        conn.execute("""
            INSERT OR REPLACE INTO documents
            (file_path, filename, source_type, subfolder, document_role,
             word_count, last_modified, ingest_date, chunk_count,
             abstract, notes, status, year_published, author, names_mentioned)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rel_path, filename, source_type, subfolder, document_role,
              word_count, last_modified, datetime.now().isoformat(),
              len(chunks), "", "", "ingested",
              year_published, author, names_mentioned))
        conn.commit()

        run_stats["ingested" if not is_update else "updated"] += 1
        log.info(f"  OK — {word_count} words, {len(chunks)} chunks, "
                 f"author='{author}', year='{year_published}'")

    except Exception as e:
        log.error(f"  FAILED: {filename} — {e}")
        log.debug(traceback.format_exc())
        conn.execute("""
            INSERT OR REPLACE INTO documents
            (file_path, filename, source_type, subfolder, document_role,
             word_count, last_modified, ingest_date, chunk_count,
             abstract, notes, status, year_published, author, names_mentioned)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (rel_path, filename, source_type, subfolder, document_role,
              0, last_modified, datetime.now().isoformat(),
              0, "", "", "failed", "", "", "[]"))
        conn.commit()
        run_stats["failed"] += 1

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 70)
    if args.repair:
        log.info(f"INGEST REPAIR STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    elif args.prune:
        log.info(f"INGEST PRUNE STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        log.info(f"INGEST RUN STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Corpus root : {CORPUS_ROOT}")
    log.info(f"Work root   : {WORK_ROOT}")
    log.info(f"DB path     : {DB_PATH}")
    log.info(f"Chroma path : {CHROMA_PATH}")
    log.info("=" * 70)

    run_stats = {"ingested": 0, "updated": 0, "skipped": 0, "failed": 0, "total": 0}

    try:
        conn = get_db(DB_PATH)

        if args.repair:
            repair_metadata(conn)

        elif args.prune:
            chroma     = chromadb.PersistentClient(path=str(CHROMA_PATH))
            collection = chroma.get_or_create_collection(COLLECTION_NAME)
            prune_missing(conn, collection)

        else:
            chroma     = chromadb.PersistentClient(path=str(CHROMA_PATH))
            collection = chroma.get_or_create_collection(COLLECTION_NAME)
            nlp        = spacy.load("en_core_web_sm")
            embedder   = SentenceTransformer(EMBEDDING_MODEL)

            # Scan both Corpus and Work roots
            txt_files = sorted(CORPUS_ROOT.rglob("*.txt")) + sorted(WORK_ROOT.rglob("*.txt"))
            run_stats["total"] = len(txt_files)
            log.info(f"Found {len(txt_files)} .txt files total "
                     f"({len(list(CORPUS_ROOT.rglob('*.txt')))} corpus, "
                     f"{len(list(WORK_ROOT.rglob('*.txt')))} work).")

            for file_path in txt_files:
                ingest_file(file_path, conn, collection, nlp, embedder, run_stats)

    except Exception as e:
        log.critical(f"INGEST ABORTED — unhandled error: {e}")
        log.critical(traceback.format_exc())
        sys.exit(1)

    finally:
        if not args.repair and not args.prune:
            log.info("-" * 70)
            log.info("INGEST RUN COMPLETE")
            log.info(f"  Total files found : {run_stats['total']}")
            log.info(f"  Ingested (new)    : {run_stats['ingested']}")
            log.info(f"  Updated (changed) : {run_stats['updated']}")
            log.info(f"  Skipped           : {run_stats['skipped']}")
            log.info(f"  Failed            : {run_stats['failed']}")
            log.info("=" * 70)

if __name__ == "__main__":
    main()
