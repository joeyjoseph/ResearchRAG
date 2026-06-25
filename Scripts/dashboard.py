#!/usr/bin/env python3
"""
Research RAG — Analytics Dashboard Generator v3
Reads from corpus.db and state.db (read-only), generates a self-contained dashboard.html.

Usage:
    python3 Scripts/dashboard.py --config Scripts/config.ini
    python3 Scripts/dashboard.py --config Scripts/config.ini --output /path/to/dashboard.html
"""

import argparse
import configparser
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta


# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────

def load_config(path):
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return {
        "corpus_db":    cfg.get("paths", "DB_PATH",        fallback="Index/corpus.db"),
        "state_db":     cfg.get("paths", "STATE_DB_PATH",  fallback=os.path.expanduser("~/.hermes/state.db")),
        "project_root": cfg.get("paths", "PROJECT_ROOT",   fallback="."),
        "work_root":    cfg.get("paths", "WORK_ROOT",       fallback="Work"),
    }


# ──────────────────────────────────────────────
#  CORPUS DATA
# ──────────────────────────────────────────────

def corpus_data(db_path):
    if not os.path.exists(db_path):
        print(f"[dashboard] corpus.db not found: {db_path}", file=sys.stderr)
        return {}
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    d = {}

    c.execute("SELECT COUNT(*) FROM documents WHERE status='ingested' AND document_role='source'")
    d["corpus_count"] = c.fetchone()[0]

    c.execute("SELECT SUM(word_count) FROM documents WHERE status='ingested' AND document_role='source'")
    d["corpus_words"] = c.fetchone()[0] or 0

    c.execute("SELECT SUM(word_count) FROM documents WHERE status='ingested' AND document_role='author' AND source_type='Manuscript'")
    d["manuscript_words"] = c.fetchone()[0] or 0

    c.execute("""SELECT source_type, COUNT(*) as cnt FROM documents
                 WHERE status='ingested' AND document_role='source'
                 GROUP BY source_type ORDER BY cnt DESC""")
    d["breakdown"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT filename, source_type, subfolder, ingest_date, word_count, author, year_published, abstract
                 FROM documents WHERE status='ingested' AND document_role='source'
                 ORDER BY ingest_date DESC LIMIT 5""")
    d["recently_added"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT filename, ingest_date, word_count, abstract
                 FROM documents WHERE status='ingested' AND document_role='author'
                 AND source_type='Research Memos'
                 ORDER BY ingest_date DESC LIMIT 10""")
    d["recent_memos"] = [dict(r) for r in c.fetchall()]

    c.execute("""SELECT COUNT(*) FROM documents WHERE status='ingested'
                 AND document_role='author' AND source_type='Research Memos'""")
    d["total_memos"] = c.fetchone()[0]

    c.execute("""SELECT filename, word_count, ingest_date FROM documents
                 WHERE status='ingested' AND document_role='author' AND source_type='Manuscript'
                 ORDER BY last_modified DESC LIMIT 12""")
    d["manuscript_files"] = [dict(r) for r in c.fetchall()]

    conn.close()
    return d


# ──────────────────────────────────────────────
#  STATE DATA
# ──────────────────────────────────────────────

def parse_timestamp(raw):
    if raw is None:
        return ""
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""
    return str(raw)[:16].replace("T", " ")


