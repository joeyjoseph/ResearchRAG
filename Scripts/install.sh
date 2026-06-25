#!/bin/bash
# install.sh — Research Corpus Setup
# Run from the directory where you want your virtual environment to live.
# Usage: bash install.sh

set -e  # Stop immediately if any command fails

echo "========================================================"
echo " Research Corpus — Installation"
echo "========================================================"

# --- Check Python version ---
echo ""
echo "Checking Python version..."
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "ERROR: python3 not found. Install Python 3.10+ from https://www.python.org/downloads/"
    exit 1
fi

VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
    echo "ERROR: Python 3.10+ required. Found Python $VERSION."
    echo "Install a newer Python from https://www.python.org/downloads/"
    exit 1
fi
echo "  OK — Python $VERSION"

# --- Check for Homebrew (required for tesseract/poppler, used by autoadd.py) ---
echo ""
echo "Checking for Homebrew..."
if ! command -v brew >/dev/null 2>&1; then
    echo "ERROR: Homebrew not found. Install it first:"
    echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    exit 1
fi
echo "  OK — Homebrew found."

# --- Install system binaries: tesseract (OCR) and poppler (PDF rasterization) ---
# Used by autoadd.py to convert scanned PDFs and images dropped into the
# watch folder into searchable text before they're filed into the corpus.
echo ""
echo "Installing tesseract (OCR engine)..."
if brew list tesseract >/dev/null 2>&1; then
    echo "  Already installed, skipping."
else
    brew install tesseract
    echo "  OK — tesseract installed."
fi

echo ""
echo "Installing poppler (PDF-to-image conversion, used for OCR fallback)..."
if brew list poppler >/dev/null 2>&1; then
    echo "  Already installed, skipping."
else
    brew install poppler
    echo "  OK — poppler installed."
fi

# --- Create virtual environment ---
echo ""
echo "Creating virtual environment (corpus-env)..."
if [ -d "corpus-env" ]; then
    echo "  corpus-env already exists, skipping creation."
else
    $PYTHON -m venv corpus-env
    echo "  OK — corpus-env created."
fi

# --- Activate virtual environment ---
echo ""
echo "Activating virtual environment..."
source corpus-env/bin/activate
echo "  OK — corpus-env active."

# --- Upgrade pip ---
echo ""
echo "Upgrading pip..."
pip install --upgrade pip --quiet
echo "  OK — pip upgraded."

# --- Install chromadb ---
echo ""
echo "Installing chromadb..."
pip install chromadb --quiet
echo "  OK — chromadb installed."

# --- Install sentence-transformers ---
echo ""
echo "Installing sentence-transformers..."
pip install sentence-transformers --quiet
echo "  OK — sentence-transformers installed."

# --- Install spaCy ---
echo ""
echo "Installing spaCy..."
pip install spacy --quiet
echo "  OK — spaCy installed."

# --- Download spaCy language model ---
echo ""
echo "Downloading spaCy English model (en_core_web_sm, ~12MB)..."
python3 -m spacy download en_core_web_sm --quiet
echo "  OK — en_core_web_sm downloaded."

# --- Pre-download sentence-transformers embedding model ---
echo ""
echo "Downloading embedding model (all-MiniLM-L6-v2, ~90MB)..."
python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
echo "  OK — embedding model ready."

# --- Install autoadd.py dependencies ---
# pypdf/pdf2image/pytesseract handle PDF and image conversion; python-docx
# handles Word docs; beautifulsoup4 handles saved HTML pages; Pillow backs
# both pytesseract and pdf2image.
echo ""
echo "Installing autoadd.py dependencies (pypdf, python-docx, beautifulsoup4, pytesseract, pdf2image, Pillow)..."
pip install pypdf python-docx beautifulsoup4 pytesseract pdf2image Pillow --quiet
echo "  OK — autoadd.py dependencies installed."

# --- Verify everything ---
echo ""
echo "Verifying installation..."
python3 - <<'EOF'
import sys
import shutil

results = []

try:
    import sqlite3
    results.append(f"  OK  sqlite3          {sqlite3.sqlite_version}")
except Exception as e:
    results.append(f"  FAIL sqlite3         {e}")

try:
    import chromadb
    results.append(f"  OK  chromadb         {chromadb.__version__}")
except Exception as e:
    results.append(f"  FAIL chromadb        {e}")

try:
    import sentence_transformers
    results.append(f"  OK  sentence-trans   {sentence_transformers.__version__}")
except Exception as e:
    results.append(f"  FAIL sentence-trans  {e}")

try:
    import spacy
    spacy.load("en_core_web_sm")
    results.append(f"  OK  spacy            {spacy.__version__} + en_core_web_sm")
except Exception as e:
    results.append(f"  FAIL spacy           {e}")

try:
    import pypdf
    results.append(f"  OK  pypdf            {pypdf.__version__}")
except Exception as e:
    results.append(f"  FAIL pypdf           {e}")

try:
    import docx
    results.append(f"  OK  python-docx      (module loaded)")
except Exception as e:
    results.append(f"  FAIL python-docx     {e}")

try:
    import bs4
    results.append(f"  OK  beautifulsoup4   {bs4.__version__}")
except Exception as e:
    results.append(f"  FAIL beautifulsoup4  {e}")

try:
    import pytesseract
    results.append(f"  OK  pytesseract      (module loaded)")
except Exception as e:
    results.append(f"  FAIL pytesseract     {e}")

try:
    import pdf2image
    results.append(f"  OK  pdf2image        (module loaded)")
except Exception as e:
    results.append(f"  FAIL pdf2image       {e}")

try:
    import PIL
    results.append(f"  OK  Pillow           {PIL.__version__}")
except Exception as e:
    results.append(f"  FAIL Pillow          {e}")

if shutil.which("tesseract"):
    results.append(f"  OK  tesseract binary {shutil.which('tesseract')}")
else:
    results.append(f"  FAIL tesseract binary  not found on PATH")

if shutil.which("pdftoppm"):  # comes from poppler, used by pdf2image
    results.append(f"  OK  poppler binary   {shutil.which('pdftoppm')}")
else:
    results.append(f"  FAIL poppler binary    not found on PATH")

print("\n".join(results))

failures = [r for r in results if "FAIL" in r]
if failures:
    print("\nSome packages failed to verify. Review errors above.")
    sys.exit(1)
else:
    print("\nAll packages verified successfully.")
EOF

# --- Done ---
echo ""
echo "========================================================"
echo " Installation complete."
echo ""
echo " To activate this environment in future terminal sessions:"
echo "   source corpus-env/bin/activate"
echo ""
echo " Next step: edit config.ini with your corpus paths,"
echo " then run:  python ingest.py --config config.ini"
echo "========================================================"
