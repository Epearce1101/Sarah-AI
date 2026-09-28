"""Sarah's episodic memory: real moments, found again by meaning.

Summaries squash a conversation into a few lines; this keeps the moments
themselves. Every exchange with Zero, things she noticed or did, what she
learned on her own time, journal entries and saved facts become *episodes*:
the original text, when it happened, and an embedding (a vector of its
meaning from a small local model, bge-small, no cloud and no cost).

Each turn, what Zero just said is embedded and the closest past moments that
aren't already in the conversation window come back into her prompt ("this
reminds me of..."). She can also search on purpose with the `recall` tool,
by meaning or by day.

Writes go through one background worker thread, so saving a memory never
slows a reply; search is a single matrix product over an in-memory copy of
all vectors (a few ms for tens of thousands of episodes).
"""
from __future__ import annotations

import logging
import math
import os
import queue
import re
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

logger = logging.getLogger("sarah.episodic")

MODEL_REPO = "BAAI/bge-small-en-v1.5"
DIM = 384
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
MIN_SCORE = 0.53          # raw cosine a memory needs to be recalled at all
SPREAD = 0.10             # ...and within this of the best match
MAX_TEXT = 1200
# Experience kinds (backend.memory.journal) worth keeping as episodes.
# "saw" (every screen change) and "felt" are too fine-grained; the journal
# carries them.
EXPERIENCE_KINDS = {"noticed", "did", "sensed", "learned", "away"}

_lock = threading.RLock()
_embed_fn: Optional[Callable[[Sequence[str]], np.ndarray]] = None
_model_failed = False
_matrix: Optional[np.ndarray] = None      # (N, DIM) float32, rows normalized
_ids: List[int] = []
_queue: "queue.Queue[tuple]" = queue.Queue()
_worker: Optional[threading.Thread] = None
SYNC = False   # tests: process writes inline instead of on the worker


def enabled() -> bool:
    return os.environ.get("SARAH_EPISODIC", "1") != "0"


# ---------------------------------------------------------------------------
# The embedding model (local ONNX on the CPU: ~6 ms per sentence)
# ---------------------------------------------------------------------------

def _load_model() -> Optional[Callable[[Sequence[str]], np.ndarray]]:
    global _model_failed
    if _model_failed:
        return None
    try:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model = hf_hub_download(MODEL_REPO, "onnx/model.onnx")
        tok = Tokenizer.from_file(hf_hub_download(MODEL_REPO, "tokenizer.json"))
        tok.enable_truncation(512)
        tok.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2   # leave the CPU to games and speech
        sess = ort.InferenceSession(model, sess_options=opts, providers=["CPUExecutionProvider"])
        wants_types = "token_type_ids" in {i.name for i in sess.get_inputs()}

        def embed(texts: Sequence[str]) -> np.ndarray:
            enc = tok.encode_batch(list(texts))
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
            if wants_types:
                feeds["token_type_ids"] = np.zeros_like(ids)
            out = sess.run(None, feeds)[0][:, 0]  # CLS pooling (bge)
            return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)

        embed(["warm up"])
        logger.info("[EPISODIC] embedding model ready (%s)", MODEL_REPO)
        return embed
    except Exception as exc:
        _model_failed = True
        logger.warning("[EPISODIC] embedding model unavailable, recall by meaning is off: %s", exc)
        return None


def set_embedder(fn: Optional[Callable[[Sequence[str]], np.ndarray]]) -> None:
    """Swap the embedding function (tests). Clears the vector cache."""
    global _embed_fn, _matrix, _ids, _model_failed
    with _lock:
        _embed_fn, _matrix, _ids, _model_failed = fn, None, [], False


def _embed(texts: Sequence[str]) -> Optional[np.ndarray]:
    global _embed_fn
    with _lock:
        if _embed_fn is None:
            _embed_fn = _load_model()
        fn = _embed_fn
    if fn is None or not texts:
        return None
    return np.asarray(fn(texts), dtype=np.float32)


def available() -> bool:
    return enabled() and _embed(["ok"]) is not None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _conn():
    from backend.models.core import get_connection

    conn = get_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episodes (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT,"
        " text TEXT, conversation_id INTEGER, source TEXT UNIQUE, importance REAL DEFAULT 0,"
        " recalled INTEGER DEFAULT 0, last_recalled TEXT, embedding BLOB)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_at ON episodes(at)")
    return conn


def _clean(text: str) -> str:
    try:
        from backend.embodiment import strip_body_tags
        text = strip_body_tags(text or "")
    except Exception:
        pass
    text = re.sub(r"<[^>]{1,80}/>", " ", text or "")   # agenda tags etc.
    return re.sub(r"\s+", " ", text).strip()