def state_data(db_path):
    d = {
        "questions_asked":  0,
        "avg_tokens":       0,
        "avg_response_sec": 0,
        "tokens_today":     0,
        "tokens_week":      0,
        "tokens_total":     0,
        "cost_today":       0.0,
        "cost_week":        0.0,
        "cost_total":       0.0,
        "daily_costs":      [],
        "recent_questions": [],
        "memos_generated":  0,
    }
    if not os.path.exists(db_path):
        print(f"[dashboard] state.db not found: {db_path}", file=sys.stderr)
        return d

    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA query_only = ON")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("PRAGMA table_info(sessions)")
        cols = {r["name"] for r in c.fetchall()}

        today    = datetime.now().date().isoformat()
        week_ago = (datetime.now().date() - timedelta(days=7)).isoformat()

        tok_cols    = [t for t in ("input_tokens", "output_tokens", "cache_read_tokens") if t in cols]
        cost_col    = "estimated_cost_usd" if "estimated_cost_usd" in cols else None
        started_col = "started_at"         if "started_at"         in cols else None

        if tok_cols and started_col:
            tok_sum = " + ".join(f"COALESCE({t},0)" for t in tok_cols)

            c.execute(f"SELECT COUNT(*), SUM({tok_sum}), AVG({tok_sum}) FROM sessions")
            row = c.fetchone()
            d["questions_asked"] = row[0] or 0
            d["tokens_total"]    = int(row[1] or 0)
            d["avg_tokens"]      = int(row[2] or 0)

            c.execute(f"SELECT {started_col} FROM sessions LIMIT 1")
            sample = c.fetchone()
            if sample and isinstance(sample[0], (int, float)):
                today_ts    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                week_ago_ts = (datetime.now() - timedelta(days=7)).timestamp()
                c.execute(f"SELECT SUM({tok_sum}) FROM sessions WHERE {started_col} >= ?", (today_ts,))
                d["tokens_today"] = int(c.fetchone()[0] or 0)
                c.execute(f"SELECT SUM({tok_sum}) FROM sessions WHERE {started_col} >= ?", (week_ago_ts,))
                d["tokens_week"]  = int(c.fetchone()[0] or 0)
                if cost_col:
                    c.execute(f"SELECT SUM({cost_col}) FROM sessions")
                    d["cost_total"] = round(c.fetchone()[0] or 0, 2)
                    c.execute(f"SELECT SUM({cost_col}) FROM sessions WHERE {started_col} >= ?", (today_ts,))
                    d["cost_today"] = round(c.fetchone()[0] or 0, 2)
                    c.execute(f"SELECT SUM({cost_col}) FROM sessions WHERE {started_col} >= ?", (week_ago_ts,))
                    d["cost_week"]  = round(c.fetchone()[0] or 0, 2)
                    thirty_ago_ts = (datetime.now() - timedelta(days=30)).timestamp()
                    c.execute(f"""SELECT date({started_col}, 'unixepoch') as day, SUM({cost_col}) as cost
                                  FROM sessions WHERE {started_col} >= ?
                                  GROUP BY day ORDER BY day""", (thirty_ago_ts,))
                    d["daily_costs"] = [{"day": r["day"], "cost": round(r["cost"] or 0, 2)} for r in c.fetchall()]
            else:
                c.execute(f"SELECT SUM({tok_sum}) FROM sessions WHERE date({started_col}) = ?", (today,))
                d["tokens_today"] = int(c.fetchone()[0] or 0)
                c.execute(f"SELECT SUM({tok_sum}) FROM sessions WHERE date({started_col}) >= ?", (week_ago,))
                d["tokens_week"]  = int(c.fetchone()[0] or 0)
                if cost_col:
                    c.execute(f"SELECT SUM({cost_col}) FROM sessions")
                    d["cost_total"] = round(c.fetchone()[0] or 0, 2)
                    c.execute(f"SELECT SUM({cost_col}) FROM sessions WHERE date({started_col}) = ?", (today,))
                    d["cost_today"] = round(c.fetchone()[0] or 0, 2)
                    c.execute(f"SELECT SUM({cost_col}) FROM sessions WHERE date({started_col}) >= ?", (week_ago,))
                    d["cost_week"]  = round(c.fetchone()[0] or 0, 2)
                    c.execute(f"""SELECT date({started_col}) as day, SUM({cost_col}) as cost
                                  FROM sessions WHERE date({started_col}) >= date('now','-30 days')
                                  GROUP BY day ORDER BY day""")
                    d["daily_costs"] = [{"day": r["day"], "cost": round(r["cost"] or 0, 2)} for r in c.fetchall()]

        if "started_at" in cols and "ended_at" in cols:
            c.execute("""SELECT AVG((julianday(ended_at) - julianday(started_at)) * 86400)
                         FROM sessions WHERE ended_at IS NOT NULL
                         AND typeof(ended_at) = 'text'""")
            val = c.fetchone()[0]
            d["avg_response_sec"] = round(val or 0, 1)

        title_col = "title" if "title" in cols else None
        if title_col and started_col:
            c.execute(f"SELECT {title_col}, {started_col} FROM sessions ORDER BY {started_col} DESC LIMIT 10")
            d["recent_questions"] = [
                {"title": r[0] or "—", "started_at": r[1]}
                for r in c.fetchall()
            ]

        if "source" in cols:
            c.execute("SELECT COUNT(*) FROM sessions WHERE lower(source) = 'cron'")
            d["memos_generated"] = c.fetchone()[0]
        elif title_col:
            c.execute(f"SELECT COUNT(*) FROM sessions WHERE lower({title_col}) LIKE '%memo%'")
            d["memos_generated"] = c.fetchone()[0]

        conn.close()

    except Exception as e:
        print(f"[dashboard] state.db error: {e}", file=sys.stderr)

    return d


# ──────────────────────────────────────────────
#  CRON JOBS
# ──────────────────────────────────────────────

def get_cron_jobs():
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        jobs = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("ANTHROPIC"):
                continue
            parts = line.split(None, 5)
            if len(parts) >= 6:
                schedule = " ".join(parts[:5])
                cmd      = parts[5]
                script   = next((p for p in cmd.split() if p.endswith(".py")), cmd[-50:])
                script   = os.path.basename(script)
                jobs.append({"schedule": schedule, "script": script})
        return jobs
    except Exception:
        return []


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────

def fmt(n, decimals=0):
    if n is None:
        return "—"
    if decimals > 0:
        return f"{float(n):,.{decimals}f}"
    return f"{int(n):,}"


def strip_ext(s):
    import re
    return re.sub(r'\.(txt|pdf|docx|md)$', '', s or '', flags=re.IGNORECASE)


def memo_topic(abstract):
    if not abstract:
        return ""
    for line in abstract.split("\n"):
        if line.startswith("TOPIC:"):
            return line.replace("TOPIC:", "").strip()
    return ""


def sched_human(s):
    mapping = {
        "0 2 * * *":  "Nightly 2:00 AM",
        "0 3 * * *":  "Nightly 3:00 AM",
        "30 3 * * *": "Nightly 3:30 AM",
        "0 4 * * *":  "Nightly 4:00 AM",
    }
    return mapping.get(s, s)


# ──────────────────────────────────────────────
#  HTML BUILDER
# ──────────────────────────────────────────────

