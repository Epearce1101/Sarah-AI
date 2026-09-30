# backend/document_reader.py
"""
Document reader powered by Docling (https://github.com/docling-project/docling).

Turns PDFs, Word/PowerPoint/Excel files, HTML and scanned images into clean
Markdown (tables included) so Sarah can research from real documents.
Runs fully offline once Docling's models have downloaded on first use.

Install:  pip install docling
"""
import ipaddress
import socket
import tempfile
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse
from typing import Any, Awaitable, Callable, Dict, Optional

PLAIN_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".log", ".json"}
MAX_PROMPT_CHARS = 12000   # keeps questions inside Groq's free-tier token limits
MAX_TEXT_FILE_BYTES = 20 * 1024 * 1024  # don't load huge log files into memory
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_REDIRECTS = 5
_SUFFIX_BY_TYPE = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
}

_converter = None
_converter_lock = threading.Lock()   # guards creating the converter
_convert_lock = threading.Lock()     # one conversion at a time (not known to be thread-safe)


def docling_available() -> bool:
    try:
        import docling  # noqa: F401
        return True
    except Exception:
        return False


def _is_public_url(url: str) -> bool:
    """Only fetch internet addresses, never this PC / the home network."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _download_public(url: str) -> Path:
    """
    Download a URL to a temp file. Every redirect hop is re-checked, so a
    public link can't bounce Sarah onto this PC or the home network.
    """
    import requests

    for _ in range(MAX_REDIRECTS + 1):
        if not _is_public_url(url):
            raise ValueError("Only public internet URLs can be read.")
        resp = requests.get(url, stream=True, timeout=30, allow_redirects=False)
        try:
            if resp.is_redirect:
                url = urljoin(url, resp.headers.get("location", ""))
                continue
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            suffix = Path(urlparse(url).path).suffix.lower() or _SUFFIX_BY_TYPE.get(content_type, ".html")
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            size = 0
            try:
                with tmp:
                    for chunk in resp.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise ValueError("Download is too large (over 50 MB).")
                        tmp.write(chunk)
            except Exception:
                Path(tmp.name).unlink(missing_ok=True)
                raise
            return Path(tmp.name)
        finally:
            resp.close()
    raise ValueError("Too many redirects.")


def _get_converter():
    global _converter
    with _converter_lock:
        if _converter is None:
            from docling.document_converter import DocumentConverter
            _converter = DocumentConverter()  # slow: loads layout/OCR models
        return _converter


def read_document(source: str) -> Dict[str, Any]:
    """
    source: a local file path or an http(s) URL.
    Returns {ok, markdown, tables, pages, error}.
    """
    source = (source or "").strip()
    if not source:
        return {"ok": False, "error": "No document path or URL given."}

    downloaded: Optional[Path] = None
    if source.lower().startswith(("http://", "https://")):
        try:
            downloaded = _download_public(source)
        except Exception as e:
            return {"ok": False, "error": f"Could not download: {e}"}
        path = downloaded
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            return {"ok": False, "error": f"File not found: {source}"}
    try:
        return _read_path(path)
    finally:
        if downloaded is not None:
            downloaded.unlink(missing_ok=True)


def _read_path(path: Path) -> Dict[str, Any]:
    # Plain text doesn't need Docling at all
    if path.suffix.lower() in PLAIN_TEXT_SUFFIXES:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            return {"ok": False, "error": "File is too large (over 20 MB)."}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "markdown": text, "tables": 0, "pages": None}

    if not docling_available():
        return {
            "ok": False,
            "error": "Docling is not installed. Run: pip install docling",
        }

    try:
        converter = _get_converter()
        with _convert_lock:
            result = converter.convert(str(path))
        doc = result.document
        return {
            "ok": True,
            "markdown": doc.export_to_markdown(),
            "tables": len(getattr(doc, "tables", []) or []),
            "pages": len(getattr(doc, "pages", {}) or {}) or None,
        }
    except Exception as e:
        return {"ok": False, "error": f"Docling could not read the document: {e}"}


async def ask_document(
    source: str,
    question: str,
    llm: Callable[[str], Awaitable[str]],
    read_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Read a document and have the LLM answer a question about it."""
    import asyncio

    loop = asyncio.get_running_loop()
    doc = await loop.run_in_executor(None, read_fn or read_document, source)
    if not doc.get("ok"):
        return doc

    text = doc["markdown"]
    truncated = len(text) > MAX_PROMPT_CHARS
    prompt = (
        "Answer the Creator's question using ONLY the document below. "
        "If the answer isn't in it, say so.\n\n"
        f"--- DOCUMENT{' (first part only)' if truncated else ''} ---\n"
        f"{text[:MAX_PROMPT_CHARS]}\n--- END DOCUMENT ---\n\n"
        f"Question: {question}"
    )
    answer = await llm(prompt)
    return {
        "ok": True,
        "answer": answer,
        "truncated": truncated,
        "tables": doc.get("tables"),
        "pages": doc.get("pages"),
    }
