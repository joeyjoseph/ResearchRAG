"""
autoadd.py — Corpus Auto-Add Script
Watches a folder (e.g. an iCloud Drive drop folder) for new source documents,
converts them to .txt, classifies each one into a Corpus subfolder using the
Anthropic API, and deletes the original on success.

Intended to run shortly before the nightly ingest job, so anything added here
is picked up automatically by ingest.py the same night.

Supported input types:
    .pdf   (text extraction via pypdf; falls back to OCR if extraction is thin)
    .docx  (python-docx)
    .html / .htm (BeautifulSoup, visible text only)
    .jpg / .jpeg / .png / .tif / .tiff (OCR via pytesseract)

Classification:
    The first ~1,500 words of extracted text are sent to the Anthropic API,
    which returns exactly one of: Books, Articles Journals Websites, Misc.
    This is corpus/source material, so it is routed through the same backend
    as source-document summarization in summarize.py — never LM Studio, which
    is reserved for the author's own unpublished writing.

On success:
    The .txt file is written to CORPUS_ROOT/Text/<category>/<filename>.txt
    and the original file is deleted from the watch folder.

On failure (extraction too thin, OCR failure, API error after retries,
unsupported file type):
    The original file is left untouched in the watch folder and the failure
    is logged clearly for manual follow-up. Nothing is ever deleted unless
    it was successfully converted and filed.

iCloud note:
    Files not yet downloaded from iCloud appear as zero-byte placeholders
    with a ".icloud" extension (e.g. ".My Book.pdf.icloud"). These are
    skipped and logged so they can be picked up on a later run once iCloud
    has finished syncing them locally.

Usage:
    python3 autoadd.py --config config.ini
    python3 autoadd.py --config config.ini --dry-run

Scheduled usage (cron example, runs nightly at 1:30am, before the 2am ingest):
    30 1 * * * /path/to/corpus-env/bin/python3 /path/to/Scripts/autoadd.py --config /path/to/Scripts/config.ini
"""

import os
import sys
import time
import random
import logging
import argparse
import traceback
import configparser
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(description="Auto-convert and file new corpus documents.")
parser.add_argument("--config", required=True, help="Path to config.ini")
parser.add_argument("--dry-run", action="store_true",
                    help="Classify and log what would happen, but don't write files or delete originals")
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

config = configparser.ConfigParser(inline_comment_prefixes=("#",))
config.read(args.config)

CORPUS_ROOT = Path(config["paths"]["CORPUS_ROOT"])
LOG_PATH    = Path(config["paths"]["LOG_PATH"].replace("ingest.log", "autoadd.log"))

WATCH_FOLDER        = Path(config["autoadd"]["ADD_TO_CORPUS_ROOT"]).expanduser()
MIN_WORDS_EXTRACTED = int(config["autoadd"].get("MIN_WORDS_EXTRACTED", "50"))

# Reuse the Anthropic credentials/model already configured for summarize.py
LLM_MODEL         = config["summarize"]["LLM_MODEL"].strip()
ANTHROPIC_API_KEY = config["summarize"].get("ANTHROPIC_API_KEY", "").strip() \
                    or os.environ.get("ANTHROPIC_API_KEY", "")
MAX_RETRIES       = int(config["summarize"].get("MAX_RETRIES", "5"))

# Target categories — must match the subset of CORPUS_CATEGORY_FOLDERS in
# ingest.py that this script knows how to sort into. Files land at the top
# level of Corpus/Text/Interviews/ — no attempt is made to guess which
# Interviews subfolder a document belongs in; sort those by hand if needed.
CATEGORIES = ["Books", "Articles Journals Websites", "Interviews", "Misc"]

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
# Text extraction — one function per supported input type
# ---------------------------------------------------------------------------