def build_html(corp, state, cron_jobs, generated_at, work_root=""):

    ring_colors = ["#b05a00","#1a4f7a","#3a6e2a","#7a2a5a","#2a5a7a","#7a5a2a","#4a2a7a","#2a7a5a"]
    breakdown   = corp.get("breakdown", [])
    total_bd    = sum(r["cnt"] for r in breakdown) or 1
    ring_segments = []
    for i, row in enumerate(breakdown):
        ring_segments.append({
            "label": row["source_type"] or "Other",
            "count": row["cnt"],
            "pct":   round(row["cnt"] / total_bd * 100, 1),
            "color": ring_colors[i % len(ring_colors)],
        })

    # ── Memo cards ────────────────────────────
    def memo_cards(memos, limit=5):
        if not memos:
            return '<div class="empty">No research memos found in corpus.db</div>'
        out = ""
        for m in memos[:limit]:
            name  = strip_ext(m["filename"])
            topic = memo_topic(m.get("abstract") or "")
            date  = (m.get("ingest_date") or "")[:10]
            words = fmt(m.get("word_count"))

            memo_path = os.path.join(work_root, "Research Memos", m["filename"])
            file_content = ""
            if os.path.exists(memo_path):
                try:
                    with open(memo_path, "r", encoding="utf-8", errors="replace") as f:
                        file_content = f.read().strip()
                except Exception:
                    file_content = ""

            if file_content:
                content_html = "<br>".join(line for line in file_content.splitlines())
                exp_html = f'<div class="memo-content">{content_html}</div>'
            else:
                exp_html = '<div class="empty">File not found in Work/Research Memos</div>'

            out += f"""
<div class="memo-item expandable" onclick="toggleExpand(this)">
  <div class="memo-main">
    <span class="expand-hint">&#9660; expand</span>
    <div class="memo-name">{name}</div>
    {'<div class="memo-topic">' + topic + '</div>' if topic else ''}
    <div class="memo-foot"><span>{date}</span><span>{words} words</span></div>
  </div>
  <div class="expand-panel">{exp_html}</div>
</div>"""
        return out

    # ── Recently added docs ───────────────────
    def recent_docs_html(docs):
        if not docs:
            return '<div class="empty">No documents found</div>'
        out = ""
        for doc in docs:
            name  = strip_ext(doc["filename"])
            meta  = " · ".join(filter(None, [doc.get("source_type"), doc.get("author"), doc.get("year_published")]))
            date  = (doc.get("ingest_date") or "")[:10]
            words = fmt(doc.get("word_count"))
            out += f"""
<div class="doc-item expandable" onclick="toggleExpand(this)">
  <div class="doc-main">
    <span class="expand-hint">&#9660; expand</span>
    <div class="doc-name">{name[:65]}</div>
    <div class="doc-meta">{meta[:60]}</div>
  </div>
  <div class="doc-right"><span class="doc-date">{date}</span></div>
  <div class="expand-panel">
    <div class="exp-row"><span class="exp-key">WORDS</span><span class="exp-val">{words}</span></div>
    <div class="exp-row"><span class="exp-key">FOLDER</span><span class="exp-val">{doc.get('subfolder') or doc.get('source_type') or '—'}</span></div>
    <div class="exp-row"><span class="exp-key">AUTHOR</span><span class="exp-val">{doc.get('author') or '—'}</span></div>
    <div class="exp-row"><span class="exp-key">YEAR</span><span class="exp-val">{doc.get('year_published') or '—'}</span></div>
  </div>
</div>"""
        return out

    # ── Manuscript files ──────────────────────
    def manuscript_files_html(files):
        if not files:
            return '<div class="empty">No manuscript files found</div>'
        out = ""
        for f in files:
            name  = strip_ext(f["filename"])
            words = fmt(f.get("word_count"))
            out += f'<div class="ms-row"><span class="ms-name">{name[:50]}</span><span class="ms-words">{words}</span></div>'
        return out

    # ── Cron jobs ─────────────────────────────
    def cron_html(jobs):
        if not jobs:
            return '<div class="empty">No crontab entries found</div>'
        out = ""
        for j in jobs:
            out += f'<div class="cron-row"><span class="cron-script">{j["script"]}</span><span class="cron-sched">{sched_human(j["schedule"])}</span></div>'
        return out

    # ── Recent sessions ───────────────────────
    def questions_html(qs):
        if not qs:
            return '<div class="empty">No sessions found in state.db</div>'
        out = ""
        for q in qs[:8]:
            title = q.get("title") or "—"
            ts    = parse_timestamp(q.get("started_at"))
            out  += f'<div class="q-row"><span class="q-arrow">&#8250;</span><span class="q-text">{title}</span><span class="q-ts">{ts}</span></div>'
        return out

    # ── Formatted values ──────────────────────
    now_str          = generated_at.strftime("%B %d, %Y — %I:%M %p")
    ms_words         = fmt(corp.get("manuscript_words", 0))
    corpus_words     = fmt(corp.get("corpus_words", 0))
    corpus_count     = fmt(corp.get("corpus_count", 0))
    total_memos      = fmt(corp.get("total_memos", 0))
    questions_asked  = fmt(state.get("questions_asked", 0))
    memos_generated  = fmt(state.get("memos_generated", 0))
    avg_tokens       = fmt(state.get("avg_tokens", 0))
    avg_secs         = f'{state.get("avg_response_sec", 0):.1f}s'
    cost_today       = f'${state.get("cost_today", 0):.2f}'
    cost_week        = f'${state.get("cost_week", 0):.2f}'
    cost_total       = f'${state.get("cost_total", 0):.2f}'
    tok_today        = fmt(state.get("tokens_today", 0))
    tok_week         = fmt(state.get("tokens_week", 0))
    tok_total        = fmt(state.get("tokens_total", 0))

    memo_home        = memo_cards(corp.get("recent_memos", []), 5)
    memo_work        = memo_cards(corp.get("recent_memos", []), 10)
    recent_docs      = recent_docs_html(corp.get("recently_added", []))
    ms_files         = manuscript_files_html(corp.get("manuscript_files", []))
    cron_markup      = cron_html(cron_jobs)
    questions_markup = questions_html(state.get("recent_questions", []))
    ring_json        = json.dumps(ring_segments)
    daily_json       = json.dumps(state.get("daily_costs", []))
    total_memos_int  = corp.get("total_memos", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Research RAG — Research Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,500;0,700;1,500&family=Inconsolata:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:      #f4f1ec;
  --bg2:     #ffffff;
  --bg3:     #eceae4;
  --bg4:     #e2dfd8;
  --line:    #d0cbc0;
  --line2:   #b8b2a6;
  --ink:     #1a1714;
  --ink2:    #3d3830;
  --ink3:    #706860;
  --amber:   #b05a00;
  --amber2:  #8a4400;
  --blue:    #1a4f7a;
  --green:   #2a5e1e;
  --red:     #8a1a1a;
  --serif:   'Playfair Display',Georgia,serif;
  --mono:    -apple-system,'SF Mono','Inconsolata','Courier New',monospace;
  --r:       5px;
  --sh:      0 1px 4px rgba(0,0,0,.08), 0 2px 12px rgba(0,0,0,.05);
}}
html,body{{height:100%;overflow:hidden}}
body{{background:var(--bg);color:var(--ink);font-family:var(--mono);font-size:15px;display:flex;flex-direction:column}}

