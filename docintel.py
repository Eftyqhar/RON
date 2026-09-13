"""docintel.py — RON Smart PDF & Document Intelligence Engine

Autonomous document analysis, executive synthesis, tabular data extraction,
and context-aware Q&A for PDFs, Word documents, and financial CSV spreadsheets.

Capabilities:
1. Multi-format Ingestion:
   - PDF (via PyMuPDF/fitz & pdfplumber for high-precision table extraction)
   - Word Documents (.docx via XML element tree parsing)
   - Spreadsheets (.csv with automatic numerical column summarization)
   - Plain text and Markdown (.txt, .md)
2. Autonomous Executive Digest:
   - 5 critical actionable takeaways
   - Core document premise
   - Key figures, deadlines, risk/warranty terms
   - 30-second spoken debrief for voice synthesis
3. Interactive Document Q&A & Calculations:
   - Direct answers with page/section citations
   - Mathematical calculations (total expenses, tax sums, averages)
4. Table Extraction & CSV Export:
   - Extracts all detected tables into CSV files saved in Documents/RON_Extracted_Tables/
5. Event Bus Integration:
   - Broadcasts real-time events to HUD (`bus.docintel`)
"""

import base64
import csv
import datetime
import io
import json
import os
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

import bus

# External PDF libraries (verified available)
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

# Cache & Storage setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "data", "docintel_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

TABLES_EXPORT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "RON_Extracted_Tables")
os.makedirs(TABLES_EXPORT_DIR, exist_ok=True)

RAG_FOLDER = os.path.join(os.path.expanduser("~"), "Documents", "RON_RAG")
os.makedirs(RAG_FOLDER, exist_ok=True)

_lock = threading.Lock()
_active_doc: Optional[Dict[str, Any]] = None
_docs_registry: Dict[str, Dict[str, Any]] = {}


def _get_client_and_model():
    """Retrieve OpenAI client and model configured in main.py, or fallback."""
    try:
        import main
        if getattr(main, "client", None):
            return main.client, getattr(main, "MODEL", "auto")
    except Exception:
        pass

    try:
        from openai import OpenAI
        api_key = os.getenv("OPENAI_API_KEY", "sk-lVxhYKO3KhvxTgWmVZDOA5Ete9VixiTaOFzI15rdxjk7qmUX")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.hcnsec.cn/v1")
        return OpenAI(api_key=api_key, base_url=base_url), "auto"
    except Exception as e:
        print(f"[docintel] Failed to initialize AI client: {e}")
        return None, "auto"


# ---------------------------------------------------------------------------
# Document Ingestion & Parsers
# ---------------------------------------------------------------------------