def extract_pdf_text(file_path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(file_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return "\n".join(pages).strip()


def ocr_pdf(file_path: Path) -> str:
    """Fallback for scanned/image PDFs — rasterize pages and run OCR."""
    from pdf2image import convert_from_path
    import pytesseract
    images = convert_from_path(str(file_path))
    pages = [pytesseract.image_to_string(img) for img in images]
    return "\n".join(pages).strip()


def extract_docx_text(file_path: Path) -> str:
    import docx
    doc = docx.Document(str(file_path))
    return "\n".join(p.text for p in doc.paragraphs).strip()


def extract_html_text(file_path: Path) -> str:
    from bs4 import BeautifulSoup
    raw = read_raw_text(file_path)
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines left behind by stripped tags
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_image_text(file_path: Path) -> str:
    from PIL import Image
    import pytesseract
    img = Image.open(str(file_path))
    return pytesseract.image_to_string(img).strip()


def read_raw_text(file_path: Path) -> str:
    for enc in ["utf-8", "latin-1"]:
        try:
            return file_path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="ignore")


EXTRACTORS = {
    ".pdf":  extract_pdf_text,
    ".docx": extract_docx_text,
    ".html": extract_html_text,
    ".htm":  extract_html_text,
    ".jpg":  extract_image_text,
    ".jpeg": extract_image_text,
    ".png":  extract_image_text,
    ".tif":  extract_image_text,
    ".tiff": extract_image_text,
}


def extract_text(file_path: Path) -> str:
    """
    Extract text using the appropriate method for the file's extension.
    PDFs that yield too little text are retried via OCR automatically.
    """
    suffix = file_path.suffix.lower()
    extractor = EXTRACTORS.get(suffix)
    if extractor is None:
        raise ValueError(f"Unsupported file type: {suffix}")

    text = extractor(file_path)

    if suffix == ".pdf" and len(text.split()) < MIN_WORDS_EXTRACTED:
        log.info(f"  Thin text extraction ({len(text.split())} words) — trying OCR fallback")
        ocr_text = ocr_pdf(file_path)
        if len(ocr_text.split()) > len(text.split()):
            text = ocr_text

    return text

# ---------------------------------------------------------------------------
# Classification via Anthropic API
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT_TEMPLATE = """You are sorting a newly added document into a research corpus.

Read the excerpt below and decide which single category it belongs to. Respond with exactly one of these four words/phrases and nothing else — no punctuation, no explanation:

Books
Articles Journals Websites
Interviews
Misc

Use "Books" for full-length books or book-length manuscripts. Use "Articles Journals Websites" for journal articles, magazine or newspaper articles, blog posts, or website content. Use "Interviews" for oral histories, interview transcripts, or Q&A-format documents — including anything that explicitly identifies itself as an "oral history" or "interview," even if it also contains biographical or narrative framing around the Q&A content. Use "Misc" for anything else, or anything you cannot confidently classify (reports, miscellaneous notes, unclear fragments, etc.).

Document excerpt:
---
{excerpt}
---

Category:"""


def call_anthropic_classify(excerpt: str) -> str:
    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed. Run: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = CLASSIFY_PROMPT_TEMPLATE.format(excerpt=excerpt)

    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=LLM_MODEL,
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = message.content[0].text.strip()
            return normalize_category(raw)

        except anthropic.RateLimitError as e:
            retry_after = getattr(e, "retry_after", None)
            wait = retry_after if retry_after else (2 ** attempt + random.uniform(0, 1))
            log.warning(f"  Rate limit hit (attempt {attempt + 1}/{MAX_RETRIES}). Waiting {wait:.1f}s...")
            time.sleep(wait)

        except anthropic.APIStatusError as e:
            if e.status_code == 529:
                wait = 2 ** attempt + random.uniform(0, 2)
                log.warning(f"  API overloaded (529, attempt {attempt + 1}/{MAX_RETRIES}). Waiting {wait:.1f}s...")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"Anthropic API failed after {MAX_RETRIES} attempts.")


def normalize_category(raw: str) -> str:
    """Match the model's response to an allowed category, defaulting to Misc."""
    cleaned = raw.strip().strip(".").strip()
    for category in CATEGORIES:
        if cleaned.lower() == category.lower():
            return category
    log.warning(f"  Unexpected classification response: '{raw}' — defaulting to Misc")
    return "Misc"

# ---------------------------------------------------------------------------
# Filing the converted text
# ---------------------------------------------------------------------------

def unique_target_path(target_dir: Path, stem: str) -> Path:
    """Avoid overwriting an existing file by appending (2), (3), etc."""
    candidate = target_dir / f"{stem}.txt"
    counter = 2
    while candidate.exists():
        candidate = target_dir / f"{stem} ({counter}).txt"
        counter += 1
    return candidate