/* ── Header ── */
.header{{display:flex;align-items:center;justify-content:space-between;padding:0 28px;height:56px;background:var(--ink);flex-shrink:0;border-bottom:3px solid var(--amber)}}
.header-title{{font-family:var(--serif);font-size:18px;font-weight:500;color:#f4f1ec}}
.header-title em{{font-style:italic;color:#e8b060}}
.header-meta{{font-size:12px;color:#a09080;letter-spacing:.06em;text-align:right;line-height:1.7}}
.header-meta span{{display:block}}

/* ── Shell ── */
.shell{{display:flex;flex:1;overflow:hidden}}

/* ── Sidebar ── */
.sidebar{{width:190px;flex-shrink:0;background:var(--bg3);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:20px 0}}
.nav-label{{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink3);padding:18px 22px 7px;font-weight:600}}
.nav-label:first-child{{padding-top:6px}}
.nav-item{{display:flex;align-items:center;gap:10px;padding:10px 22px;cursor:pointer;color:var(--ink2);font-size:14px;transition:all .15s;border-left:3px solid transparent}}
.nav-item:hover{{background:var(--bg4);color:var(--ink)}}
.nav-item.active{{background:var(--bg2);color:var(--amber);border-left-color:var(--amber);font-weight:600}}
.nav-icon{{font-size:15px;width:20px;text-align:center;flex-shrink:0}}

/* ── Main ── */
.main{{flex:1;overflow-y:auto;padding:26px;scrollbar-width:thin;scrollbar-color:var(--line2) transparent;background:var(--bg)}}
.main::-webkit-scrollbar{{width:6px}}
.main::-webkit-scrollbar-thumb{{background:var(--line2);border-radius:3px}}

/* ── Pages ── */
.page{{display:none}}
.page.active{{display:block;animation:fadeIn .18s ease}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(3px)}}to{{opacity:1;transform:none}}}}
.page-title{{font-family:var(--serif);font-size:15px;font-weight:500;font-style:italic;color:var(--ink3);margin-bottom:22px;border-bottom:1px solid var(--line);padding-bottom:10px}}

/* ── Grids ── */
.grid{{display:grid;gap:18px}}
.grid-home{{grid-template-columns:1.4fr 1fr}}
.grid-2{{grid-template-columns:1fr 1fr}}
.grid-3{{grid-template-columns:1fr 1fr 1fr}}
.col-span-2{{grid-column:span 2}}

/* ── Tiles ── */
.tile{{background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;box-shadow:var(--sh);transition:border-color .2s,box-shadow .2s}}
.tile:hover{{border-color:var(--line2);box-shadow:0 2px 8px rgba(0,0,0,.1),0 4px 20px rgba(0,0,0,.07)}}
.tile-head{{display:flex;align-items:center;justify-content:space-between;padding:11px 16px 10px;border-bottom:1px solid var(--line);background:var(--bg3)}}
.tile-title{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);font-weight:600}}
.tile-badge{{font-size:11px;color:var(--amber);letter-spacing:.06em}}
.tile-body{{padding:16px}}