def parse_document(
    file_path: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    filename: str = "document.pdf"
) -> Dict[str, Any]:
    """Parse document bytes or file path into a structured document record."""
    if not file_bytes and file_path and os.path.isfile(file_path):
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()

    if not file_bytes:
        raise ValueError("No document bytes or valid file path provided")

    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    if not ext:
        ext = "pdf"

    doc_id = str(uuid.uuid4())[:8]
    bus.activity(f"Document Intelligence: Ingesting {filename}...", "pending")
    bus.docintel(status="INGESTING", filename=filename, progress=10, event="parsing")

    full_text = ""
    page_texts: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {"filename": filename, "ext": ext, "size_bytes": len(file_bytes)}
    num_summary: Dict[str, Any] = {}

    if ext == "pdf":
        full_text, page_texts, tables, pdf_meta = _parse_pdf(file_bytes)
        metadata.update(pdf_meta)
    elif ext in ("docx", "doc"):
        full_text, page_texts, tables, docx_meta = _parse_docx(file_bytes)
        metadata.update(docx_meta)
    elif ext in ("csv", "tsv"):
        full_text, page_texts, tables, num_summary = _parse_csv(file_bytes, filename)
    elif ext in ("txt", "md", "json", "log"):
        full_text, page_texts = _parse_plain_text(file_bytes)
    else:
        # Fallback: attempt utf-8 decode
        try:
            full_text = file_bytes.decode("utf-8", errors="replace")
            page_texts = [{"page": 1, "text": full_text}]
        except Exception as e:
            full_text = f"Binary content ({len(file_bytes)} bytes)"
            page_texts = [{"page": 1, "text": full_text}]

    word_count = len(re.findall(r"\\b\\w+\\b", full_text))
    page_count = len(page_texts) if page_texts else 1

    doc_record = {
        "id": doc_id,
        "filename": filename,
        "ext": ext,
        "pages": page_count,
        "words": word_count,
        "text": full_text,
        "page_texts": page_texts,
        "tables": tables,
        "table_count": len(tables),
        "metadata": metadata,
        "numerical_summary": num_summary,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "digest": None,
        "qa_history": [],
    }

    with _lock:
        global _active_doc
        _active_doc = doc_record
        _docs_registry[doc_id] = doc_record

    # Save to cache
    try:
        cache_path = os.path.join(CACHE_DIR, f"{doc_id}.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            lite = dict(doc_record)
            if len(lite["text"]) > 200000:
                lite["text"] = lite["text"][:200000] + "\\n...[truncated for cache]"
            json.dump(lite, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[docintel] Cache save warning: {e}")

    bus.activity(f"Document Ingested: {filename} ({page_count} pages, {len(tables)} tables)", "ok")
    bus.docintel(
        status="PARSED",
        filename=filename,
        doc_id=doc_id,
        pages=page_count,
        words=word_count,
        table_count=len(tables),
        progress=50
    )

    return doc_record


def _parse_pdf(file_bytes: bytes) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """High-speed text and table extraction from PDF bytes."""
    full_text_parts = []
    page_texts = []
    pdf_meta = {}
    tables = []

    if fitz:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            pdf_meta = {
                "title": doc.metadata.get("title") or "",
                "author": doc.metadata.get("author") or "",
                "subject": doc.metadata.get("subject") or "",
                "creator": doc.metadata.get("creator") or "",
                "producer": doc.metadata.get("producer") or "",
            }
            for i, page in enumerate(doc):
                txt = page.get_text("text") or ""
                clean_txt = txt.strip()
                page_texts.append({"page": i + 1, "text": clean_txt})
                if clean_txt:
                    full_text_parts.append(f"--- PAGE {i + 1} ---\\n{clean_txt}")
            doc.close()
        except Exception as e:
            print(f"[docintel] fitz extraction error: {e}")

    if pdfplumber:
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                for i, page in enumerate(pdf.pages):
                    extracted = page.extract_tables() or []
                    for t_idx, tbl in enumerate(extracted):
                        if not tbl or len(tbl) < 2:
                            continue
                        cleaned_rows = []
                        for row in tbl:
                            cleaned_rows.append([str(c or "").strip() for c in row])
                        headers = [h or f"Col_{c+1}" for c, h in enumerate(cleaned_rows[0])]
                        data_rows = cleaned_rows[1:]
                        if data_rows:
                            tables.append({
                                "id": len(tables) + 1,
                                "page": i + 1,
                                "headers": headers,
                                "rows": data_rows,
                                "row_count": len(data_rows),
                                "col_count": len(headers)
                            })
        except Exception as e:
            print(f"[docintel] pdfplumber table extraction warning: {e}")

    full_text = "\\n\\n".join(full_text_parts)
    return full_text, page_texts, tables, pdf_meta


def _parse_docx(file_bytes: bytes) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Zero-dependency docx parser via zipfile & XML element tree."""
    import zipfile
    full_text = ""
    page_texts = []
    tables = []
    meta = {}

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            xml_content = z.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            paragraphs = []
            for p in tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                texts = [node.text for node in p.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if node.text]
                if texts:
                    paragraphs.append("".join(texts))
            
            full_text = "\\n".join(paragraphs)
            page_texts.append({"page": 1, "text": full_text})

            for t_idx, tbl in enumerate(tree.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl")):
                rows_data = []
                for tr in tbl.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr"):
                    row = []
                    for tc in tr.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc"):
                        cell_texts = [node.text for node in tc.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t") if node.text]
                        row.append(" ".join(cell_texts).strip())
                    if row:
                        rows_data.append(row)
                if len(rows_data) >= 2:
                    headers = rows_data[0]
                    tables.append({
                        "id": len(tables) + 1,
                        "page": 1,
                        "headers": headers,
                        "rows": rows_data[1:],
                        "row_count": len(rows_data) - 1,
                        "col_count": len(headers)
                    })

    except Exception as e:
        print(f"[docintel] docx parse error: {e}")
        full_text = "Failed to parse DOCX structure."
        page_texts.append({"page": 1, "text": full_text})

    return full_text, page_texts, tables, meta


def _parse_csv(file_bytes: bytes, filename: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Parse CSV / TSV spreadsheet with statistical column calculations."""
    text_data = ""
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text_data = file_bytes.decode(enc)
            break
        except Exception:
            continue

    lines = [l for l in text_data.splitlines() if l.strip()]
    if not lines:
        return "", [], [], {}

    delim = ","
    if "\\t" in lines[0]:
        delim = "\\t"
    elif ";" in lines[0] and lines[0].count(";") > lines[0].count(","):
        delim = ";"

    reader = csv.reader(lines, delimiter=delim)
    all_rows = list(reader)
    if not all_rows:
        return "", [], [], {}

    headers = [h.strip() or f"Col_{i+1}" for i, h in enumerate(all_rows[0])]
    data_rows = all_rows[1:]

    num_summary = {}
    col_totals = {}
    for c_idx, head in enumerate(headers):
        values = []
        for r in data_rows:
            if c_idx < len(r):
                val_str = re.sub(r"[^\\d\\.\\-]", "", r[c_idx].strip())
                try:
                    values.append(float(val_str))
                except Exception:
                    pass
        if len(values) >= max(1, len(data_rows) * 0.4):
            total = sum(values)
            col_totals[head] = {
                "sum": round(total, 2),
                "avg": round(total / len(values), 2),
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "count": len(values)
            }

    num_summary["columns"] = col_totals
    num_summary["row_count"] = len(data_rows)
    num_summary["col_count"] = len(headers)

    full_text = f"CSV Spreadsheet: {filename}\\nRows: {len(data_rows)}, Columns: {len(headers)}\\nHeaders: {', '.join(headers)}\\n\\n"
    for r in data_rows[:15]:
        full_text += " | ".join(r) + "\\n"
    if len(data_rows) > 15:
        full_text += f"\\n... [{len(data_rows) - 15} additional rows not shown in preview]"

    page_texts = [{"page": 1, "text": full_text}]
    tables = [{
        "id": 1,
        "page": 1,
        "headers": headers,
        "rows": data_rows,
        "row_count": len(data_rows),
        "col_count": len(headers),
        "title": filename
    }]

    return full_text, page_texts, tables, num_summary


def _parse_plain_text(file_bytes: bytes) -> Tuple[str, List[Dict[str, Any]]]:
    """Parse UTF-8 or CP1252 text content."""
    text_data = ""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            text_data = file_bytes.decode(enc)
            break
        except Exception:
            continue
    page_texts = [{"page": 1, "text": text_data}]
    return text_data, page_texts


# ---------------------------------------------------------------------------
# Autonomous Executive Digest
# ---------------------------------------------------------------------------

def generate_executive_digest(doc_id: Optional[str] = None) -> Dict[str, Any]:
    """Generate 5-point actionable executive digest and spoken debrief."""
    with _lock:
        target = _docs_registry.get(doc_id) if doc_id else _active_doc
        if not target:
            raise ValueError("No active document loaded to summarize")

    bus.activity(f"Document Intelligence: Generating Executive Digest for {target['filename']}...", "pending")
    bus.docintel(status="ANALYZING", filename=target["filename"], progress=75)

    client, model = _get_client_and_model()
    if not client:
        fallback_digest = {
            "premise": f"Document '{target['filename']}' containing {target['pages']} pages and {target['words']} words.",
            "takeaways": [
                f"Contains {target['pages']} page(s) and {target['words']} words across {target['table_count']} structured table(s).",
                f"File format: {target['ext'].upper()} ingested on {target['created_at']}.",
                "Tabular data is extracted and ready for CSV download.",
                "Direct question answering and calculation queries are enabled.",
                "Executive AI synthesis requires active API connection."
            ],
            "critical_data": f"Total Tables: {target['table_count']} | Pages: {target['pages']} | Format: {target['ext'].upper()}",
            "spoken_debrief": f"Document {target['filename']} loaded successfully. Verified {target['pages']} pages and {target['table_count']} structured tables. Ready for your questions, Sir."
        }
        target["digest"] = fallback_digest
        bus.docintel(status="READY", doc_id=target["id"], digest=fallback_digest, progress=100, event="show_overlay")
        return fallback_digest

    sample_text = target["text"][:28000]
    tables_summary = ""
    if target["tables"]:
        tables_summary = f"\\nEXTRACTED TABLES ({len(target['tables'])} total):\\n"
        for t in target["tables"][:3]:
            tables_summary += f"- Table on Page {t['page']}: Headers: {', '.join(t['headers'])}, Rows: {t['row_count']}\\n"

    num_context = ""
    if target.get("numerical_summary") and target["numerical_summary"].get("columns"):
        num_context = f"\\nNUMERICAL COLUMN STATISTICS:\\n{json.dumps(target['numerical_summary']['columns'], indent=2)}\\n"

    system_prompt = (
        "You are R.O.N.'s Autonomous Document Intelligence Engine. You analyze complex technical manuals, "
        "legal contracts, research papers, and financial bank statements. Extract clear, actionable, high-signal insights.\\n"
        "Respond ONLY with a valid JSON object matching this exact schema:\\n"
        "{\\n"
        '  "premise": "1 clear sentence summarizing the fundamental purpose and author/entity of the document.",\\n'
        '  "takeaways": [\\n'
        '    "1. High-impact takeaway point.",\\n'
        '    "2. High-impact takeaway point.",\\n'
        '    "3. High-impact takeaway point.",\\n'
        '    "4. High-impact takeaway point.",\\n'
        '    "5. High-impact takeaway point."\\n'
        "  ],\\n"
        '  "critical_data": "Key numerical metrics, financial sums, deadlines, contract liabilities, or warranty terms in 2-3 lines.",\\n'
        '  "spoken_debrief": "A professional 30-second spoken debrief for R.O.N. addressing the user as Sir. Natural cadence for speech synthesis."\\n'
        "}"
    )

    user_prompt = (
        f"Analyze the following document:\\n"
        f"FILENAME: {target['filename']}\\n"
        f"TOTAL PAGES: {target['pages']}\\n"
        f"WORD COUNT: {target['words']}\\n"
        f"{tables_summary}\\n"
        f"{num_context}\\n"
        f"DOCUMENT CONTENT EXCERPT:\\n{sample_text}\\n"
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        raw_json = resp.choices[0].message.content or "{}"
        digest = json.loads(raw_json)
    except Exception as e:
        print(f"[docintel] AI synthesis error: {e}")
        digest = {
            "premise": f"Document '{target['filename']}' ({target['pages']} pages).",
            "takeaways": [
                f"Total Pages: {target['pages']} | Word Count: {target['words']}",
                f"Extracted Tables: {target['table_count']}",
                "Automated synthesis experienced API timeout; raw reader is available.",
                "Direct section search is active.",
                "Tabular data ready for CSV export."
            ],
            "critical_data": f"Tables: {target['table_count']}",
            "spoken_debrief": f"Document {target['filename']} parsed successfully with {target['pages']} pages and {target['table_count']} tables. Ready for queries, Sir."
        }

    target["digest"] = digest
    bus.activity(f"Document Digest complete for {target['filename']}", "ok")
    bus.docintel(
        status="READY",
        doc_id=target["id"],
        filename=target["filename"],
        pages=target["pages"],
        words=target["words"],
        table_count=target["table_count"],
        digest=digest,
        progress=100,
        event="show_overlay",
        open=True
    )
    return digest


# ---------------------------------------------------------------------------
# Interactive Document Q&A & Calculations
# ---------------------------------------------------------------------------

def ask_document(query: str, doc_id: Optional[str] = None) -> Dict[str, Any]:
    """Answer user questions with citations, or execute financial math calculations."""
    q = (query or "").strip()
    if not q:
        return {"error": "Empty question provided", "answer": "Please ask a specific question, Sir."}

    with _lock:
        target = _docs_registry.get(doc_id) if doc_id else _active_doc
        if not target:
            return {"error": "No document loaded", "answer": "No active document is currently loaded in the HUD, Sir."}

    client, model = _get_client_and_model()
    if not client:
        return {
            "answer": "AI Engine is offline. Cannot query document content without active API connection.",
            "spoken_answer": "AI client is offline, Sir. Please verify network connectivity."
        }

    bus.activity(f"Doc Intel: Answering '{q[:40]}'...", "pending")

    context_chunks = []
    if target["tables"]:
        context_chunks.append("=== EXTRACTED TABLES ===")
        for t in target["tables"]:
            context_chunks.append(f"Table ID {t['id']} (Page {t['page']}):\\nHeaders: {', '.join(t['headers'])}")
            for row in t["rows"][:30]:
                context_chunks.append(" | ".join(row))

    context_chunks.append("=== DOCUMENT TEXT ===")
    context_chunks.append(target["text"][:35000])

    full_context = "\\n".join(context_chunks)

    system_prompt = (
        "You are R.O.N.'s Expert Document Intelligence Analyst. You answer questions directly based on the provided document text and tables.\\n"
        "Guidelines:\\n"
        "1. Cite exact Page Numbers, Sections, and Table IDs where your findings come from.\\n"
        "2. If the user asks for a calculation (e.g. total expenses, sums, taxes, differences), show the step-by-step arithmetic clearly and provide the exact total.\\n"
        "3. If the answer is not mentioned in the document, explicitly say so rather than speculating.\\n"
        "4. Respond with a JSON object:\\n"
        "{\\n"
        '  "answer": "Detailed Markdown answer with clear bullets and mathematical calculations.",\\n'
        '  "citations": ["Page 4, Section 2.1", "Table 1 (Page 2)"],\\n'
        '  "spoken_answer": "Crisp 2-sentence spoken answer suitable for R.O.N. to speak to the user as Sir."\\n'
        "}"
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Document: {target['filename']}\\n\\nContext:\\n{full_context}\\n\\nQuestion: {q}"}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        res_data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        print(f"[docintel] Q&A error: {e}")
        res_data = {
            "answer": f"Unable to process query due to error: {e}",
            "citations": [],
            "spoken_answer": "I encountered an error analyzing that document section, Sir."
        }

    qa_entry = {
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "question": q,
        "answer": res_data.get("answer", ""),
        "citations": res_data.get("citations", []),
        "spoken_answer": res_data.get("spoken_answer", "")
    }

    with _lock:
        target["qa_history"].append(qa_entry)

    bus.activity(f"Doc Intel: Answered query on {target['filename']}", "ok")
    bus.docintel(
        event="qa_answered",
        doc_id=target["id"],
        qa=qa_entry
    )

    return res_data


# ---------------------------------------------------------------------------
# Table Extraction & CSV Export
# ---------------------------------------------------------------------------

def export_tables_to_csv(doc_id: Optional[str] = None, output_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Export all extracted tables in document to clean CSV files."""
    with _lock:
        target = _docs_registry.get(doc_id) if doc_id else _active_doc
        if not target:
            raise ValueError("No active document loaded")

    tables = target.get("tables", [])
    if not tables:
        return []

    target_dir = output_dir or TABLES_EXPORT_DIR
    os.makedirs(target_dir, exist_ok=True)

    base_name = os.path.splitext(target["filename"])[0]
    exported_files = []

    for idx, t in enumerate(tables):
        csv_filename = f"{base_name}_table_{t.get('id', idx+1)}_p{t.get('page', 1)}.csv"
        csv_path = os.path.join(target_dir, csv_filename)

        try:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if t.get("headers"):
                    writer.writerow(t["headers"])
                for r in t.get("rows", []):
                    writer.writerow(r)

            exported_files.append({
                "table_id": t.get("id", idx + 1),
                "page": t.get("page", 1),
                "filename": csv_filename,
                "path": csv_path,
                "rows": t.get("row_count", len(t.get("rows", []))),
                "cols": t.get("col_count", len(t.get("headers", [])))
            })
        except Exception as e:
            print(f"[docintel] Failed to export table {t.get('id')}: {e}")

    bus.activity(f"Exported {len(exported_files)} table(s) to CSV in Documents", "ok")
    return exported_files


# ---------------------------------------------------------------------------
# State Getters
# ---------------------------------------------------------------------------

def get_active_document() -> Optional[Dict[str, Any]]:
    """Return lightweight snapshot of currently active document."""
    with _lock:
        if not _active_doc:
            return None
        return {
            "id": _active_doc["id"],
            "filename": _active_doc["filename"],
            "ext": _active_doc["ext"],
            "pages": _active_doc["pages"],
            "words": _active_doc["words"],
            "table_count": _active_doc["table_count"],
            "tables": _active_doc["tables"],
            "digest": _active_doc["digest"],
            "numerical_summary": _active_doc["numerical_summary"],
            "qa_history": _active_doc["qa_history"][-10:],
            "created_at": _active_doc["created_at"],
            "preview_text": _active_doc["text"][:3000]
        }


def get_document_by_id(doc_id: str) -> Optional[Dict[str, Any]]:
    """Return document by ID."""
    with _lock:
        return _docs_registry.get(doc_id)


# ---------------------------------------------------------------------------
# Autonomous Folder Watcher (RON_RAG)
# ---------------------------------------------------------------------------

RAG_INDEX_FILE = os.path.join(CACHE_DIR, "rag_watcher_index.json")


def _load_rag_index() -> Dict[str, float]:
    """Load previously recorded file modification times."""
    try:
        if os.path.isfile(RAG_INDEX_FILE):
            with open(RAG_INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): float(v) for k, v in data.items()}
    except Exception as e:
        print(f"[docintel] Could not read rag index: {e}")
    return {}


def _save_rag_index(data: Dict[str, float]):
    """Save recorded file modification times to disk cache."""
    try:
        with open(RAG_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[docintel] Could not write rag index: {e}")


_watcher_thread: Optional[threading.Thread] = None
_watcher_stop = threading.Event()
_processed_files: Dict[str, float] = _load_rag_index()


def initialize_rag_baseline(target_dir: Optional[str] = None):
    """Scan RAG folder on startup to mark all pre-existing files as already known.
    Quietly registers the most recent document so it is immediately accessible
    without triggering unprompted spoken debriefs on boot."""
    global _processed_files
    folder = target_dir or RAG_FOLDER
    if not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
        return

    supported_exts = {".pdf", ".docx", ".doc", ".csv", ".tsv", ".txt", ".md"}
    latest_file = None
    latest_mtime = 0.0

    try:
        entries = os.listdir(folder)
    except Exception:
        return

    for fname in entries:
        full_path = os.path.join(folder, fname)
        if not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in supported_exts:
            continue
        try:
            mtime = os.path.getmtime(full_path)
            # Record mtime so watcher loop knows this file already existed before startup
            _processed_files[full_path] = mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_file = (full_path, fname)
        except Exception:
            pass

    _save_rag_index(_processed_files)

    # Quietly parse the most recent pre-existing document so it's ready in memory & HUD
    # if the user later asks "summarize this pdf" or queries it, WITHOUT speaking.
    if latest_file and not _active_doc:
        try:
            print(f"[docintel] Pre-loaded active baseline document: {latest_file[1]}")
            parse_document(file_path=latest_file[0], filename=latest_file[1])
        except Exception as e:
            print(f"[docintel] Preload notice: {e}")


def scan_and_analyze_rag_folder(folder_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Check RON_RAG folder for any new or modified documents and analyze them automatically."""
    target_dir = folder_path or RAG_FOLDER
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir, exist_ok=True)

    supported_exts = {".pdf", ".docx", ".doc", ".csv", ".tsv", ".txt", ".md"}
    new_docs = []

    try:
        entries = os.listdir(target_dir)
    except Exception as e:
        print(f"[docintel watcher] Failed to list {target_dir}: {e}")
        return []

    for fname in entries:
        full_path = os.path.join(target_dir, fname)
        if not os.path.isfile(full_path):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in supported_exts:
            continue

        try:
            mtime = os.path.getmtime(full_path)
            last_seen = _processed_files.get(full_path)

            if last_seen is None or mtime > last_seen:
                # Wait briefly to ensure file write is complete if being copied
                size_before = os.path.getsize(full_path)
                time.sleep(0.3)
                size_after = os.path.getsize(full_path)
                if size_before != size_after:
                    continue  # still copying

                print(f"[docintel watcher] New document detected in RON_RAG: {fname}")
                bus.activity(f"RON_RAG detected: {fname}. Auto-analyzing...", "pending")

                # Parse document
                doc_record = parse_document(file_path=full_path, filename=fname)
                _processed_files[full_path] = mtime
                _save_rag_index(_processed_files)

                # Generate autonomous executive digest
                digest = generate_executive_digest(doc_record["id"])

                # Check if RON is currently busy interacting with the user
                current_state = bus.get_state()
                if current_state not in (bus.SPEAKING, bus.LISTENING, bus.THINKING, bus.EXECUTING):
                    spoken = digest.get("spoken_debrief") or f"Document {fname} analyzed automatically from RON RAG folder, Sir."
                    try:
                        import voice
                        voice.speak(spoken)
                    except Exception:
                        pass
                else:
                    bus.activity(f"RAG document {fname} indexed. Digest ready on HUD.", "ok")

                new_docs.append(doc_record)
        except Exception as e:
            print(f"[docintel watcher] Error processing {fname}: {e}")

    return new_docs


def _rag_watcher_worker():
    """Background polling daemon for RON_RAG folder."""
    print(f"[docintel] Autonomous RON_RAG watcher active on: {RAG_FOLDER}")
    while not _watcher_stop.is_set():
        try:
            scan_and_analyze_rag_folder()
        except Exception as e:
            print(f"[docintel watcher loop error: {e}]")
        _watcher_stop.wait(3.0)


def start_rag_watcher() -> bool:
    """Start the background daemon watching C:\\Users\\Rownok\\Documents\\RON_RAG."""
    global _watcher_thread
    if _watcher_thread and _watcher_thread.is_alive():
        return True

    os.makedirs(RAG_FOLDER, exist_ok=True)
    _watcher_stop.clear()

    # Mark pre-existing files as known so startup never plays spontaneous dossier debriefs
    initialize_rag_baseline(RAG_FOLDER)

    _watcher_thread = threading.Thread(target=_rag_watcher_worker, daemon=True)
    _watcher_thread.start()
    return True


def stop_rag_watcher():
    """Stop the background watcher."""
    _watcher_stop.set()

