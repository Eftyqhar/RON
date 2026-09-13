"""Neural Long-Term Memory & Knowledge Graph for R.O.N.

Provides persistent, durable semantic memory across assistant restarts:
  * SQLite storage (`memories.db`) with full ACID compliance.
  * FTS5 BM25 full-text search for exact keywords, numbers, technical terms.
  * Subword/character n-gram dense semantic embeddings with Cosine Similarity.
  * Reciprocal Rank Fusion (RRF) for hybrid retrieval.
  * Knowledge Graph relations mapping subjects to entities (people, dates, preferences).
  * Proactive context injection: supplies relevant memories to LLM turns in <2ms.
  * Real-time HUD integration via bus.memory().
"""

import json
import math
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import bus

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memories.db")
_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Embedding & Vector Math (Zero external dependencies, sub-millisecond speed)
# ---------------------------------------------------------------------------

_EMBEDDING_DIM = 128

def _tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase words and alphanumeric tokens."""
    if not text:
        return []
    words = re.findall(r"[\w']+", text.lower(), re.UNICODE)
    return [w for w in words if len(w) > 0]

def _compute_embedding(text: str) -> List[float]:
    """Compute a normalized fixed-size dense embedding vector using word + character n-grams."""
    vec = [0.0] * _EMBEDDING_DIM
    if not text:
        return vec

    low = text.lower().strip()
    words = _tokenize(low)

    # 1. Word hashing with simple TF weighting
    for w in words:
        idx = hash("w_" + w) % _EMBEDDING_DIM
        vec[idx] += 1.5

    # 2. Character bi-grams and tri-grams for subword morphology and typo resilience
    for n in (2, 3):
        if len(low) >= n:
            for i in range(len(low) - n + 1):
                gram = low[i:i + n]
                idx = hash("g_" + gram) % _EMBEDDING_DIM
                vec[idx] += 0.8

    # Normalize vector to unit length (L2 norm)
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 1e-9:
        vec = [round(v / norm, 5) for v in vec]
    return vec

def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity between two unit vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    return max(0.0, min(1.0, dot))


# ---------------------------------------------------------------------------
# Database Initialization & Migration
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Get a thread-safe connection to memories.db with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    """Create schema, FTS5 virtual table, and triggers if they do not exist."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            # 1. Main memories table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    last_accessed_at REAL,
                    embedding TEXT NOT NULL DEFAULT '[]'
                )
            """)

            # 2. FTS5 Virtual Table for BM25 text search
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'")
            if not cur.fetchone():
                try:
                    cur.execute("""
                        CREATE VIRTUAL TABLE memories_fts USING fts5(
                            subject,
                            content,
                            tags,
                            content='memories',
                            content_rowid='id',
                            tokenize='unicode61'
                        )
                    """)
                    cur.execute("""
                        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                            INSERT INTO memories_fts(rowid, subject, content, tags)
                            VALUES (new.id, new.subject, new.content, new.tags);
                        END;
                    """)
                    cur.execute("""
                        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                            INSERT INTO memories_fts(memories_fts, rowid, subject, content, tags)
                            VALUES ('delete', old.id, old.subject, old.content, old.tags);
                        END;
                    """)
                    cur.execute("""
                        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                            INSERT INTO memories_fts(memories_fts, rowid, subject, content, tags)
                            VALUES ('delete', old.id, old.subject, old.content, old.tags);
                            INSERT INTO memories_fts(rowid, subject, content, tags)
                            VALUES (new.id, new.subject, new.content, new.tags);
                        END;
                    """)
                except sqlite3.OperationalError:
                    pass

            # 3. Knowledge Graph Relations table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    relation TEXT NOT NULL,
                    target_id INTEGER,
                    target_entity TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES memories(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES memories(id) ON DELETE SET NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

init_db()


# ---------------------------------------------------------------------------
# Category & Subject Extraction
# ---------------------------------------------------------------------------

CATEGORIES = ["identity", "family", "preference", "project", "credential", "date", "general"]

def _infer_category_and_subject(text: str) -> Tuple[str, str, List[str]]:
    """Analyze memory text to extract category, canonical subject, and tags."""
    clean = text.strip()
    low = clean.lower()

    category = "general"
    subject = clean
    tags = []

    # Credential / Security
    if any(w in low for w in ("password", "passcode", "pin", "api key", "secret", "token", "login", "ভুলোনা", "পাসওয়ার্ড")):
        category = "credential"
        m = re.search(r"\b(?:my\s+)?([a-z0-9_\-\.\s]+?)\s+(?:password|passcode|pin|api key|secret|token|credentials?)\b", low)
        if m:
            subject = f"{m.group(1).strip()} password"
        elif "wifi" in low or "wi-fi" in low:
            subject = "WiFi password"
        tags.extend(["security", "credentials", "private"])

    # Family & Relationships
    elif any(w in low for w in ("mother", "mom", "father", "dad", "sister", "brother", "wife", "husband", "son", "daughter", "friend", "মা", "বাবা", "ভাই", "বোন", "বন্ধু")):
        category = "family"
        for kin in ("mother's", "mom's", "father's", "dad's", "sister's", "brother's", "wife's", "husband's", "mother", "father", "mom", "dad"):
            if kin in low:
                subject = f"{kin.replace('s', '')} info"
                break
        tags.extend(["family", "contacts"])

    # Dates, Birthdays & Anniversaries
    elif any(w in low for w in ("birthday", "anniversary", "born on", "born in", "deadline", "জন্মদিন", "বার্ষিকী")):
        category = "date"
        m = re.search(r"\b([a-z0-9'\s]+?)\s+(?:birthday|anniversary|deadline)\b", low)
        if m:
            subject = f"{m.group(1).strip()}'s date"
        tags.extend(["calendar", "dates", "events"])

    # Preferences & Habits
    elif any(w in low for w in ("prefer", "preference", "favorite", "favourite", "like", "love", "hate", "dislike", "diet", "পছন্দ", "ভালোবাসি", "প্রিয়")):
        category = "preference"
        m = re.search(r"\b(?:my\s+)?(?:favorite|favourite|preferred)\s+([a-z0-9\s]+?)(?:\s+is|\s+are|\s*$)", low)
        if m:
            subject = f"favorite {m.group(1).strip()}"
        elif "dark mode" in low or "light mode" in low:
            subject = "theme preference"
        tags.extend(["preferences", "personal"])

    # Projects, Code & Work
    elif any(w in low for w in ("project", "github", "repo", "repository", "codebase", "branch", "server", "docker", "প্রজেক্ট")):
        category = "project"
        m = re.search(r"\b(?:project|repo|repository)\s+([a-z0-9_\-]+)\b", low)
        if m:
            subject = f"project {m.group(1).strip()}"
        tags.extend(["work", "development", "project"])

    # Identity & Personal Profile
    elif any(w in low for w in ("my name is", "i am", "call me", "my email", "my address", "my phone", "আমার নাম", "আমার ইমেইল")):
        category = "identity"
        if "email" in low:
            subject = "my email address"
        elif "phone" in low or "number" in low:
            subject = "my phone number"
        elif "address" in low or "live in" in low:
            subject = "my home address"
        else:
            subject = "user profile"
        tags.extend(["identity", "profile"])

    if subject == clean:
        tokens = clean.split()
        subject = " ".join(tokens[:5]) + ("…" if len(tokens) > 5 else "")

    for w in _tokenize(subject):
        if len(w) > 3 and w not in tags:
            tags.append(w)

    return category, subject.strip(), tags


# ---------------------------------------------------------------------------
# Storage & Retrieval Operations
# ---------------------------------------------------------------------------

def remember(text: str, category: Optional[str] = None, subject: Optional[str] = None) -> Dict[str, Any]:
    """Store a new fact, note, or preference in the neural memory."""
    clean_text = (text or "").strip()
    if not clean_text:
        return {"ok": False, "error": "Empty memory content"}

    auto_cat, auto_sub, tags = _infer_category_and_subject(clean_text)
    final_cat = (category or auto_cat).lower().strip()
    if final_cat not in CATEGORIES:
        final_cat = "general"
    final_sub = (subject or auto_sub).strip()

    now = time.time()
    vec = _compute_embedding(f"{final_sub} {clean_text} {' '.join(tags)}")
    embedding_json = json.dumps(vec)
    tags_json = json.dumps(tags)

    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM memories WHERE subject = ? COLLATE NOCASE", (final_sub,))
            existing = cur.fetchone()
            if existing:
                mem_id = existing["id"]
                cur.execute("""
                    UPDATE memories
                    SET content = ?, category = ?, tags = ?, updated_at = ?, embedding = ?
                    WHERE id = ?
                """, (clean_text, final_cat, tags_json, now, embedding_json, mem_id))
            else:
                cur.execute("""
                    INSERT INTO memories (category, subject, content, tags, created_at, updated_at, access_count, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """, (final_cat, final_sub, clean_text, tags_json, now, now, embedding_json))
                mem_id = cur.lastrowid

            _create_graph_relations(conn, mem_id, final_cat, final_sub, clean_text, tags)
            conn.commit()

            cur.execute("SELECT * FROM memories WHERE id = ?", (mem_id,))
            row = cur.fetchone()
            record = _row_to_dict(row)
        finally:
            conn.close()

    _broadcast_memory_state(event_name="stored", active_record=record)
    return {"ok": True, "memory": record}


def recall(query: str, limit: int = 5, min_score: float = 0.15) -> List[Dict[str, Any]]:
    """Retrieve memories matching `query` using Reciprocal Rank Fusion (BM25 + Semantic Cosine)."""
    clean_query = (query or "").strip()
    if not clean_query:
        return []

    q_tokens = _tokenize(clean_query)
    q_vec = _compute_embedding(clean_query)
    now = time.time()

    fts_ranks = {}
    semantic_scores = {}

    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()

            # 1. FTS5 BM25 Search
            try:
                fts_query = " OR ".join([f'"{t}"*' for t in q_tokens if len(t) > 1])
                if fts_query:
                    cur.execute("""
                        SELECT rowid, rank FROM memories_fts
                        WHERE memories_fts MATCH ?
                        ORDER BY rank
                        LIMIT 30
                    """, (fts_query,))
                    for rank_idx, row in enumerate(cur.fetchall()):
                        fts_ranks[row[0]] = rank_idx + 1
            except Exception:
                pass

            # 2. Dense Semantic Cosine Search across memories
            cur.execute("SELECT id, embedding FROM memories")
            all_rows = cur.fetchall()
            for r in all_rows:
                mem_id = r["id"]
                emb_str = r["embedding"]
                if emb_str:
                    try:
                        emb = json.loads(emb_str)
                        sim = _cosine_similarity(q_vec, emb)
                        semantic_scores[mem_id] = sim
                    except Exception:
                        semantic_scores[mem_id] = 0.0

            # 3. Reciprocal Rank Fusion (RRF)
            sorted_semantic = sorted(semantic_scores.items(), key=lambda x: x[1], reverse=True)
            sem_ranks = {mem_id: rank_idx + 1 for rank_idx, (mem_id, sim) in enumerate(sorted_semantic) if sim > 0.08}

            all_candidate_ids = set(fts_ranks.keys()) | set(sem_ranks.keys())
            rrf_scores = {}
            for cid in all_candidate_ids:
                rank_bm25 = fts_ranks.get(cid, 100)
                rank_sem = sem_ranks.get(cid, 100)
                score = (1.0 / (60.0 + rank_bm25)) + (1.0 / (60.0 + rank_sem))
                score += semantic_scores.get(cid, 0.0) * 0.1
                rrf_scores[cid] = score

            ranked_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:limit]

            results = []
            if ranked_ids:
                placeholders = ",".join("?" for _ in ranked_ids)
                cur.execute(f"SELECT * FROM memories WHERE id IN ({placeholders})", ranked_ids)
                rows_by_id = {row["id"]: row for row in cur.fetchall()}

                for cid in ranked_ids:
                    if cid in rows_by_id and rrf_scores[cid] >= min_score * 0.03:
                        row = rows_by_id[cid]
                        d = _row_to_dict(row)
                        d["score"] = round(rrf_scores[cid], 4)
                        results.append(d)

                        cur.execute("""
                            UPDATE memories
                            SET access_count = access_count + 1, last_accessed_at = ?
                            WHERE id = ?
                        """, (now, cid))
                conn.commit()
        finally:
            conn.close()

    if results:
        _broadcast_memory_state(event_name="recalled", recalled_items=results)

    return results


def forget(query: str) -> Dict[str, Any]:
    """Search and delete memories matching `query`."""
    clean_query = (query or "").strip()
    if not clean_query:
        return {"ok": False, "deleted_count": 0, "message": "Empty query"}

    matches = recall(clean_query, limit=5, min_score=0.1)
    if not matches:
        with _lock:
            conn = _get_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM memories WHERE subject LIKE ? OR content LIKE ?",
                            (f"%{clean_query}%", f"%{clean_query}%"))
                matches = [_row_to_dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    if not matches:
        return {"ok": False, "deleted_count": 0, "message": f"No memories found matching '{clean_query}'"}

    deleted_ids = [m["id"] for m in matches]
    deleted_subjects = [m["subject"] for m in matches]

    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in deleted_ids)
            cur.execute(f"DELETE FROM relations WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                        deleted_ids + deleted_ids)
            cur.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", deleted_ids)
            conn.commit()
        finally:
            conn.close()

    _broadcast_memory_state(event_name="forgotten", forgotten_subjects=deleted_subjects)
    return {"ok": True, "deleted_count": len(deleted_ids), "deleted_subjects": deleted_subjects}


def delete_memory_by_id(memory_id: int) -> bool:
    """Delete a single memory by ID."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM relations WHERE source_id = ? OR target_id = ?", (memory_id, memory_id))
            cur.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            success = cur.rowcount > 0
            conn.commit()
        finally:
            conn.close()

    if success:
        _broadcast_memory_state(event_name="forgotten")
    return success