/* ── Large stat numbers ── */
.stat-big{{font-size:42px;font-weight:700;color:var(--ink);letter-spacing:-.03em;line-height:1;font-variant-numeric:tabular-nums}}
.stat-big.amber{{color:var(--amber)}}
.stat-big.blue{{color:var(--blue)}}
.stat-sub{{font-size:13px;color:var(--ink3);margin-top:7px;letter-spacing:.02em}}

/* ── Memo items ── */
.memo-item{{padding:12px 0;border-bottom:1px solid var(--line);cursor:pointer}}
.memo-item:last-child{{border-bottom:none}}
.memo-name{{font-size:15px;color:var(--ink);line-height:1.35;font-weight:600}}
.memo-topic{{font-family:var(--serif);font-style:italic;font-size:14px;color:var(--ink2);margin-top:3px}}
.memo-foot{{display:flex;gap:14px;margin-top:6px;font-size:13px;color:var(--ink3)}}
.memo-content{{font-size:14px;color:var(--ink2);line-height:1.75;white-space:pre-wrap;font-family:var(--serif)}}

/* ── Expand ── */
.expand-panel{{display:none;margin-top:12px;padding:14px;background:var(--bg3);border-radius:var(--r);border:1px solid var(--line);max-height:400px;overflow-y:auto}}
.expandable.open .expand-panel{{display:block}}
.expandable.open .memo-name,.expandable.open .doc-name{{color:var(--amber)}}
.exp-row{{display:flex;gap:12px;padding:4px 0;border-bottom:1px solid var(--line);font-size:13px}}
.exp-row:last-child{{border-bottom:none}}
.exp-key{{width:100px;flex-shrink:0;color:var(--ink3);letter-spacing:.08em;font-size:11px;text-transform:uppercase;padding-top:1px}}
.exp-val{{color:var(--ink2);flex:1;line-height:1.4}}
.expand-hint{{font-size:12px;color:var(--ink3);float:right;letter-spacing:.04em;transition:color .15s}}
.expandable:hover .expand-hint{{color:var(--amber)}}
.expandable.open .expand-hint{{color:var(--amber);font-weight:600}}

/* ── Doc items ── */
.doc-item{{display:flex;gap:12px;align-items:flex-start;padding:11px 0;border-bottom:1px solid var(--line);cursor:pointer}}
.doc-item:last-child{{border-bottom:none}}
.doc-main{{flex:1;min-width:0}}
.doc-name{{font-size:15px;color:var(--ink);font-weight:600;line-height:1.3}}
.doc-meta{{font-size:13px;color:var(--ink3);margin-top:3px}}
.doc-right{{flex-shrink:0;text-align:right}}
.doc-date{{font-size:13px;color:var(--amber)}}

/* ── Manuscript rows ── */
.ms-row{{display:flex;justify-content:space-between;align-items:baseline;padding:8px 0;border-bottom:1px solid var(--line);gap:10px}}
.ms-row:last-child{{border-bottom:none}}
.ms-name{{font-size:14px;color:var(--ink2);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.ms-words{{font-size:14px;color:var(--amber);flex-shrink:0;font-weight:600}}

/* ── Cron rows ── */
.cron-row{{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid var(--line)}}
.cron-row:last-child{{border-bottom:none}}
.cron-script{{font-size:14px;color:var(--amber);font-weight:600}}
.cron-sched{{font-size:13px;color:var(--ink3)}}

/* ── Session rows ── */
.q-row{{display:flex;align-items:baseline;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}}
.q-row:last-child{{border-bottom:none}}
.q-arrow{{color:var(--amber);flex-shrink:0;font-size:16px}}
.q-text{{flex:1;font-size:14px;color:var(--ink2);line-height:1.3}}
.q-ts{{font-size:12px;color:var(--ink3);flex-shrink:0;white-space:nowrap}}

/* ── Token / cost panels ── */
.tok-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;margin-bottom:14px}}
.tok-cell{{background:var(--bg3);padding:12px 14px;text-align:center}}
.tok-val{{font-size:20px;font-weight:700;color:var(--amber)}}
.tok-lbl{{font-size:11px;color:var(--ink3);letter-spacing:.1em;text-transform:uppercase;margin-top:4px}}
.cost-row{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;margin-top:14px}}
.cost-cell{{background:var(--bg3);padding:12px 14px;text-align:center}}
.cost-val{{font-size:18px;font-weight:700;color:var(--green)}}
.cost-lbl{{font-size:11px;color:var(--ink3);letter-spacing:.1em;text-transform:uppercase;margin-top:4px}}

/* ── Ring legend ── */
.ring-legend{{display:flex;flex-direction:column;gap:7px;flex:1;min-width:160px}}
.ring-leg-row{{display:flex;align-items:center;gap:10px;font-size:14px}}
.ring-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.ring-leg-label{{flex:1;color:var(--ink2)}}
.ring-leg-val{{color:var(--amber);font-size:14px;font-weight:600}}

/* ── Misc ── */
.empty{{font-size:13px;color:var(--ink3);font-style:italic;padding:8px 0}}
.gen-note{{font-size:12px;color:var(--ink3);margin-top:26px;text-align:center;letter-spacing:.04em}}

@media(max-width:900px){{
  .sidebar{{display:none}}
  .grid-home,.grid-2,.grid-3{{grid-template-columns:1fr}}
  .col-span-2{{grid-column:span 1}}
}}
</style>
</head>
<body>