def _store(items: List[Dict[str, Any]]) -> int:
    """Embed and insert episodes (skipping sources already stored)."""
    if not items:
        return 0
    conn = _conn()
    try:
        fresh, texts = [], set()
        for it in items:
            src = it.get("source")
            if src and conn.execute("SELECT 1 FROM episodes WHERE source = ?", (src,)).fetchone():
                continue
            # The same moment again ("Zero opened the app...") is one memory.
            key = (it["kind"], it["text"])
            if key in texts or (it["kind"] != "conversation" and conn.execute(
                    "SELECT 1 FROM episodes WHERE kind = ? AND text = ?", key).fetchone()):
                continue
            texts.add(key)
            fresh.append(it)
        if not fresh:
            return 0
        vecs = _embed([it["text"] for it in fresh])
        if vecs is None:
            return 0
        new_ids, new_vecs = [], []
        for it, vec in zip(fresh, vecs):
            cur = conn.execute(
                "INSERT OR IGNORE INTO episodes (at, kind, text, conversation_id, source, importance, embedding)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (it.get("at") or datetime.now().isoformat(timespec="seconds"), it["kind"], it["text"],
                 it.get("conversation_id"), it.get("source"), float(it.get("importance") or 0),
                 vec.astype(np.float16).tobytes()),
            )
            if cur.rowcount:
                new_ids.append(cur.lastrowid)
                new_vecs.append(vec)
        conn.commit()
    finally:
        conn.close()
    global _matrix
    with _lock:
        if _matrix is not None and new_vecs:
            _matrix = np.vstack([_matrix, np.stack(new_vecs)])
            _ids.extend(new_ids)
    return len(new_ids)


def _load_matrix() -> None:
    global _matrix, _ids
    with _lock:
        if _matrix is not None:
            return
        conn = _conn()
        try:
            rows = conn.execute("SELECT id, embedding FROM episodes WHERE embedding IS NOT NULL ORDER BY id").fetchall()
        finally:
            conn.close()
        _ids = [r[0] for r in rows]
        if rows:
            _matrix = np.stack([np.frombuffer(r[1], dtype=np.float16) for r in rows]).astype(np.float32)
        else:
            _matrix = np.zeros((0, DIM), dtype=np.float32)


# ---------------------------------------------------------------------------
# Writing (queued; the worker embeds in batches)
# ---------------------------------------------------------------------------

def _message_episode(message_id: int) -> Optional[Dict[str, Any]]:
    """An assistant message + the user turn it answered, as one moment."""
    from backend.identity import get_user_name
    from backend.models.core import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT id, conversation_id, role, content, meta_json, created_at FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
        if not row or row["role"] != "assistant":
            return None
        prev = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? AND id < ? ORDER BY id DESC LIMIT 1",
            (row["conversation_id"], message_id)).fetchone()
    finally:
        conn.close()
    reply = _clean(row["content"])[:MAX_TEXT // 2]
    if not reply:
        return None
    spontaneous = "spontaneous" in (row["meta_json"] or "")
    if prev and prev["role"] == "user" and not spontaneous:
        text = f"{get_user_name()}: {_clean(prev['content'])[:MAX_TEXT // 2]}\nYou: {reply}"
    else:
        text = f"You said (on your own): {reply}"
    return {"kind": "conversation", "text": text, "conversation_id": row["conversation_id"],
            "source": f"msg:{message_id}", "at": _utc_to_local(row["created_at"])}


def _utc_to_local(stamp: Optional[str]) -> str:
    try:
        from datetime import timezone
        dt = datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _resolve(job: tuple) -> Optional[Dict[str, Any]]:
    if job[0] == "message":
        return _message_episode(job[1])
    return job[1]


def _drain(block: bool = True) -> None:
    batch = []
    try:
        batch.append(_queue.get(timeout=5) if block else _queue.get_nowait())
        while len(batch) < 32:
            batch.append(_queue.get_nowait())
    except queue.Empty:
        pass
    items = []
    for job in batch:
        try:
            item = _resolve(job)
            if item and item.get("text"):
                items.append(item)
        except Exception as exc:
            logger.debug("[EPISODIC] skipped %s: %s", job[0], exc)
    if items:
        try:
            _store(items)
        except Exception as exc:
            logger.warning("[EPISODIC] store failed: %s", exc)


def _run_worker() -> None:
    while True:
        _drain(block=True)


def _submit(job: tuple) -> None:
    global _worker
    if not enabled():
        return
    _queue.put(job)
    if SYNC:
        while not _queue.empty():
            _drain(block=False)
        return
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="sarah-episodic", daemon=True)
            _worker.start()


def note_message(message_id: Optional[int]) -> None:
    """A reply of hers was saved: remember the exchange (never blocks)."""
    if message_id:
        _submit(("message", int(message_id)))