def is_icloud_placeholder(file_path: Path) -> bool:
    """iCloud Drive represents not-yet-downloaded files as '.<name>.icloud'."""
    return file_path.suffix.lower() == ".icloud" or file_path.name.startswith(".") and file_path.name.endswith(".icloud")

# ---------------------------------------------------------------------------
# Main processing logic
# ---------------------------------------------------------------------------

def process_file(file_path: Path, run_stats: dict):
    filename = file_path.name
    log.info(f"Processing: {filename}")

    try:
        text = extract_text(file_path)
    except ValueError as e:
        log.warning(f"  SKIPPED — {e}")
        run_stats["skipped_unsupported"] += 1
        return
    except Exception as e:
        log.error(f"  FAILED — extraction error: {e}")
        log.debug(traceback.format_exc())
        run_stats["failed"] += 1
        return

    word_count = len(text.split())
    if word_count < MIN_WORDS_EXTRACTED:
        log.error(f"  FAILED — extraction too thin ({word_count} words). Original left in place for review.")
        run_stats["failed"] += 1
        return

    excerpt = " ".join(text.split()[:1500])

    try:
        category = call_anthropic_classify(excerpt)
    except Exception as e:
        log.error(f"  FAILED — classification error: {e}. Original left in place.")
        log.debug(traceback.format_exc())
        run_stats["failed"] += 1
        return

    target_dir = CORPUS_ROOT / "Text" / category
    target_path = unique_target_path(target_dir, file_path.stem)

    if args.dry_run:
        log.info(f"  [DRY RUN] Would classify as '{category}' and write to: {target_path}")
        log.info(f"  [DRY RUN] Would delete original: {file_path}")
        run_stats["would_process"] += 1
        return

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(text, encoding="utf-8")
        log.info(f"  Filed as '{category}' → {target_path.name} ({word_count} words)")
    except Exception as e:
        log.error(f"  FAILED — could not write text file: {e}. Original left in place.")
        log.debug(traceback.format_exc())
        run_stats["failed"] += 1
        return

    try:
        file_path.unlink()
        log.info(f"  Deleted original: {filename}")
        run_stats["processed"] += 1
    except Exception as e:
        log.error(f"  WARNING — text filed successfully, but failed to delete original: {e}")
        run_stats["processed"] += 1

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 70)
    log.info(f"AUTOADD RUN STARTED — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Watch folder : {WATCH_FOLDER}")
    log.info(f"Corpus root  : {CORPUS_ROOT}")
    log.info(f"Dry run      : {args.dry_run}")
    log.info("=" * 70)

    run_stats = {
        "total": 0, "processed": 0, "failed": 0,
        "skipped_unsupported": 0, "skipped_icloud": 0, "would_process": 0,
    }

    if not WATCH_FOLDER.exists():
        log.error(f"Watch folder not found: {WATCH_FOLDER}")
        sys.exit(1)

    try:
        candidates = sorted(p for p in WATCH_FOLDER.iterdir() if p.is_file())
    except Exception as e:
        log.critical(f"AUTOADD ABORTED — could not list watch folder: {e}")
        sys.exit(1)

    for file_path in candidates:
        if file_path.name.startswith("."):
            if is_icloud_placeholder(file_path):
                log.info(f"Skipping (not yet downloaded from iCloud): {file_path.name}")
                run_stats["skipped_icloud"] += 1
            else:
                log.info(f"Skipping hidden file: {file_path.name}")
            continue

        run_stats["total"] += 1
        process_file(file_path, run_stats)

    log.info("-" * 70)
    log.info("AUTOADD RUN COMPLETE")
    log.info(f"  Total files found      : {run_stats['total']}")
    if args.dry_run:
        log.info(f"  Would process          : {run_stats['would_process']}")
    else:
        log.info(f"  Processed (filed)      : {run_stats['processed']}")
    log.info(f"  Failed (left in place) : {run_stats['failed']}")
    log.info(f"  Skipped (unsupported)  : {run_stats['skipped_unsupported']}")
    log.info(f"  Skipped (iCloud stub)  : {run_stats['skipped_icloud']}")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
