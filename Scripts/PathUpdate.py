#!/usr/bin/env python3
"""
PathUpdate.py — Research RAG One-Time Setup Script
Run this once, after your Corpus/ and Work/ folders (and their top-level
subfolders) already exist, to replace every placeholder path in the repo
with your real paths — and to register any category folders found on disk
that aren't already listed in config.ini.

Any folder you create directly under Corpus/ (e.g. "Photographs",
"Opinion Pieces") is treated as its own category automatically. Categories
themselves — and, for Corpus, the classification guidance text used by
autoadd.py — live in config.ini's [corpus_categories] and [work_categories]
sections. ingest.py and autoadd.py read those sections directly at runtime;
neither file is ever edited by this script.

What it edits:
    Scripts/config.ini
        - CORPUS_ROOT, WORK_ROOT, PROJECT_ROOT, DB_PATH, CHROMA_PATH,
          LOG_PATH, STATE_DB_PATH, ADD_TO_CORPUS_ROOT
        - [corpus_categories] / [work_categories] — ADDITIVE MERGE ONLY.
          Any folder found on disk that isn't already a key gets added with
          a blank description. Any key that already exists is left
          completely untouched, descriptions included — this is the part
          that matters most: it means it's always safe to re-run this
          script later (after moving the project, adding a folder, etc.)
          without losing any guidance text you've already written. A key
          that no longer has a matching folder on disk is never removed
          automatically — you'll just get a printed warning so you can
          decide whether to clean it up by hand.

    Scripts/Manual Runs and Crontab/Crontab
        - every /Users/YourUserName/ProjectRoot/... path

    Scripts/Manual Runs and Crontab/*.txt
        - cd / source lines in each manual-run helper file (handles the
          "ProjecRoot" typo and "Project Root" with-a-space variants too)

What it never touches:
    ANTHROPIC_API_KEY — in config.ini this is left exactly as found
    (blank, or whatever placeholder is already there). The key should
    only ever live in your shell profile and crontab, never in a file
    inside the project folder.

    Existing category descriptions — see the additive-merge note above.

A .bak copy of config.ini (and each crontab/manual-run file) is saved the
first time it's edited, so you can always diff or revert.

Usage:
    python3 Scripts/PathUpdate.py --project-root "/Users/jane/Documents/My Book" \\
        --add-to-corpus "/Users/jane/Library/Mobile Documents/com~apple~CloudDocs/Add To Corpus"

    # Preview changes without writing anything:
    python3 Scripts/PathUpdate.py --project-root "..." --add-to-corpus "..." --dry-run

Optional flags:
    --corpus-root   defaults to <project-root>/Corpus
    --work-root     defaults to <project-root>/Work
    --state-db      defaults to ~/.hermes/state.db
    --yes           skip the confirmation prompt
"""

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Placeholder variants found across the repo — every one of these gets
# replaced with the real project root, wherever they appear.
# ---------------------------------------------------------------------------

PROJECT_ROOT_PLACEHOLDERS = [
    "/Users/YourUserName/ProjectRoot",
    "/Users/YourUserName/ProjecRoot",   # typo variant present in two files
    "/Users/YourUserName/Project Root", # space variant present in one file
]

# Files that get a blanket find/replace of the placeholders above.
PLAIN_REPLACE_FILES = [
    "Scripts/Manual Runs and Crontab/Crontab",
    "Scripts/Manual Runs and Crontab/Manually Ingest.txt",
    "Scripts/Manual Runs and Crontab/Manually Run AutoAdd.txt",
    "Scripts/Manual Runs and Crontab/Manually Run Prune.txt",
    "Scripts/Manual Runs and Crontab/Update Dashboard.txt",
    "Scripts/Manual Runs and Crontab/Activate Environment.txt",
    "Scripts/Manual Runs and Crontab/Start Stop Dashboard Server.txt",
]

CONFIG_FILE = "Scripts/config.ini"

