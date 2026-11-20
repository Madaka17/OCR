"""Usage:

    python -m server                                  serve (refuses to start until passwords are set)
    python -m server passwords --user X --admin Y     set the user and admin passwords
    python -m server passwords                        same, asking for each password (typed twice)

Changing passwords invalidates every session, and the running server must be restarted.
"""
import argparse
import getpass
import sys

from server import settings
from server.app import make_server, start_cleanup
from server.auth import Auth
from server.llm import LLMClient
from server.scheduler import Scheduler
from server.stats import EventLog
from server.store import JobStore
from server.summary_worker import SummaryWorker


def config_path():
    return settings.DATA_DIR / "config.json"


def load_engine():
    """Imported lazily so that `passwords` does not load the OCR model stack."""
    sys.path.insert(0, str(settings.MODEL_DIR))
    from runtime.line_engine import LineOCR  # noqa: E402

    return LineOCR(str(settings.MODEL_DIR), threads=settings.THREADS)


def set_passwords(user_pw: str, admin_pw: str) -> int:
    if not user_pw or not admin_pw:
        print("รหัสผ่านต้องไม่ว่างเปล่า", file=sys.stderr)
        return 2
    Auth(config_path()).set_passwords(user_pw, admin_pw)
    print(f"ตั้งรหัสผ่านแล้ว บันทึกที่ {config_path()}")
    print("ต้องเริ่มเซิร์ฟเวอร์ใหม่ เพื่อให้รหัสผ่านใหม่มีผล (การเข้าสู่ระบบเดิมจะใช้ไม่ได้แล้ว)")
    return 0


def ask_password(label: str) -> str | None:
    """Ask twice; None when the two entries are empty or differ."""
    first = getpass.getpass(f"{label}: ")
    second = getpass.getpass(f"{label} (พิมพ์อีกครั้ง): ")
    return first if first and first == second else None


def make_summaries(store):
    """The AI summary worker, or None when OCR_LLM_URL is empty. Built but not started."""
    if not settings.LLM_URL:
        print("AI summary: off", flush=True)
        return None
    print(f"AI summary: {settings.LLM_MODEL} at {settings.LLM_URL}", flush=True)
    llm = LLMClient(settings.LLM_URL, settings.LLM_MODEL)
    return SummaryWorker(store, llm, settings.LLM_MODEL, url=settings.LLM_URL)


def serve() -> int:
    auth = Auth(config_path())
    if not auth.ready():
        print(
            "ยังไม่ได้ตั้งรหัสผ่าน ให้รันคำสั่งนี้ก่อน:\n"
            "  python -m server passwords --user <รหัสผู้ใช้> --admin <รหัสผู้ดูแล>",
            file=sys.stderr,
        )
        return 1
    store = JobStore(settings.DATA_DIR)
    events = EventLog(settings.DATA_DIR)
    scheduler = Scheduler(store, events, engine_factory=load_engine, workers=settings.WORKERS)
    scheduler.start()
    start_cleanup(store, events=events)
    summaries = make_summaries(store)
    httpd = make_server(settings.HOST, settings.PORT, store, scheduler, auth, events, summaries=summaries)
    host, port = httpd.server_address[:2]
    print(f"Listening on http://{host}:{port}", flush=True)
    if summaries is not None:
        summaries.start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
    finally:
        httpd.server_close()
        if summaries is not None:
            summaries.stop()
        scheduler.stop()
    return 0


def main(argv=None) -> int:
    # Thai messages must print on Windows consoles too (default codepage is cp1252 there).
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m server", description="Thai OCR job server.")
    sub = parser.add_subparsers(dest="command")
    pw = sub.add_parser("passwords", help="set the user and admin passwords")
    pw.add_argument("--user", default=None, help="shared user password (asked for when omitted)")
    pw.add_argument("--admin", default=None, help="admin password (asked for when omitted)")
    args = parser.parse_args(argv)
    if args.command == "passwords":
        user_pw = args.user if args.user is not None else ask_password("รหัสผ่านผู้ใช้")
        admin_pw = args.admin if args.admin is not None else ask_password("รหัสผ่านผู้ดูแล")
        if user_pw is None or admin_pw is None:
            print("รหัสผ่านว่างเปล่าหรือไม่ตรงกัน ยังไม่ได้เปลี่ยน", file=sys.stderr)
            return 2
        return set_passwords(user_pw, admin_pw)
    return serve()


if __name__ == "__main__":
    sys.exit(main())
