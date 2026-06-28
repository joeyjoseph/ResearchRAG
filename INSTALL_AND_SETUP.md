# Research Corpus Toolkit — Installation and Setup

This document walks through installing the components, setting up the tool, and running it for the first time. For full system details once it's running, see the [System_Documentation](<System_Documentation.md>).

## Step 1 - Downloading the Repo and Naming Folders

- Download this repo by clicking the green **Code** button and select **Download Zip** This will download the tool with the necesary directory structures in place.
- Unzip if your OS doesn't automatically, then rename `ResearchRAG-main` to the name of your Project.
- Move the entire directory out of your `Downloads` folder into a more logical place. We placed ours in `/Users/your username/Documents/My Project`, but you can place it whereever you like.

Throughout this guide, `<PROJECT_ROOT>` stands for wherever you keep your project folder. Until we have a scripted installer, you'll be required to open the scripts contained in the project folders and replace any file paths with your own.

### The Folder Structure

This toolkit splits your files into two roles:

- **Source material** — books, articles, interviews, and other reference documents are contained in the folder named ("Corpus")
- **Your own writing** — drafts, manuscript chapters, notes, research memos, outlines are contained in the folder named ("Work") 

Decide your structure now, because two things need to match it before you run anything:

1. **`Scripts/config.ini`** — the `CORPUS_ROOT` and `WORK_ROOT` paths
2. **`Scripts/ingest.py`** — the `CORPUS_CATEGORY_FOLDERS` and `WORK_CATEGORY_FOLDERS` sets, which must list your actual top-level subfolder names so files get tagged correctly

If you're not sure yet, it's fine to start with two simple folders and refine later — adding a new top-level folder later just means updating those two places and re-running ingest (see "Adding New Content" in [System_Documentation](<System_Documentation.md>)).

### What kind of files should be in the Corpus?

We chose to convert everything into plain txt files. Who knows why. Could've been md files, or html, or json. But there's a general preference for working with simple consistent data structures. You can use a different format, or even multiple formats. Just know that if you have a lot of PDFs or unsusual files, you're going to want to convert them into something the system can ingest and work with more easily.

The autoadd.py script that handles adding new files to the corpus as you go about using it. It wants to convert everything to txt. If you want it to do something different, you can either crack it open and manually to change it, or just ask Hermes to do it for you.

---

## Step 2 - Installing System Prerequisites

- macOS or Linux with Homebrew available (the cron jobs assume a Homebrew `PATH`) Open your terminal and enter:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
- Be sure to follow any instructions Homebrew gives at the end of installation to activate environments.
  
- Python 3.10+

Once Homebrew is installed, in terminal enter:
```bash
brew install python
```

---

## Step 3 Installing Code Libraries

Install steps must run in order — the install script builds the Python environment everything else depends on, and the API key must exist in your shell environment before any script that calls the Anthropic API will work.

### 1. Place the project folder

```bash
cd "<PROJECT_ROOT>"
```

### 2. Run the install script

This creates the `corpus-env` virtual environment, installs all required Python packages, and downloads the embedding and NER models.

```bash
bash Scripts/install.sh
```

This installs:

| Package | Purpose |
|---|---|
| chromadb | Vector store |
| sentence-transformers | Local embedding model (`all-MiniLM-L6-v2`, ~90MB) |
| spacy + en_core_web_sm | Named entity recognition (~12MB) |
| anthropic | Anthropic API client |
| requests | LM Studio API calls |

The script will fail loudly and stop (`set -e`) if Python is below 3.10 or if any package fails to verify — check the printed output for `FAIL` lines if something goes wrong.

### 3. Activate the environment

Every manual script run needs this first, in any new terminal session:

```bash
source "<PROJECT_ROOT>/corpus-env/bin/activate"
```

## Step 4. Installing and configuring your LLM Tools

This will walk you through a hybrid local + frontier model setup. If you want to run everything local, skip the Anthropic (or other) API setup steps. You will need