<div class="header">
  <div class="header-title">Research RAG &mdash; Research Dashboard</div>
  <div class="header-meta">
    <span>John Markoff</span>
    <span>{now_str}</span>
  </div>
</div>

<div class="shell">
  <nav class="sidebar">
    <div class="nav-label">Navigate</div>
    <div class="nav-item active" onclick="showPage('home',this)"><span class="nav-icon">&#8962;</span> Home</div>
    <div class="nav-label">Categories</div>
    <div class="nav-item" onclick="showPage('work',this)"><span class="nav-icon">&#9998;</span> Work</div>
    <div class="nav-item" onclick="showPage('hermes',this)"><span class="nav-icon">&#9889;</span> Hermes &amp; Cron</div>
    <div class="nav-item" onclick="showPage('resources',this)"><span class="nav-icon">&#9672;</span> Resources</div>
    <div class="nav-item" onclick="showPage('corpus',this)"><span class="nav-icon">&#9636;</span> Corpus</div>
  </nav>

  <main class="main">

    <!-- HOME -->
    <div id="page-home" class="page active">
      <div class="page-title">Overview &mdash; session summary</div>
      <div class="grid grid-home">

        <div class="tile" style="grid-row:span 2">
          <div class="tile-head">
            <span class="tile-title">Recent Memos</span>
            <span class="tile-badge">{total_memos_int} total</span>
          </div>
          <div class="tile-body">{memo_home}</div>
        </div>

        <div class="tile">
          <div class="tile-head">
            <span class="tile-title">Token Cost</span>
            <span class="tile-badge">last 30 days</span>
          </div>
          <div class="tile-body">
            <div class="tok-row">
              <div class="tok-cell"><div class="tok-val">{tok_today}</div><div class="tok-lbl">Today</div></div>
              <div class="tok-cell"><div class="tok-val">{tok_week}</div><div class="tok-lbl">This Week</div></div>
              <div class="tok-cell"><div class="tok-val">{tok_total}</div><div class="tok-lbl">All Time</div></div>
            </div>
            <canvas id="cost-canvas-home" height="80"></canvas>
            <div class="cost-row">
              <div class="cost-cell"><div class="cost-val">{cost_today}</div><div class="cost-lbl">Today</div></div>
              <div class="cost-cell"><div class="cost-val">{cost_week}</div><div class="cost-lbl">This Week</div></div>
              <div class="cost-cell"><div class="cost-val">{cost_total}</div><div class="cost-lbl">All Time</div></div>
            </div>
          </div>
        </div>

        <div class="tile">
          <div class="tile-head"><span class="tile-title">Hermes Activity</span></div>
          <div class="tile-body" style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
            <div>
              <div class="stat-sub">Questions Asked</div>
              <div class="stat-big amber">{questions_asked}</div>
            </div>
            <div>
              <div class="stat-sub">Memos Generated</div>
              <div class="stat-big">{memos_generated}</div>
            </div>
          </div>
        </div>

        <div class="tile">
          <div class="tile-head"><span class="tile-title">Documents in Corpus</span></div>
          <div class="tile-body">
            <div class="stat-big amber">{corpus_count}</div>
            <div class="stat-sub">source documents indexed</div>
          </div>
        </div>

        <div class="tile">
          <div class="tile-head"><span class="tile-title">Total Words in Corpus</span></div>
          <div class="tile-body">
            <div class="stat-big">{corpus_words}</div>
            <div class="stat-sub">words across all source files</div>
          </div>
        </div>

        <div class="tile">
          <div class="tile-head"><span class="tile-title">Word Count</span></div>
          <div class="tile-body">
            <div class="stat-big blue">{ms_words}</div>
            <div class="stat-sub">words written in manuscript</div>
          </div>
        </div>

      </div>
      <div class="gen-note">Generated {now_str} &nbsp;&middot;&nbsp; corpus.db &nbsp;&middot;&nbsp; state.db</div>
    </div>

    <!-- WORK -->
    <div id="page-work" class="page">
      <div class="page-title">Work &mdash; author documents &amp; manuscript</div>
      <div class="grid grid-2">
        <div class="tile col-span-2">
          <div class="tile-head">
            <span class="tile-title">Recent Memos</span>
            <span class="tile-badge">{total_memos_int} total &mdash; click to expand</span>
          </div>
          <div class="tile-body">{memo_work}</div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Word Count</span><span class="tile-badge">Manuscript files</span></div>
          <div class="tile-body">
            <div class="stat-big blue" style="margin-bottom:16px">{ms_words}</div>
            {ms_files}
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Research Memos Generated</span></div>
          <div class="tile-body">
            <div class="stat-big amber">{total_memos}</div>
            <div class="stat-sub">total memos in Work/Research Memos</div>
          </div>
        </div>
      </div>
    </div>

    <!-- HERMES & CRON -->
    <div id="page-hermes" class="page">
      <div class="page-title">Hermes &amp; Cron &mdash; agent activity &amp; automation</div>
      <div class="grid grid-2">
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Questions Asked</span></div>
          <div class="tile-body">
            <div class="stat-big amber">{questions_asked}</div>
            <div class="stat-sub">total Hermes sessions</div>
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Research Memos Generated</span></div>
          <div class="tile-body">
            <div class="stat-big">{memos_generated}</div>
            <div class="stat-sub">via cron or session</div>
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Average Time to Answer</span></div>
          <div class="tile-body">
            <div class="stat-big">{avg_secs}</div>
            <div class="stat-sub">mean session duration</div>
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Average Tokens per Query</span></div>
          <div class="tile-body">
            <div class="stat-big amber">{avg_tokens}</div>
            <div class="stat-sub">input + output per session</div>
          </div>
        </div>
        <div class="tile col-span-2">
          <div class="tile-head"><span class="tile-title">Recent Sessions</span></div>
          <div class="tile-body">{questions_markup}</div>
        </div>
        <div class="tile col-span-2">
          <div class="tile-head"><span class="tile-title">Scheduled Cron Jobs</span></div>
          <div class="tile-body">{cron_markup}</div>
        </div>
      </div>
    </div>

    <!-- RESOURCES -->
    <div id="page-resources" class="page">
      <div class="page-title">Resources &mdash; token usage &amp; API cost</div>
      <div class="grid grid-2">
        <div class="tile col-span-2">
          <div class="tile-head">
            <span class="tile-title">Token Cost</span>
            <span class="tile-badge">30-day daily breakdown</span>
          </div>
          <div class="tile-body">
            <div class="tok-row">
              <div class="tok-cell"><div class="tok-val">{tok_today}</div><div class="tok-lbl">Tokens Today</div></div>
              <div class="tok-cell"><div class="tok-val">{tok_week}</div><div class="tok-lbl">Tokens This Week</div></div>
              <div class="tok-cell"><div class="tok-val">{tok_total}</div><div class="tok-lbl">Tokens All Time</div></div>
            </div>
            <canvas id="cost-canvas-resources" height="130"></canvas>
            <div class="cost-row">
              <div class="cost-cell"><div class="cost-val">{cost_today}</div><div class="cost-lbl">USD Today</div></div>
              <div class="cost-cell"><div class="cost-val">{cost_week}</div><div class="cost-lbl">USD This Week</div></div>
              <div class="cost-cell"><div class="cost-val">{cost_total}</div><div class="cost-lbl">USD All Time</div></div>
            </div>
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Average Tokens per Query</span></div>
          <div class="tile-body">
            <div class="stat-big amber">{avg_tokens}</div>
            <div class="stat-sub">mean tokens per Hermes session</div>
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Average Time to Answer</span></div>
          <div class="tile-body">
            <div class="stat-big">{avg_secs}</div>
            <div class="stat-sub">mean session response time</div>
          </div>
        </div>
      </div>
    </div>

    <!-- CORPUS -->
    <div id="page-corpus" class="page">
      <div class="page-title">Corpus &mdash; source documents &amp; index</div>
      <div class="grid grid-2">
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Documents in Corpus</span></div>
          <div class="tile-body">
            <div class="stat-big amber">{corpus_count}</div>
            <div class="stat-sub">indexed source documents</div>
          </div>
        </div>
        <div class="tile">
          <div class="tile-head"><span class="tile-title">Total Words in Corpus</span></div>
          <div class="tile-body">
            <div class="stat-big">{corpus_words}</div>
            <div class="stat-sub">across all source files</div>
          </div>
        </div>
        <div class="tile col-span-2">
          <div class="tile-head">
            <span class="tile-title">Corpus Breakdown</span>
            <span class="tile-badge">by source type</span>
          </div>
          <div class="tile-body" style="display:flex;gap:36px;align-items:center;flex-wrap:wrap">
            <canvas id="ring-canvas" width="180" height="180"></canvas>
            <div id="ring-legend" class="ring-legend"></div>
          </div>
        </div>
        <div class="tile col-span-2">
          <div class="tile-head">
            <span class="tile-title">Recently Added</span>
            <span class="tile-badge">5 most recent ingests &mdash; click to expand</span>
          </div>
          <div class="tile-body">{recent_docs}</div>
        </div>
      </div>
    </div>

  </main>