def get_all_memories(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all stored memories sorted by recency and access frequency."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            if category and category.lower() != "all":
                cur.execute("SELECT * FROM memories WHERE category = ? ORDER BY updated_at DESC", (category.lower(),))
            else:
                cur.execute("SELECT * FROM memories ORDER BY updated_at DESC")
            return [_row_to_dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


def get_memory_stats() -> Dict[str, Any]:
    """Get aggregate statistics on long-term memories."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM memories")
            total = cur.fetchone()[0]

            cur.execute("SELECT category, COUNT(*) FROM memories GROUP BY category")
            categories = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("SELECT COUNT(*) FROM relations")
            relations_count = cur.fetchone()[0]

            return {
                "total_memories": total,
                "categories": categories,
                "relations_count": relations_count,
            }
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Knowledge Graph & Relations
# ---------------------------------------------------------------------------

def _create_graph_relations(conn: sqlite3.Connection, mem_id: int, category: str, subject: str, content: str, tags: List[str]):
    """Automatically extract entity links and store edges in `relations` table."""
    cur = conn.cursor()
    cur.execute("DELETE FROM relations WHERE source_id = ?", (mem_id,))

    now = time.time()
    edges = []

    # 1. Relation to USER
    edges.append((mem_id, "belongs_to", None, "User (Ifteqhar)", 1.0))

    # 2. Relation to Category
    edges.append((mem_id, "categorized_as", None, category.capitalize(), 0.8))

    # 3. Specific entities
    low = content.lower()

    for kin in ("mother", "mom", "father", "dad", "sister", "brother", "wife", "friend"):
        if kin in low:
            edges.append((mem_id, "relates_to_person", None, kin.capitalize(), 0.9))

    months = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
    for m in months:
        if m in low:
            edges.append((mem_id, "scheduled_or_dated", None, m.capitalize(), 0.9))

    tech_keywords = ("python", "github", "daraz", "amazon", "brave", "chrome", "sqlite", "fastapi", "react", "windows", "docker")
    for t in tech_keywords:
        if t in low:
            edges.append((mem_id, "tech_entity", None, t.capitalize(), 0.7))

    for source_id, rel, target_id, entity, weight in edges:
        cur.execute("""
            INSERT INTO relations (source_id, relation, target_id, target_entity, weight, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (source_id, rel, target_id, entity, weight, now))


def get_knowledge_graph() -> Dict[str, Any]:
    """Generate a graph of nodes and edges formatted for the HUD interactive Canvas."""
    with _lock:
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, category, subject, content FROM memories ORDER BY updated_at DESC LIMIT 50")
            mem_rows = cur.fetchall()

            cur.execute("""
                SELECT r.source_id, r.relation, r.target_entity, r.weight, m.subject
                FROM relations r
                JOIN memories m ON r.source_id = m.id
            """)
            rel_rows = cur.fetchall()
        finally:
            conn.close()

    nodes = []
    edges = []
    node_ids = set()

    root_id = "node_user"
    nodes.append({
        "id": root_id,
        "label": "IFTEQHAR",
        "category": "root",
        "size": 22,
        "color": "#00f0ff"
    })
    node_ids.add(root_id)

    for cat in CATEGORIES:
        cid = f"node_cat_{cat}"
        nodes.append({
            "id": cid,
            "label": cat.upper(),
            "category": "category_hub",
            "size": 14,
            "color": "#b055ff" if cat == "preference" else "#ff0077" if cat == "family" else "#00e5ff"
        })
        node_ids.add(cid)
        edges.append({
            "source": root_id,
            "target": cid,
            "relation": "has_aspect",
            "color": "rgba(0, 240, 255, 0.4)"
        })

    for row in mem_rows:
        mid = f"mem_{row['id']}"
        cat = row["category"]
        nodes.append({
            "id": mid,
            "label": row["subject"][:20],
            "full_subject": row["subject"],
            "content": row["content"],
            "category": cat,
            "size": 10,
            "color": "#e0e6ed"
        })
        node_ids.add(mid)

        cat_hub_id = f"node_cat_{cat}"
        edges.append({
            "source": cat_hub_id,
            "target": mid,
            "relation": "contains",
            "color": "rgba(176, 85, 255, 0.5)"
        })

    for rel in rel_rows:
        mid = f"mem_{rel['source_id']}"
        entity_name = rel["target_entity"]
        eid = f"entity_{re.sub(r'[^a-zA-Z0-9]', '_', entity_name.lower())}"

        if eid not in node_ids:
            nodes.append({
                "id": eid,
                "label": entity_name[:16],
                "category": "entity",
                "size": 8,
                "color": "#ffbe0b"
            })
            node_ids.add(eid)

        edges.append({
            "source": mid,
            "target": eid,
            "relation": rel["relation"],
            "color": "rgba(255, 190, 11, 0.4)"
        })

    return {"nodes": nodes, "edges": edges, "count": len(mem_rows)}


# ---------------------------------------------------------------------------
# Proactive Context Injection for Conversational Fallback
# ---------------------------------------------------------------------------

def get_proactive_context(user_query: str) -> str:
    """Retrieve top relevant memories and format them into a prompt injection block."""
    clean = (user_query or "").strip()
    if not clean or len(clean) < 3:
        return ""

    low = clean.lower()
    if any(low.startswith(p) for p in ("volume", "set timer", "what time", "open ", "play ", "shutdown", "exit", "sleep")):
        return ""

    matches = recall(clean, limit=4, min_score=0.22)
    if not matches:
        return ""

    lines = ["[RECALLED LONG-TERM MEMORIES & PERSONAL FACTS]"]
    for m in matches:
        lines.append(f"- [{m['category'].upper()}] {m['subject']}: {m['content']}")
    lines.append("[INSTRUCTION: Use the above personal facts naturally in your response if relevant. Do not recite them robotically.]")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Bus Telemetry Broadcaster
# ---------------------------------------------------------------------------

def _broadcast_memory_state(event_name: str = "update", active_record: Optional[Dict] = None,
                            recalled_items: Optional[List] = None, forgotten_subjects: Optional[List] = None):
    """Publish memory snapshot and graph structure to bus.memory() for HUD rendering."""
    try:
        stats = get_memory_stats()
        all_memories = get_all_memories()[:30]
        graph = get_knowledge_graph()

        payload = {
            "event": event_name,
            "total_count": stats["total_memories"],
            "categories": stats["categories"],
            "memories": all_memories,
            "graph": graph,
            "active_record": active_record,
            "recalled_items": recalled_items,
            "forgotten_subjects": forgotten_subjects,
            "updated_at": time.time()
        }
        bus.memory(**payload)
    except Exception:
        pass


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    """Convert an SQLite row into a clean serializable dictionary."""
    d = dict(row)
    if isinstance(d.get("tags"), str):
        try:
            d["tags"] = json.loads(d["tags"])
        except Exception:
            d["tags"] = []
    if "embedding" in d:
        del d["embedding"]
    return d
