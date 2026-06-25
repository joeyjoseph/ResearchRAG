"""
currentstatus.py — Manuscript Status Updater
Scans the Work/Manuscript folder, generates a 1-2 sentence summary for each
file via LM Studio, and updates the ## Current Manuscript Status section in
PROJECT_CONTEXT.md.

Only processes new or changed files. Unchanged files keep their existing
summaries. Replaces the entire status section on each run to ensure accuracy.

Usage:
    python currentstatus.py --config config.ini

Scheduled usage (cron example, runs nightly at 3:30am):
    30 3 * * * /path/to/corpus-env/bin/python3 /path/to/Scripts/currentstatus.py --config /path/to/Scripts/config.ini
"""

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

parser = argparse.ArgumentParser(description="Update manuscript status in PROJECT_CONTEXT.md.")
parser.add_argument("--config", required=True, help="Path to config.ini")
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

config = configparser.ConfigParser()
config.read(args.config)

WORK_ROOT          = Path(config["paths"]["WORK_ROOT"])
PROJECT_ROOT       = Path(config["paths"]["PROJECT_ROOT"])
LOG_PATH           = Path(config["paths"]["LOG_PATH"].replace("ingest.log", "currentstatus.log"))
LMSTUDIO_BASE_URL  = config["summarize"].get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1").strip()
MAX_RETRIES        = int(config["summarize"].get("MAX_RETRIES", "5"))
MIN_WORDS_SKIP     = int(config["chunking"]["MIN_WORDS_SKIP"])

MANUSCRIPT_ROOT    = WORK_ROOT / "Manuscript"
CONTEXT_FILE       = PROJECT_ROOT / "PROJECT_CONTEXT.md"
STATUS_CACHE       = PROJECT_ROOT / ".manuscript_status_cache.json"

SECTION_HEADER     = "## Current Manuscript Status"
SECTION_FOOTER     = "---"  # the next section divider

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
# Cache helpers — track file paths, timestamps, and summaries
# ---------------------------------------------------------------------------

import json