</div>

<script>
const RING_DATA  = {ring_json};
const DAILY_DATA = {daily_json};

function showPage(id, el) {{
  document.querySelectorAll('.page').forEach(function(p) {{ p.classList.remove('active'); }});
  document.querySelectorAll('.nav-item').forEach(function(n) {{ n.classList.remove('active'); }});
  document.getElementById('page-' + id).classList.add('active');
  el.classList.add('active');
  if (id === 'resources') drawCostChart('cost-canvas-resources', 130);
  if (id === 'corpus')    drawRingChart();
}}

function toggleExpand(el) {{
  el.classList.toggle('open');
  var hint = el.querySelector('.expand-hint');
  if (hint) hint.textContent = el.classList.contains('open') ? '\u25b2 collapse' : '\u25bc expand';
}}

function drawRingChart() {{
  var canvas = document.getElementById('ring-canvas');
  if (!canvas || !RING_DATA.length) return;
  var ctx = canvas.getContext('2d');
  var cx = 90, cy = 90, R = 72, r = 42;
  ctx.clearRect(0, 0, 180, 180);
  var start = -Math.PI / 2;
  var total = RING_DATA.reduce(function(s, d) {{ return s + d.count; }}, 0);
  RING_DATA.forEach(function(seg) {{
    var sweep = (seg.count / total) * 2 * Math.PI;
    ctx.beginPath();
    ctx.moveTo(cx + R * Math.cos(start), cy + R * Math.sin(start));
    ctx.arc(cx, cy, R, start, start + sweep);
    ctx.arc(cx, cy, r, start + sweep, start, true);
    ctx.closePath();
    ctx.fillStyle = seg.color;
    ctx.fill();
    start += sweep;
  }});
  ctx.fillStyle = '#1a1714';
  ctx.font = 'bold 20px -apple-system,monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(total.toLocaleString(), cx, cy - 7);
  ctx.font = '12px -apple-system,monospace';
  ctx.fillStyle = '#706860';
  ctx.fillText('documents', cx, cy + 12);
  var legend = document.getElementById('ring-legend');
  if (legend && legend.children.length === 0) {{
    RING_DATA.forEach(function(seg) {{
      var row = document.createElement('div');
      row.className = 'ring-leg-row';
      row.innerHTML = '<div class="ring-dot" style="background:' + seg.color + '"></div>'
        + '<span class="ring-leg-label">' + seg.label + '</span>'
        + '<span class="ring-leg-val">' + seg.count.toLocaleString()
        + ' <span style="color:#706860;font-size:12px">' + seg.pct + '%</span></span>';
      legend.appendChild(row);
    }});
  }}
}}

