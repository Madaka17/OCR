"""Local web app for the OpenThai 2.0 OCR model.

Serves a single page where you drop or paste an image or PDF, and shows the
recognised text next to the page image with a box drawn around every line.
Uploads are processed in memory and not stored on the server.

Usage:
    python web_app.py [--port 8000] [--host 127.0.0.1]

Then open http://127.0.0.1:8000 in a browser.

Endpoints:
    GET  /     the web page (web/index.html)
    POST /ocr  raw file bytes in the body, original filename percent-encoded in
               the X-Filename header; returns JSON with the text and line boxes
"""
import argparse
import base64
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "OpenThai2.0-OCR-Text"
INDEX_HTML = BASE_DIR / "web" / "index.html"
MAX_BODY_BYTES = 50 * 1024 * 1024
MAX_DRAIN_BYTES = 200 * 1024 * 1024
DRAIN_CHUNK_BYTES = 64 * 1024
PDF_RENDER_SCALE = 200 / 72
NOT_AN_IMAGE_MESSAGE = "ไฟล์นี้ไม่ใช่รูปภาพหรือ PDF ที่อ่านได้"
MAX_PAGES = int(os.environ.get("MAX_PAGES", "30"))


class ClientError(Exception):
    """A problem with the request that should be reported as a 4xx response."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def load_engine():
    """Load the OCR model. Imported lazily so that importing this module stays cheap."""
    sys.path.insert(0, str(MODEL_DIR))
    from runtime.line_engine import LineOCR  # noqa: E402
    return LineOCR(str(MODEL_DIR), threads=2)


def decode_pages(filename, body):
    """Turn an uploaded file into a list of BGR ndarrays, one per page."""
    if filename.lower().endswith(".pdf") or body[:4] == b"%PDF":
        import pypdfium2 as pdfium
        try:
            pdf = pdfium.PdfDocument(body)
        except pdfium.PdfiumError:
            raise ClientError(400, NOT_AN_IMAGE_MESSAGE)
        try:
            if len(pdf) > MAX_PAGES:
                raise ClientError(400, f"PDF มีมากเกิน {MAX_PAGES} หน้า")
            pages = []
            for i in range(len(pdf)):
                page = pdf[i]
                try:
                    pil = page.render(scale=PDF_RENDER_SCALE).to_pil()
                finally:
                    page.close()
                rgb = np.asarray(pil.convert("RGB"))
                pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            return pages
        finally:
            pdf.close()
    image = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ClientError(400, NOT_AN_IMAGE_MESSAGE)
    return [image]


def run_ocr(server, filename, body):
    """Run OCR over every page of an upload and build the response dictionary."""
    arrays = decode_pages(filename, body)
    pages = []
    for number, arr in enumerate(arrays, start=1):
        with server.ocr_lock:
            start = time.perf_counter()
            result = server.ocr.read(arr)
            seconds = time.perf_counter() - start
        ok, jpeg = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("could not encode page image")
        height, width = arr.shape[:2]
        pages.append({
            "number": number,
            "width": int(width),
            "height": int(height),
            "seconds": round(seconds, 3),
            "image": "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode("ascii"),
            "text": result["text"],
            "lines": [
                {"text": line["text"],
                 "confidence": float(line["confidence"]),
                 "box": [[int(x), int(y)] for x, y in line["box"]]}
                for line in result["lines"]
            ],
        })
    return {
        "filename": filename,
        "text": "\n\n".join(page["text"] for page in pages),
        "pages": pages,
    }


class OCRRequestHandler(BaseHTTPRequestHandler):
    server_version = "ThaiOCR/1.0"

    def log_message(self, format, *args):
        # log_request passes ('"%s" %s %s', requestline, status, size).
        if args and format.startswith('"%s"') and len(args) >= 2:
            parts = str(args[0]).split()
            method = parts[0] if parts else "-"
            path = parts[1] if len(parts) > 1 else "-"
            print(f"{method} {path} {args[1]}", file=sys.stderr)
        else:
            print(format % args, file=sys.stderr)

    def send_json(self, status, payload, extra_headers=()):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            data = INDEX_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_json(404, {"error": "ไม่พบหน้านี้"})

    def do_POST(self):
        if self.path != "/ocr":
            self.send_json(404, {"error": "ไม่พบหน้านี้"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json(400, {"error": "ไม่มีไฟล์ในคำขอ"})
            return
        if length > MAX_BODY_BYTES:
            # Answer first, then read and discard the body so the client can
            # finish sending and see the response instead of a reset socket.
            self.close_connection = True
            self.send_json(413, {"error": "ไฟล์ใหญ่เกิน 50 MB"},
                           extra_headers=[("Connection", "close")])
            remaining = min(length, MAX_DRAIN_BYTES)
            while remaining > 0:
                chunk = self.rfile.read(min(DRAIN_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            return
        body = self.rfile.read(length)
        filename = urllib.parse.unquote(self.headers.get("X-Filename") or "")
        try:
            payload = run_ocr(self.server, filename, body)
        except ClientError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 - report any failure to the browser
            self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, payload)


def make_server(host, port, ocr):
    """Create (but do not start) the HTTP server bound to the given OCR engine."""
    httpd = ThreadingHTTPServer((host, port), OCRRequestHandler)
    httpd.ocr = ocr
    httpd.ocr_lock = threading.Lock()
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="Thai OCR in your browser.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    args = parser.parse_args(argv)

    print("Loading OCR model...", flush=True)
    ocr = load_engine()
    httpd = make_server(args.host, args.port, ocr)
    print(f"Listening on http://{args.host}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