def note(kind: str, text: str, *, source: Optional[str] = None, conversation_id: Optional[int] = None,
         importance: float = 0, at: Optional[str] = None) -> None:
    text = _clean(text)[:MAX_TEXT]
    if text:
        _submit(("item", {"kind": kind, "text": text, "source": source, "conversation_id": conversation_id,
                          "importance": importance, "at": at}))


def flush(timeout: float = 10.0) -> None:
    """Wait until queued writes are stored (tests, shutdown)."""
    end = time.time() + timeout
    while not _queue.empty() and time.time() < end:
        if SYNC or _worker is None:
            _drain(block=False)
        else:
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# Remembering (search)
# ---------------------------------------------------------------------------

def _existing_messages(ids: Iterable[int]) -> set:
    ids = list(ids)
    if not ids:
        return set()
    from backend.models.core import get_connection

    conn = get_connection()
    try:
        marks = ",".join("?" * len(ids))
        return {r[0] for r in conn.execute(f"SELECT id FROM messages WHERE id IN ({marks})", ids).fetchall()}
    finally:
        conn.close()


def search(query: str, k: int = 5, *, exclude_sources: Iterable[str] = (), kinds: Optional[Iterable[str]] = None,
           min_score: Optional[float] = None, touch: bool = False) -> List[Dict[str, Any]]:
    """The past moments closest in meaning to `query`, best first.
    ``min_score`` defaults to MIN_SCORE; the recall tool digs a little deeper."""
    min_score = MIN_SCORE if min_score is None else min_score
    query = (query or "").strip()
    if not enabled() or not query:
        return []
    qv = _embed([QUERY_PREFIX + query[:1000]])
    if qv is None:
        return []
    _load_matrix()
    with _lock:
        matrix, ids = _matrix, list(_ids)
    if matrix is None or not len(ids):
        return []
    sims = matrix @ qv[0]
    order = np.argsort(-sims)[: max(k * 6, 30)]
    cand_ids = [ids[i] for i in order if sims[i] >= min_score]
    if not cand_ids:
        return []
    best = float(sims[order[0]])
    score_of = {ids[i]: float(sims[i]) for i in order}
    conn = _conn()
    try:
        marks = ",".join("?" * len(cand_ids))
        rows = conn.execute(f"SELECT id, at, kind, text, conversation_id, source, importance, recalled FROM episodes"
                            f" WHERE id IN ({marks})", cand_ids).fetchall()
    finally:
        conn.close()
    exclude = set(exclude_sources)
    kinds = set(kinds) if kinds else None
    msg_ids = [int(r["source"][4:]) for r in rows if (r["source"] or "").startswith("msg:")]
    alive = _existing_messages(msg_ids)
    now = datetime.now()
    found, seen_texts = [], set()
    for r in sorted(rows, key=lambda r: -score_of[r["id"]]):
        src = r["source"] or ""
        if src in exclude or (kinds and r["kind"] not in kinds) or r["text"] in seen_texts:
            continue
        seen_texts.add(r["text"])
        if src.startswith("msg:") and int(src[4:]) not in alive:
            continue  # the message was deleted: so is the memory
        sim = score_of[r["id"]]
        if sim < best - SPREAD:
            continue
        try:
            age_days = max(0.0, (now - datetime.fromisoformat(r["at"])).total_seconds() / 86400)
        except Exception:
            age_days = 30.0
        score = sim + 0.03 * float(r["importance"] or 0) + 0.04 * math.exp(-age_days / 14) \
            + 0.01 * min(5, int(r["recalled"] or 0))
        found.append({"id": r["id"], "at": r["at"], "kind": r["kind"], "text": r["text"],
                      "conversation_id": r["conversation_id"], "source": src, "score": round(score, 3),
                      "similarity": round(sim, 3)})
    found.sort(key=lambda x: -x["score"])
    found = found[:k]
    if touch and found:
        conn = _conn()
        try:
            conn.executemany("UPDATE episodes SET recalled = recalled + 1, last_recalled = ? WHERE id = ?",
                             [(now.isoformat(timespec="seconds"), f["id"]) for f in found])
            conn.commit()
        finally:
            conn.close()
    return found