function drawCostChart(canvasId, height) {{
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;
  var dpr = window.devicePixelRatio || 1;
  var W   = canvas.parentElement.offsetWidth;
  canvas.width  = W * dpr;
  canvas.height = height * dpr;
  canvas.style.width  = W + 'px';
  canvas.style.height = height + 'px';
  var ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  if (!DAILY_DATA.length) {{
    ctx.fillStyle = '#706860';
    ctx.font = '13px -apple-system,monospace';
    ctx.textAlign = 'center';
    ctx.fillText('No cost data available', W / 2, height / 2);
    return;
  }}
  var pad = {{top:12, right:18, bottom:34, left:64}};
  var cw  = W - pad.left - pad.right;
  var ch  = height - pad.top - pad.bottom;
  var costs   = DAILY_DATA.map(function(d) {{ return d.cost; }});
  var labels  = DAILY_DATA.map(function(d) {{ return d.day.slice(5); }});
  var maxCost = Math.max.apply(null, costs.concat([0.01]));
  ctx.strokeStyle = '#d0cbc0';
  ctx.lineWidth = 1;
  [0, 0.5, 1].forEach(function(f) {{
    var y = pad.top + ch * (1 - f);
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + cw, y); ctx.stroke();
    ctx.fillStyle = '#706860';
    ctx.font = '11px -apple-system,monospace';
    ctx.textAlign = 'right';
    ctx.fillText('$' + (maxCost * f).toFixed(2), pad.left - 8, y + 4);
  }});
  var bw = Math.max(4, (cw / DAILY_DATA.length) - 3);
  DAILY_DATA.forEach(function(d, i) {{
    var x  = pad.left + i * (cw / DAILY_DATA.length) + (cw / DAILY_DATA.length - bw) / 2;
    var bh = ch * (d.cost / maxCost);
    var y  = pad.top + ch - bh;
    var g  = ctx.createLinearGradient(0, y, 0, y + bh);
    g.addColorStop(0, '#b05a00');
    g.addColorStop(1, '#e8b060');
    ctx.fillStyle = g;
    ctx.fillRect(x, y, bw, bh);
  }});
  ctx.fillStyle = '#706860';
  ctx.font = '11px -apple-system,monospace';
  ctx.textAlign = 'center';
  labels.forEach(function(lbl, i) {{
    if (i % 5 === 0 || i === labels.length - 1) {{
      var x = pad.left + i * (cw / DAILY_DATA.length) + (cw / DAILY_DATA.length) / 2;
      ctx.fillText(lbl, x, height - 8);
    }}
  }});
}}

window.addEventListener('load', function() {{
  drawCostChart('cost-canvas-home', 80);
}});
window.addEventListener('resize', function() {{
  drawCostChart('cost-canvas-home', 80);
  if (document.getElementById('page-resources').classList.contains('active'))
    drawCostChart('cost-canvas-resources', 130);
}});
</script>
</body>
</html>"""


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Research RAG Dashboard Generator")
    parser.add_argument("--config", default="Scripts/config.ini")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[dashboard] Config not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    cfg   = load_config(args.config)
    now   = datetime.now()

    print("[dashboard] Reading corpus.db ...")
    corp  = corpus_data(cfg["corpus_db"])

    print("[dashboard] Reading state.db ...")
    state = state_data(cfg["state_db"])

    print("[dashboard] Reading crontab ...")
    cron  = get_cron_jobs()

    print("[dashboard] Building HTML ...")
    html  = build_html(corp, state, cron, now, work_root=cfg.get("work_root", ""))

    out   = args.output or os.path.join(cfg["project_root"], "dashboard.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[dashboard] Done: {out}")
    print(f"[dashboard]   corpus : {corp.get('corpus_count', 0):,} docs / {corp.get('corpus_words', 0):,} words")
    print(f"[dashboard]   state  : {state.get('questions_asked', 0):,} sessions / ${state.get('cost_total', 0):.2f} total cost")


if __name__ == "__main__":
    main()