def load_cache() -> dict:
    """Load existing summaries cache from disk."""
    try:
        return json.loads(STATUS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_cache(cache: dict):
    """Save summaries cache to disk."""
    STATUS_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

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
# LM Studio call
# ---------------------------------------------------------------------------

def call_lmstudio(prompt: str) -> str:
    """Call LM Studio via its OpenAI-compatible API."""
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
                    "model": "local-model",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 128,
                    "temperature": 0.2,
                    "stream": False
                },
                timeout=180
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()

        except Exception as e:
            wait = 2 ** attempt + random.uniform(0, 1)
            log.warning(f"  LM Studio error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                        f"Waiting {wait:.1f}s...")
            time.sleep(wait)

    raise RuntimeError(f"LM Studio failed after {MAX_RETRIES} attempts.")

# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def generate_summary(file_path: Path, text: str) -> str:
    """Generate a maximum two-sentence summary of a manuscript file."""
    excerpt = " ".join(text.split()[:2000])

    prompt = f"""You are summarizing a chapter or section of a book manuscript for a research index.

Write a summary of no more than two sentences. Be as brief as possible — one sentence is preferred if it captures the content. Focus on what the section is about and its main argument or narrative thrust. Do not comment on the writing quality. Do not use phrases like "This section" or "The author". Just state the content directly.

Manuscript excerpt:
---
{excerpt}
---

Two sentence maximum summary:"""

    return call_lmstudio(prompt)

# ---------------------------------------------------------------------------
# PROJECT_CONTEXT.md update
# ---------------------------------------------------------------------------

def build_status_section(entries: list[dict]) -> str:
    """
    Build the full ## Current Manuscript Status section as a string.
    entries is a list of dicts with keys: filename, rel_path, subfolder, summary
    sorted by subfolder then filename.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        SECTION_HEADER,
        f"*Last updated: {now} by currentstatus.py. Do not edit this section manually.*",
        "",
    ]

    if not entries:
        lines.append("*No manuscript files found.*")
        lines.append("")
        return "\n".join(lines)

    # Group by subfolder
    subfolders = {}
    for entry in entries:
        sf = entry["subfolder"] or "Manuscript (root)"
        subfolders.setdefault(sf, []).append(entry)

    for subfolder in sorted(subfolders.keys()):
        lines.append(f"### {subfolder}")
        lines.append("")
        for entry in sorted(subfolders[subfolder], key=lambda x: x["filename"]):
            lines.append(f"- **{entry['filename']}**")
            lines.append(f"  {entry['summary']}")
            lines.append("")

    return "\n".join(lines)


def update_context_file(new_section: str):
    """
    Replace the ## Current Manuscript Status section in PROJECT_CONTEXT.md.
    Preserves all other content in the file exactly.
    """
    if not CONTEXT_FILE.exists():
        log.error(f"PROJECT_CONTEXT.md not found at {CONTEXT_FILE}")
        return

    content = CONTEXT_FILE.read_text(encoding="utf-8")

    # Find the section start
    start_idx = content.find(SECTION_HEADER)
    if start_idx == -1:
        # Section doesn't exist — append it
        log.info("Section not found — appending to PROJECT_CONTEXT.md")
        content = content.rstrip() + "\n\n" + new_section + "\n"
        CONTEXT_FILE.write_text(content, encoding="utf-8")
        return

    # Find the end of the section — next `---` divider or end of file
    search_from = start_idx + len(SECTION_HEADER)
    end_idx = content.find("\n---", search_from)

    if end_idx == -1:
        # Section runs to end of file
        new_content = content[:start_idx] + new_section + "\n"
    else:
        new_content = content[:start_idx] + new_section + "\n" + content[end_idx + 1:]

    CONTEXT_FILE.write_text(new_content, encoding="utf-8")
    log.info(f"Updated {SECTION_HEADER} section in PROJECT_CONTEXT.md")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 70)
    log.info(f"CURRENTSTATUS RUN STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Manuscript root : {MANUSCRIPT_ROOT}")
    log.info(f"Context file    : {CONTEXT_FILE}")
    log.info("=" * 70)

    if not MANUSCRIPT_ROOT.exists():
        log.warning(f"Manuscript folder not found: {MANUSCRIPT_ROOT}")
        log.warning("Nothing to process.")
        return

    cache = load_cache()
    txt_files = sorted(MANUSCRIPT_ROOT.rglob("*.txt"))

    if not txt_files:
        log.info("No .txt files found in Manuscript folder.")
        update_context_file(build_status_section([]))
        return

    log.info(f"Found {len(txt_files)} .txt file(s) in Manuscript.")

    entries = []
    processed = skipped = failed = 0

    for file_path in txt_files:
        rel_path      = str(file_path)
        filename      = file_path.name
        last_modified = file_path.stat().st_mtime
        subfolder     = file_path.parent.name if file_path.parent != MANUSCRIPT_ROOT else ""

        # Check cache
        cached = cache.get(rel_path)
        if cached and cached.get("last_modified") == last_modified:
            log.info(f"  Unchanged, using cached summary: {filename}")
            entries.append({
                "filename":  filename,
                "rel_path":  rel_path,
                "subfolder": subfolder,
                "summary":   cached["summary"]
            })
            skipped += 1
            continue

        # Read and check word count
        try:
            text = read_text(file_path)
            word_count = len(text.split())

            if word_count < MIN_WORDS_SKIP:
                log.info(f"  Skipped (too short: {word_count} words): {filename}")
                skipped += 1
                continue

            log.info(f"  Generating summary: {filename} ({word_count} words)")
            summary = generate_summary(file_path, text)

            # Update cache
            cache[rel_path] = {
                "last_modified": last_modified,
                "summary":       summary
            }
            save_cache(cache)

            entries.append({
                "filename":  filename,
                "rel_path":  rel_path,
                "subfolder": subfolder,
                "summary":   summary
            })
            processed += 1
            log.info(f"  OK: {summary[:80]}...")

        except Exception as e:
            log.error(f"  FAILED: {filename} — {e}")
            log.debug(traceback.format_exc())
            failed += 1

    # Build and write the status section
    new_section = build_status_section(entries)
    update_context_file(new_section)

    log.info("-" * 70)
    log.info("CURRENTSTATUS RUN COMPLETE")
    log.info(f"  Files found     : {len(txt_files)}")
    log.info(f"  Newly summarized: {processed}")
    log.info(f"  Cached/skipped  : {skipped}")
    log.info(f"  Failed          : {failed}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