# Folder names to ignore when auto-detecting categories — just real
# junk/system directories. Every other folder under Corpus/ or Work/ is a
# real category, including ones that happen to be named "Text" or "Visuals"
# if you choose to use those names yourself.
IGNORE_DIR_NAMES = {".git", ".DS_Store", "__pycache__"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def backup_once(path: Path):
    """Save a .bak copy the first time a file is touched, never overwrite it."""
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def detect_category_folders(root: Path) -> list[str]:
    """Return immediate subfolder names under root, sorted, ignoring junk dirs."""
    if not root.exists():
        return []
    return sorted(
        p.name for p in root.iterdir()
        if p.is_dir() and p.name not in IGNORE_DIR_NAMES and not p.name.startswith(".")
    )


def replace_placeholders(text: str, real_root: str) -> tuple[str, int]:
    count = 0
    for placeholder in PROJECT_ROOT_PLACEHOLDERS:
        n = text.count(placeholder)
        if n:
            text = text.replace(placeholder, real_root)
            count += n
    return text, count


def set_config_value(text: str, key: str, value: str) -> tuple[str, bool]:
    """
    Replace the value of `key = ...` on its own line in an .ini-style file,
    preserving everything else (comments, spacing of other lines) as-is.
    Only touches the first match. Returns (new_text, found).
    """
    pattern = re.compile(rf"(?m)^({re.escape(key)}\s*=\s*)(.*)$")
    match = pattern.search(text)
    if not match:
        return text, False
    new_line = f"{match.group(1)}{value}"
    new_text = text[:match.start()] + new_line + text[match.end():]
    return new_text, True


def find_section_body(text: str, section_name: str):
    """
    Locate `[section_name]` in an .ini-style text. Returns
    (header_match, body_start, body_end) where body spans from just after
    the header line to just before the next `[section]` header or EOF.
    Returns (None, None, None) if the section header isn't found.
    """
    header_pattern = re.compile(rf"^\[{re.escape(section_name)}\]\s*$", re.MULTILINE)
    header_match = header_pattern.search(text)
    if not header_match:
        return None, None, None

    body_start = header_match.end()
    next_header = re.search(r"^\[[^\]]+\]\s*$", text[body_start:], re.MULTILINE)
    body_end = body_start + next_header.start() if next_header else len(text)
    return header_match, body_start, body_end


def existing_keys_in_section(body: str) -> list[str]:
    """Parse plain `key = value` lines out of a section body, ignoring comments/blanks."""
    keys = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if "=" in stripped:
            keys.append(stripped.split("=", 1)[0].strip())
    return keys


def last_entry_end_offset(body: str) -> int:
    """
    Return the character offset (within `body`) just after the last real
    `key = value` line — skipping any trailing comments/blank lines that
    follow it (those usually belong to whatever section comes next, not
    this one). Returns 0 if the section has no entries yet (insert right
    after the header in that case).
    """
    offset = 0
    last_entry_end = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        offset += len(line)
        if stripped and not stripped.startswith("#") and not stripped.startswith(";") and "=" in stripped:
            last_entry_end = offset
    return last_entry_end


def merge_category_section(text: str, section_name: str, detected_folders: list[str]) -> tuple[str, list[str], list[str]]:
    """
    Additive merge of `detected_folders` into `[section_name]` in an
    .ini-style text:
      - a detected folder with no existing key gets added with a blank
        description ("name = ")
      - an existing key is NEVER modified, description included
      - a key with no matching folder on disk is left in place untouched;
        its name is returned in `missing` so the caller can warn about it

    If the section doesn't exist at all yet, it's created fresh at the end
    of the file with every detected folder added blank.

    Returns (new_text, added_names, missing_from_disk_names).
    """
    header_match, body_start, body_end = find_section_body(text, section_name)

    if header_match is None:
        # Section doesn't exist at all — create it fresh.
        if not detected_folders:
            return text, [], []
        new_section = f"\n[{section_name}]\n" + "\n".join(f"{f} = " for f in detected_folders) + "\n"
        return text.rstrip() + "\n" + new_section, list(detected_folders), []

    body = text[body_start:body_end]
    existing = existing_keys_in_section(body)
    added = [f for f in detected_folders if f not in existing]
    missing = [k for k in existing if k not in detected_folders]

    if not added:
        return text, [], missing

    insertion = "\n".join(f"{f} = " for f in added)
    insert_at = body_start + last_entry_end_offset(body)

    if insert_at == body_start:
        # Section has no existing entries at all — insert right after the header.
        new_text = text[:insert_at] + insertion + "\n" + text[insert_at:]
    else:
        # Insert right after the last real entry line, before any trailing
        # comments (which usually describe the *next* section, not this one).
        prefix = "" if text[:insert_at].endswith("\n") else "\n"
        new_text = text[:insert_at] + prefix + insertion + "\n" + text[insert_at:]
    return new_text, added, missing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project-root", required=True, help="Full path to your project root folder")
    parser.add_argument("--corpus-root", default=None, help="Defaults to <project-root>/Corpus")
    parser.add_argument("--work-root", default=None, help="Defaults to <project-root>/Work")
    parser.add_argument("--add-to-corpus", required=True, help="Full path to your iCloud/local 'Add To Corpus' watch folder")
    parser.add_argument("--state-db", default="~/.hermes/state.db", help="Defaults to ~/.hermes/state.db")
    parser.add_argument("--repo-root", default=".", help="Path to the repo root (where Scripts/ lives). Defaults to current directory.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing anything")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    args = parser.parse_args()

    repo_root    = Path(args.repo_root).resolve()
    project_root = str(Path(args.project_root))
    corpus_root  = Path(args.corpus_root) if args.corpus_root else Path(project_root) / "Corpus"
    work_root    = Path(args.work_root) if args.work_root else Path(project_root) / "Work"

    db_path     = str(Path(project_root) / "Index" / "corpus.db")
    chroma_path = str(Path(project_root) / "Index" / "chroma")
    log_path    = str(Path(project_root) / "Index" / "ingest.log")

    corpus_folders = detect_category_folders(corpus_root)
    work_folders   = detect_category_folders(work_root)

    print("=" * 70)
    print("Research RAG — Project Configuration")
    print("=" * 70)
    print(f"  Project root        : {project_root}")
    print(f"  Corpus root         : {corpus_root}")
    print(f"  Work root           : {work_root}")
    print(f"  Add To Corpus       : {args.add_to_corpus}")
    print(f"  State DB            : {args.state_db}")
    print(f"  Corpus folders found: {corpus_folders or '(none found — check --corpus-root)'}")
    print(f"  Work folders found  : {work_folders or '(none found — check --work-root)'}")
    print("=" * 70)

    if not corpus_folders:
        print("WARNING: no subfolders detected under the Corpus root. "
              "[corpus_categories] in config.ini will not be touched.")
    if not work_folders:
        print("WARNING: no subfolders detected under the Work root. "
              "[work_categories] in config.ini will not be touched.")

    if not args.yes and not args.dry_run:
        resp = input("\nProceed with these values? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted — no files changed.")
            sys.exit(0)

    changes = []  # (filepath, description)

    # -----------------------------------------------------------------
    # 1. config.ini — paths
    # -----------------------------------------------------------------
    config_path = repo_root / CONFIG_FILE
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        original = text

        for key, value in [
            ("CORPUS_ROOT",  str(corpus_root)),
            ("WORK_ROOT",    str(work_root)),
            ("PROJECT_ROOT", project_root),
            ("DB_PATH",      db_path),
            ("CHROMA_PATH",  chroma_path),
            ("LOG_PATH",     log_path),
            ("STATE_DB_PATH", args.state_db),
            ("ADD_TO_CORPUS_ROOT", args.add_to_corpus),
        ]:
            text, found = set_config_value(text, key, value)
            if found:
                changes.append((str(config_path), f"{key} -> {value}"))
            else:
                print(f"  NOTE: key '{key}' not found in config.ini — skipped.")

        # -------------------------------------------------------------
        # 2. config.ini — [corpus_categories] / [work_categories]
        #    additive merge: existing entries (and descriptions) are
        #    never touched; only missing folders get added, blank.
        # -------------------------------------------------------------
        if corpus_folders:
            text, added, missing = merge_category_section(text, "corpus_categories", corpus_folders)
            if added:
                changes.append((str(config_path), f"[corpus_categories] += {added} (blank description — add one by hand)"))
            if missing:
                print(f"  WARNING: [corpus_categories] in config.ini lists {missing}, "
                      f"but no matching folder was found under {corpus_root}. Left as-is — "
                      f"remove by hand if these were deleted/renamed on purpose.")

        if work_folders:
            text, added, missing = merge_category_section(text, "work_categories", work_folders)
            if added:
                changes.append((str(config_path), f"[work_categories] += {added} (blank — descriptions unused for Work/)"))
            if missing:
                print(f"  WARNING: [work_categories] in config.ini lists {missing}, "
                      f"but no matching folder was found under {work_root}. Left as-is — "
                      f"remove by hand if these were deleted/renamed on purpose.")

        if text != original:
            if not args.dry_run:
                backup_once(config_path)
                config_path.write_text(text, encoding="utf-8")
        # ANTHROPIC_API_KEY is intentionally never touched.
    else:
        print(f"  SKIPPED — not found: {config_path}")

    # -----------------------------------------------------------------
    # 3. Crontab + manual-run .txt files — blanket placeholder replace
    # -----------------------------------------------------------------
    for rel_path in PLAIN_REPLACE_FILES:
        fpath = repo_root / rel_path
        if not fpath.exists():
            print(f"  SKIPPED — not found: {fpath}")
            continue

        text = fpath.read_text(encoding="utf-8")
        new_text, count = replace_placeholders(text, project_root)

        if count:
            changes.append((str(fpath), f"{count} path placeholder(s) -> {project_root}"))
            if not args.dry_run:
                backup_once(fpath)
                fpath.write_text(new_text, encoding="utf-8")
        else:
            print(f"  NOTE: no known placeholder found in: {fpath.name}")

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------
    print("\n" + "=" * 70)
    print("DRY RUN — no files were written. Changes that would be made:" if args.dry_run
          else "Done. Changes made:")
    print("=" * 70)
    if not changes:
        print("  (no changes — check the paths above and re-run)")
    for fpath, desc in changes:
        print(f"  {fpath}\n      {desc}")

    print("\nReminders:")
    print("  - ANTHROPIC_API_KEY was left untouched everywhere. Set it in your")
    print("    shell profile (~/.zshrc) AND at the top of your crontab, per")
    print("    INSTALL_AND_SETUP.md. Never put it in config.ini.")
    print("  - Any newly added categories have a BLANK description in config.ini.")
    print("    Open config.ini and write a real one for each under")
    print("    [corpus_categories] to improve autoadd.py's classification accuracy.")
    print("  - .bak files were created next to anything edited (first run only).")
    print("  - Re-run with --dry-run any time to confirm nothing has drifted.")


if __name__ == "__main__":
    main()