def on_day(day: str, limit: int = 40) -> List[Dict[str, Any]]:
    """Episodes from one local calendar day (YYYY-MM-DD), in order."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT id, at, kind, text FROM episodes WHERE at >= ? AND at < ? ORDER BY at LIMIT ?",
                            (day, day + "T99", limit)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def when(at: str, now: Optional[datetime] = None) -> str:
    """'earlier today', 'yesterday evening', '3 days ago (Wed 25 Sep)'."""
    now = now or datetime.now()
    try:
        dt = datetime.fromisoformat(at)
    except Exception:
        return "some time ago"
    part = "morning" if dt.hour < 12 else "afternoon" if dt.hour < 17 else "evening" if dt.hour < 22 else "night"
    days = (now.date() - dt.date()).days
    if days <= 0:
        return f"earlier today ({dt.strftime('%H:%M')})"
    if days == 1:
        return f"yesterday {part}"
    if days < 14:
        return f"{days} days ago ({dt.strftime('%a %d %b')}, {part})"
    return dt.strftime("%d %B %Y")


def render_for_prompt(query: str, exclude_sources: Iterable[str] = (), k: int = 4, char_cap: int = 1400) -> str:
    """'This brings back...' block for the system prompt ('' if nothing fits)."""
    try:
        found = search(query, k=k, exclude_sources=exclude_sources, touch=True)
    except Exception as exc:
        logger.debug("[EPISODIC] recall failed: %s", exc)
        return ""
    lines, used = [], 0
    for f in found:
        text = f["text"] if len(f["text"]) <= 320 else f["text"][:317] + "..."
        line = f"- {when(f['at'])}: {text}"
        if used + len(line) > char_cap:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return ("# Moments this brings back (your own memories)\n"
            "Real things from before that relate to what's happening now. Use them naturally if they help "
            "(\"like when you...\"), ignore them if they don't. Don't list them.\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Backfill: index everything from before this existed (once, in the background)
# ---------------------------------------------------------------------------

def _tidy() -> int:
    """Drop repeated moments and memories of deleted messages."""
    global _matrix
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM episodes WHERE kind != 'conversation' AND id NOT IN"
                           " (SELECT MIN(id) FROM episodes GROUP BY kind, text)")
        removed = cur.rowcount
        cur = conn.execute("DELETE FROM episodes WHERE source LIKE 'msg:%' AND CAST(substr(source, 5) AS INTEGER)"
                           " NOT IN (SELECT id FROM messages)")
        removed += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if removed:
        with _lock:
            _matrix = None  # reload without them
        logger.info("[EPISODIC] tidied %d repeated/deleted moments", removed)
    return removed


def backfill(max_items: int = 20000) -> int:
    if not enabled() or _embed(["ok"]) is None:
        return 0
    from backend.models.core import get_connection

    _tidy()
    conn = _conn()
    try:
        have = {r[0] for r in conn.execute("SELECT source FROM episodes WHERE source IS NOT NULL").fetchall()}
    finally:
        conn.close()
    conn = get_connection()
    try:
        msg_ids = [r[0] for r in conn.execute(
            "SELECT id FROM messages WHERE role = 'assistant' ORDER BY id DESC LIMIT ?", (max_items,)).fetchall()]
        mems = conn.execute("SELECT id, content, created_at FROM memories").fetchall()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        journal = conn.execute("SELECT day, entry FROM journal").fetchall() if "journal" in tables else []
        exps = conn.execute(
            f"SELECT id, at, kind, text FROM experiences WHERE kind IN ({','.join('?' * len(EXPERIENCE_KINDS))})",
            sorted(EXPERIENCE_KINDS)).fetchall() if "experiences" in tables else []
    finally:
        conn.close()
    items: List[Dict[str, Any]] = []
    for mid in msg_ids:
        if f"msg:{mid}" not in have:
            ep = _message_episode(mid)
            if ep:
                items.append(ep)
    for m in mems:
        if f"mem:{m[0]}" not in have and m[1]:
            items.append({"kind": "fact", "text": _clean(m[1])[:MAX_TEXT], "source": f"mem:{m[0]}",
                          "importance": 1, "at": _utc_to_local(m[2])})
    for j in journal:
        if f"journal:{j[0]}" not in have and j[1]:
            items.append({"kind": "journal", "text": f"Your journal for {j[0]}: {_clean(j[1])}"[:MAX_TEXT],
                          "source": f"journal:{j[0]}", "importance": 0.5, "at": f"{j[0]}T21:00:00"})
    for e in exps:
        if f"exp:{e[0]}" not in have and e[3]:
            items.append({"kind": e[2], "text": _clean(e[3])[:MAX_TEXT], "source": f"exp:{e[0]}", "at": e[1]})
    added = 0
    for i in range(0, len(items), 64):
        added += _store(items[i:i + 64])
    if added:
        logger.info("[EPISODIC] indexed %d past moments", added)
    return added


def stats() -> Dict[str, Any]:
    try:
        conn = _conn()
        try:
            rows = conn.execute("SELECT kind, COUNT(*) FROM episodes GROUP BY kind").fetchall()
        finally:
            conn.close()
        by_kind = {r[0]: r[1] for r in rows}
    except Exception:
        by_kind = {}
    return {"enabled": enabled(), "model": MODEL_REPO, "ready": _embed_fn is not None,
            "episodes": sum(by_kind.values()), "by_kind": by_kind}