- An Anthropic API key (for summarizing source/reference material)
- [LM Studio](https://lmstudio.ai) or its headless counterpart llmster installed (for summarizing your own unpublished writing — manuscript, drafts, notes — entirely locally, so it never leaves your machine) The installation for this is covered later in the document.
- ~200MB free disk space for the embedding model, NER model, and Python environment

### 1. Adding your Anthropic API to the tool
Login to [console.anthropic.comm](https://console.anthropic.com/) and copy your API key. Then add it to your shell profile so it's available in normal terminal sessions:

```bash
echo 'export ANTHROPIC_API_KEY=your-key-here' >> ~/.zshrc
source ~/.zshrc
```

**Important:** `~/.zshrc` is not read by cron. If you plan to schedule nightly runs (Step 8 below), the key must also be set explicitly at the top of the crontab — see that section.

Never write the key into `config.ini`, into a backup file, or into anything else stored inside the project folder — it should only ever live in your shell environment or in the crontab itself.

### 2. Install LM Studio

Download and install from [lmstudio.ai](https://lmstudio.ai), then load any chat-capable local model.

The LM Studio app is fairly lightweight, but if you want an even more lightweight installation and are comfortable in a CLI there is a headless LM Studio CLI package called [llmster](https://lmstudio.ai/docs/developer/core/headless_llmster). From the perspective of both this Research RAG tool and your Hermes agent, there is no difference between the two.

Choosing a model is dependent upon your computer's cababilities and RAM.
- For basic use on most computers, select a 4bit quantized model like qwen/qwen3.5-4b or google/gemma4-4eb. In my experience, Qwen has been better at tool use. Even smaller lower reasoning models should be sufficient for the core database creation component of the tool. In fact some people recommend the smaller qwen2.5-0.5b model for basic classification taks. If you are planning to go 100% local, you will need a larger model for reasoning tasks like asking questions about your Corpus.

- If you are going to use the LM Studio app, you can use the built-in model search and download capability to search for those.
- If you are going to use the CLI, the command is 'lms get'. You can type that and see a preselected list of models to choose from.

If there's one you've found on [huggingface](<https://huggingface.co>) that you like, the command is something like this:

```bash
lms get https://huggingface.co/Qwen/Qwen3.5-4B
```

- Next, load the model and give it a context lenght of around 60000 if you have enough RAM. While Hermes (your agent) has auto contet compression, it's nice to minimize the necessity, and it might even yell at you if it's not around 40000. Looks like:

```bash
lms load qwen/qwen3.5-4b --context-length 64000
```

- Leave LM Studio running with a model loaded — summarization and manuscript-status scripts depend on it being reachable at its default end-point of `http://localhost:1234/v1`.
- Note that unlike Anthropic or other model providers that use APIs, LMStudio/llmster leave their API value empty in any relevant scripts in the /Scripts folder.
- While this Research RAG tool does not require Hermes to have access to LMStudio/llmster, if you decide to make it available to Hermes for some other reason, you will leave the API key field blank during the process of connecting the model to Hermes.

---

## Setup — First Run

Once installation is complete, run through these steps in order to bring your corpus online for the first time.

### 1. Configure paths

Open `Scripts/config.ini` and set the `[paths]` section to match the folder structure you decided on in Step 1:

```ini
[paths]
CORPUS_ROOT  = <PROJECT_ROOT>/Corpus
WORK_ROOT    = <PROJECT_ROOT>/Work
PROJECT_ROOT = <PROJECT_ROOT>
DB_PATH      = <PROJECT_ROOT>/Index/corpus.db
CHROMA_PATH  = <PROJECT_ROOT>/Index/chroma
LOG_PATH     = <PROJECT_ROOT>/Index/ingest.log
STATE_DB_PATH = ~/.hermes/state.db
```

- Adjust `CORPUS_ROOT` and `WORK_ROOT` if you're using different top-level names than `Corpus`/`Work`.

- Leave `ANTHROPIC_API_KEY` blank in this file — it should only ever come from the environment variable set in Step 4 above.

- Fill in `RESEARCH_THEMES` under `[summarize]` with your project's own themes, comma-separated (e.g. `chaos theory, complexity science, Cold War, systems thinking`). This helps the summarize step tag documents for relevance.

### 2. Register your folder names in ingest.py

Open `Scripts/ingest.py` and find these two sets near the top:

```python
CORPUS_CATEGORY_FOLDERS = {
    "Books", "Interviews", "Articles Journals Websites", "Misc"
}

WORK_CATEGORY_FOLDERS = {
    "Proposal", "Drafts", "Manuscript", "Research Memos", "Notes"
}
```

Replace the contents with the actual top-level subfolder names you created under your `Corpus/` and `Work/` roots. Any subfolder nested *inside* one of these is detected automatically — only the top-level category names need to be listed explicitly here.

### 3. Add your source material

Dropp your files into the subfolders you defined accordingly.

### 4. Run the first ingest

```bash
cd "<PROJECT_ROOT>"
source corpus-env/bin/activate
python3 Scripts/ingest.py --config Scripts/config.ini
```

This builds `Index/corpus.db` and `Index/chroma/` from scratch, embedding every file found. Watch `Index/ingest.log` if anything looks off — and double-check the log against the folder names you registered in Step 2 if files end up mis-categorized.

### 5. Generate abstracts

Make sure LM Studio is running with a model loaded, then:

```bash
python3 Scripts/summarize.py --config Scripts/config.ini
```

Source documents route to the Anthropic API; your own Work documents route to LM Studio, keeping unpublished writing off any external service. This can take a while on a large first run — `BATCH_SIZE` in `config.ini` caps how many documents are processed per run if you want to do it in chunks.

### 6. Update manuscript/draft status

```bash
python3 Scripts/currentstatus.py --config Scripts/config.ini
```

This scans your manuscript-equivalent Work folder and writes a status summary into your project's context file. LM Studio must be running for this step too. If your manuscript folder isn't literally named `Manuscript`, check that `currentstatus.py` points at the right subfolder for your structure.

### 7. OPTIONAL Generate the dashboard

If you want to see info about your project at a glance, you can use the included web dashboard. In order to build it, run the following commands.

```bash
python3 Scripts/dashboard.py --config Scripts/config.ini
```

This produces `dashboard.html` in your project root. To view it in a browser rather than opening the file directly:

```bash
python3 -m http.server 8080 --directory "<PROJECT_ROOT>" &
```

Then visit `http://localhost:8080/dashboard.html`. Stop the server when done:

```bash
pkill -f "http.server 8080"
```

### 8. Schedule nightly automation to ensure your tool is always up-to-date

These cronjobs will make sure your database is always fresh in the morning with any documents added/removed/changed the previous day accounted for.
Open your crontab:

```bash
crontab -e
```

Copy the text below into your text editor. Add your API key at the top. There are example filepaths for each cron job. Replace filepaths with your actual project paths. Rather than spaces, use `\` if your path contains any.

```
# Research RAG — nightly automation
# Order of execution: autoadd -> ingest -> summarize -> currentstatus -> dashboard

MAILTO=""
ANTHROPIC_API_KEY=Your_API_KEY_HERE

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

Order matters: autoadd → ingest → summarize → manuscript status → dashboard, so each job has fresh data from the one before it. Back up your crontab any time you edit it, but keep that backup file scrubbed of the API key line — store the key separately (e.g. a password manager), since a backup file is otherwise plain text sitting on disk:

```bash
crontab -l > Scripts/crontab.backup.txt
```

---

## Installing the Hermes Agent (under construction)

The research agent (referred to here as "Hermes") is installed separately from this toolkit. Full documentation lives at [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs).

### 1. Install Hermes

You can install either the desktop app or the CLI-only version — both give you the same underlying agent, so pick whichever fits how you like to work:

- **Desktop app** — download the installer for macOS, Windows, or Linux from the [Hermes Agent site](https://hermes-agent.nousresearch.com)
- **CLI only** — run the terminal installer:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

Either path sets up the same agent — the choice doesn't affect anything else in this guide.

### 2. Set up Hermes Agent's SOUL
The SOUL of an agent is a document describing how you want the agent to behave. Some call this its personality, but it can also describe general rules for how it should interact not only with the user, but also what kind of role it should assume, etc.. If you don't want a sychophantic robot, or for it to give you peanut gallery comments on how insightful your questions are, this is the place to tell it.

You don't need to create a SOUL document yourself. Hermes generates one automatically on first run, and you can revise it at any time either by simply telling Hermes in chat what you'd like to change about how it behaves. A good practice is to think about this and write it to a note or text file, and when you're done, begin a chat with Hermes and instruct it to "Update your SOUL document to include the following: [then paste your writing here]".

You can create multiple profiles and souls. But for the sake of this guide, we assume you are the only user and your project is your primary task. You can ignore this SOUL document entirely, and just see what happens if you're feeilng experimental. Likely it'll just stay blank, but sometimes the robot has a mind of its own!

### 3. Set up the project-specific SKILL

Skills are chucnks of knowldege and procedures Hermes can call upon automatically or by command. Hermes will come with preloaded skills, and it can automatically save new skills it learns through repetion or trial and error without any user input. An example of a skill:

> The user says "transcribe this audio file". From a previous session Hermes recognizes that phrase as an invocation of a skill about transcribing audio files. It automatically looks at the appropriately named SKILL document and sees that it should use a particular transcription service the user trusts, and executes a series predefined steps to accomplish the task.

A SKILL can be short and simple, or highly detailed instructions for how Hermes should behave with certain tasks. Similarly to the SOUL doc, the best course of action is to sit down and type up a document outlining how you want Hermes to behave when assigned a task related to your research project. Then tell hermes you want to create a new SKILL for your project, tell it to name it [PROJECT_NAME] SKILL, and paste it into the chat. Hermes will create the new SKILL.

> The [PROJECT_NAME] SKill should contain all relevant information about your Project: who you are, what your goal is, etc.. It should contain a detailed descritption of the database you've built, and all relevant paths (`CORPUS_ROOT`, `WORK_ROOT`, `DB_PATH`, `CHROMA_PATH` — give it the actual values from your `config.ini`), the SQLite schema in `corpus.db`, and the ChromaDB semantic search command for the `research_corpus` collection. Include a search decision guide for when to use SQLite vs. ChromaDB vs. both, a standard research workflow, rules for when to ask before searching the web, note-saving conventions for my Work folder, citation formats for corpus documents vs. my own writing vs. web sources, and a session-startup checklist. You want to include trigger phrases so that Hermes knows when to use this skill. Good ones are the skill name, project name, shorthand project name. If you were writing a book tentitively titled "Land of Tomorrow", you might want to include something like "lot" as a trigger.

### 4. Activate the skill

When you start new session, it's best to be safe and let Hermes know explicity that you want to work on your projec by invoking the project research skill, as opposed to any other generic skills Hermes has. You'll want to use the above mentioned trigger phrases you created by simply typing something like:

> "Let's work on the book" assuming "the book" was one of your triggers
> "[Project Name] Quesion..."
> "Load your [skill name]"

If for some reason Hermes refuses to acknowledge your trigger phrases, you can manaully load it in a session by typing:

```
/skill <your-skill-name>
```

You can revise skills at any time by chatting with Hermes. After asking Hermes to revise a skill, reload it without restarting your session:

```
/reload-skills
```


### 5. (Optional) Enable usage tracking for the dashboard

If you want the dashboard's Hermes Activity, Token Cost, and Recent Sessions panels to populate, ask Hermes whether it logs session data to a local SQLite database, and where. Set that path as `STATE_DB_PATH` in `config.ini` (default `~/.hermes/state.db`). This is independent of the corpus index — `dashboard.py` will simply show empty/zeroed panels if this database isn't present.

---

## Verifying the install

A quick sanity check that everything is wired up:

```bash
# Corpus stats
sqlite3 "Index/corpus.db" "SELECT source_type, document_role, COUNT(*) FROM documents WHERE status='ingested' GROUP BY source_type, document_role;"

# Recent ingest activity
tail -50 Index/ingest.log

# Confirm scheduled jobs are in place
crontab -l
```

If `corpus.db` shows rows under the source_type names you registered, and `ingest.log` shows no `FAILED` entries, the system is ready for day-to-day use.
