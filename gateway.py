# /// script
# dependencies = ["huawei-lte-api", "flask", "flask-sock"]
# ///

"""
SMS Gateway - E5331
Full-featured: SMS polling, SQLite storage, webhook push, USSD automated + live interactive,
delivery reports, auto-cleanup, device control.
"""

import json
import os
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps

from flask import Flask, g, jsonify, request
from flask_sock import Sock
from huawei_lte_api.Client import Client
from huawei_lte_api.Connection import Connection
from huawei_lte_api.enums.sms import BoxTypeEnum

try:
    import requests as http_requests
    from requests.exceptions import RequestException

    HAS_REQUESTS = True
except ImportError:
    http_requests = None
    RequestException = None
    HAS_REQUESTS = False

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─── App ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.json.ensure_ascii = False  # Amharic/Ethiopic renders as real characters
sock = Sock(app)


# ─── Constants ────────────────────────────────────────────────────────────────
def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


ROUTER_URL = os.environ.get("ROUTER_URL", "http://192.168.8.1")
ROUTER_USER = os.environ.get("ROUTER_USER", "admin")
ROUTER_PASS = os.environ.get("ROUTER_PASS", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
APP_PORT = env_int("APP_PORT", 5000)
DB_PATH = os.environ.get("DB_PATH", "/data/sms.db")
MODEM_CONNECT_TIMEOUT = env_float("MODEM_CONNECT_TIMEOUT", 5.0)
MODEM_READ_TIMEOUT = env_float("MODEM_READ_TIMEOUT", 15.0)
MODEM_CONNECT_RETRIES = env_int("MODEM_CONNECT_RETRIES", 3)
MODEM_RETRY_BACKOFF = env_float("MODEM_RETRY_BACKOFF", 1.0)
MODEM_FORCE_CONNECTION_CLOSE = env_bool("MODEM_FORCE_CONNECTION_CLOSE", True)
POLL_ERROR_LOG_THROTTLE = env_int("POLL_ERROR_LOG_THROTTLE", 60)
POLL_BACKOFF_MAX = env_int("POLL_BACKOFF_MAX", 60)

# Runtime defaults (overridable via POST /config)
DEFAULT_CONFIG = {
    "webhook_url": os.environ.get("WEBHOOK_URL", ""),
    "poll_interval": env_int("POLL_INTERVAL", 10),  # seconds between SMS polls
    "cleanup_interval": env_int(
        "CLEANUP_INTERVAL", 21600
    ),  # seconds between cleanup runs (6 hours)
    "modem_max_threshold": env_int(
        "MODEM_MAX_THRESHOLD", 400
    ),  # delete oldest from modem when count exceeds this
    "modem_message_max_age": env_int(
        "MODEM_MESSAGE_MAX_AGE", 3
    ),  # days before modem copy is deleted (already in DB)
}

# ─── USSD session state (single modem = single session) ────────────────────────
ussd_lock = threading.Lock()
ussd_session_ws = None  # the active WS connection object, if any
modem_api_lock = threading.Lock()  # prevent overlapping modem login sessions
health_lock = threading.Lock()
runtime_lock = threading.Lock()
runtime_started = False

modem_health = {
    "started_at": utc_now_iso(),
    "last_poll_success_at": None,
    "last_poll_error_at": None,
    "last_poll_error": "",
    "consecutive_failures": 0,
    "total_failures": 0,
    "recoveries": 0,
    "last_backoff_seconds": 0,
    "last_recovery_at": None,
    "last_sms_received_at": None,
}


def mark_modem_poll_success():
    now = utc_now_iso()
    with health_lock:
        previous_failures = modem_health["consecutive_failures"]
        had_failures = previous_failures > 0
        modem_health["last_poll_success_at"] = now
        modem_health["last_backoff_seconds"] = 0
        modem_health["consecutive_failures"] = 0
        if had_failures:
            modem_health["recoveries"] += 1
            modem_health["last_recovery_at"] = now
    return had_failures, previous_failures


def mark_modem_poll_error(error: Exception, backoff_seconds: int = 0):
    with health_lock:
        modem_health["last_poll_error_at"] = utc_now_iso()
        modem_health["last_poll_error"] = str(error)
        modem_health["consecutive_failures"] += 1
        modem_health["total_failures"] += 1
        modem_health["last_backoff_seconds"] = int(backoff_seconds)


def mark_sms_received():
    with health_lock:
        modem_health["last_sms_received_at"] = utc_now_iso()


def get_consecutive_failure_count() -> int:
    with health_lock:
        return int(modem_health["consecutive_failures"])


def get_modem_health_snapshot():
    with health_lock:
        health = dict(modem_health)

    try:
        poll_interval = max(1, int(get_config("poll_interval")))
    except Exception:
        poll_interval = int(DEFAULT_CONFIG["poll_interval"])

    health["poll_interval_seconds"] = poll_interval
    health["router_url"] = ROUTER_URL
    health["status"] = "degraded" if health["consecutive_failures"] else "healthy"
    return health


def validate_runtime_config():
    missing = []
    if not ADMIN_KEY:
        missing.append("ADMIN_KEY")
    if not ROUTER_PASS:
        missing.append("ROUTER_PASS")
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )


class ModemUnavailableError(RuntimeError):
    pass


@app.errorhandler(ModemUnavailableError)
def handle_modem_unavailable(error):
    return jsonify({"error": "Modem unavailable", "detail": str(error)}), 503


if RequestException is not None:

    @app.errorhandler(RequestException)
    def handle_modem_request_error(error):
        return jsonify({"error": "Modem request failed", "detail": str(error)}), 503


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════════════════════════════


def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


@contextmanager
def get_bg_db():
    """DB connection for background threads (not Flask context)."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_bg_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                modem_index   TEXT,
                phone         TEXT,
                content       TEXT,
                date          TEXT,
                sms_type      TEXT,
                smstat        TEXT,
                save_type     TEXT,
                received_at   TEXT DEFAULT (datetime('now')),
                forwarded_at  TEXT,
                is_delivery_report INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sent_messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                recipients    TEXT,
                content       TEXT,
                sent_at       TEXT DEFAULT (datetime('now')),
                delivery_status TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone);
            CREATE INDEX IF NOT EXISTS idx_messages_date  ON messages(date);
            CREATE INDEX IF NOT EXISTS idx_messages_modem_index ON messages(modem_index);
        """)
        # Seed default config
        for k, v in DEFAULT_CONFIG.items():
            db.execute(
                "INSERT OR IGNORE INTO config(key, value) VALUES (?, ?)", (k, str(v))
            )
    log.info("Database initialised at %s", DB_PATH)


def get_config(key):
    with get_bg_db() as db:
        row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else str(DEFAULT_CONFIG.get(key, ""))


def set_config(key, value):
    with get_bg_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO config(key,value) VALUES(?,?)", (key, str(value))
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  MODEM CONNECTION
# ═══════════════════════════════════════════════════════════════════════════════


@contextmanager
def get_client():
    """Yield a modem client with explicit connection cleanup."""
    with modem_api_lock:
        last_error = None
        conn = None
        session = None
        for attempt in range(1, MODEM_CONNECT_RETRIES + 1):
            try:
                if http_requests is not None:
                    session = http_requests.Session()
                    if MODEM_FORCE_CONNECTION_CLOSE:
                        session.headers.update({"Connection": "close"})

                conn = Connection(
                    ROUTER_URL,
                    ROUTER_USER,
                    ROUTER_PASS,
                    timeout=(MODEM_CONNECT_TIMEOUT, MODEM_READ_TIMEOUT),
                    requests_session=session,
                )
                break
            except Exception as e:
                last_error = e
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None

                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
                    session = None

                if attempt < MODEM_CONNECT_RETRIES:
                    sleep_for = MODEM_RETRY_BACKOFF * attempt
                    log.warning(
                        "Modem connect attempt %d/%d failed: %s (retrying in %.1fs)",
                        attempt,
                        MODEM_CONNECT_RETRIES,
                        e,
                        sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise ModemUnavailableError(str(e)) from e

        if conn is None:
            raise ModemUnavailableError(str(last_error))

        try:
            yield Client(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


def safe_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse_positive_int(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def fetch_modem_messages(client, box=BoxTypeEnum.LOCAL_INBOX, page=1, count=50):
    raw = client.sms.get_sms_list(page, box, count, 0, 0, 0)
    messages = []
    if raw and "Messages" in raw and raw["Messages"] and "Message" in raw["Messages"]:
        messages = safe_list(raw["Messages"]["Message"])
    total = int(raw.get("Count", len(messages))) if raw else 0
    return messages, total


# ═══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK PUSH
# ═══════════════════════════════════════════════════════════════════════════════


def push_webhook(payload: dict):
    """Fire-and-forget POST to configured webhook URL."""
    url = get_config("webhook_url")
    if not url or not HAS_REQUESTS or http_requests is None:
        return
    try:
        resp = http_requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Webhook delivered: %s → %s", payload.get("type"), resp.status_code)
    except Exception as e:
        log.warning("Webhook failed: %s", e)


def fire_webhook_async(payload: dict):
    t = threading.Thread(target=push_webhook, args=(payload,), daemon=True)
    t.start()


# ═══════════════════════════════════════════════════════════════════════════════
#  SMS POLLER (background thread)
# ═══════════════════════════════════════════════════════════════════════════════


def is_delivery_report(msg: dict) -> bool:
    """Detect delivery report messages from the network."""
    content = msg.get("Content", "").lower()
    phone = msg.get("Phone", "")
    # Delivery reports typically come from short numeric codes and contain status keywords
    keywords = ["delivered", "not delivered", "delivery", "failed to deliver"]
    return any(k in content for k in keywords) and len(phone) <= 10


def store_message(db, msg: dict, is_dr: bool = False) -> int:
    cur = db.execute(
        """INSERT OR IGNORE INTO messages
           (modem_index, phone, content, date, sms_type, smstat, save_type, is_delivery_report)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            msg.get("Index"),
            msg.get("Phone"),
            msg.get("Content"),
            msg.get("Date"),
            msg.get("SmsType"),
            msg.get("Smstat"),
            msg.get("SaveType"),
            1 if is_dr else 0,
        ),
    )
    return cur.lastrowid


def is_transient_modem_error(error: Exception) -> bool:
    text = str(error).lower()
    transient_markers = (
        "connection reset by peer",
        "connection aborted",
        "read timed out",
        "connect timeout",
        "max retries exceeded",
        "modem unavailable",
    )
    return any(marker in text for marker in transient_markers)


def sms_poller():
    log.info("SMS poller started")
    last_error = None
    last_error_logged_at = 0.0
    suppressed_errors = 0

    while True:
        try:
            interval = max(1, int(get_config("poll_interval")))
        except (TypeError, ValueError):
            interval = int(DEFAULT_CONFIG["poll_interval"])

        sleep_seconds = interval

        try:
            with get_client() as client:
                messages, _ = fetch_modem_messages(client)

                new_msgs = [m for m in messages if str(m.get("Smstat", "1")) == "0"]

                for msg in new_msgs:
                    is_dr = is_delivery_report(msg)
                    with get_bg_db() as db:
                        row_id = store_message(db, msg, is_dr)
                        db.execute(
                            "UPDATE messages SET forwarded_at=datetime('now') WHERE id=?",
                            (row_id,),
                        )

                    # Mark read on modem
                    try:
                        client.sms.set_read(int(msg["Index"]))
                    except Exception as e:
                        log.warning(
                            "Could not mark index %s read: %s", msg.get("Index"), e
                        )

                    # Push webhook
                    wh_type = "delivery_report" if is_dr else "sms_received"
                    fire_webhook_async(
                        {
                            "type": wh_type,
                            "id": row_id,
                            "phone": msg.get("Phone"),
                            "content": msg.get("Content"),
                            "date": msg.get("Date"),
                            "sms_type": msg.get("SmsType"),
                        }
                    )

                    log.info(
                        "[%s] From %s: %s",
                        wh_type,
                        msg.get("Phone"),
                        (msg.get("Content") or "")[:60],
                    )
                    if wh_type == "sms_received":
                        mark_sms_received()

            had_failures, recovered_failure_count = mark_modem_poll_success()

            if last_error is not None:
                if suppressed_errors:
                    log.error(
                        "Poller error repeated %d times: %s",
                        suppressed_errors,
                        last_error,
                    )
                    suppressed_errors = 0
                if had_failures:
                    log.info(
                        "Poller recovered after %d transient failure(s)",
                        recovered_failure_count,
                    )
                else:
                    log.info("Poller recovered")
                last_error = None

        except Exception as e:
            err = str(e)
            now = time.time()
            is_transient = is_transient_modem_error(e)

            if is_transient:
                failure_count = get_consecutive_failure_count() + 1
                backoff_multiplier = 2 ** min(failure_count - 1, 6)
                sleep_seconds = min(POLL_BACKOFF_MAX, interval * backoff_multiplier)

            mark_modem_poll_error(e, int(sleep_seconds))
            failure_count = get_consecutive_failure_count()

            if (
                err != last_error
                or (now - last_error_logged_at) >= POLL_ERROR_LOG_THROTTLE
            ):
                if suppressed_errors:
                    log.error(
                        "Poller error repeated %d times: %s",
                        suppressed_errors,
                        last_error,
                    )
                    suppressed_errors = 0
                if is_transient and sleep_seconds > interval:
                    log.error(
                        "Poller error: %s (transient #%d, backoff=%ss)",
                        e,
                        failure_count,
                        int(sleep_seconds),
                    )
                else:
                    log.error("Poller error: %s", e)
                last_error = err
                last_error_logged_at = now
            else:
                suppressed_errors += 1

        time.sleep(max(1, sleep_seconds))


# ═══════════════════════════════════════════════════════════════════════════════
#  CLEANUP JOB (background thread)
# ═══════════════════════════════════════════════════════════════════════════════


def cleanup_job():
    log.info("Cleanup job started")
    while True:
        try:
            interval = max(1, int(get_config("cleanup_interval")))
        except (TypeError, ValueError):
            interval = int(DEFAULT_CONFIG["cleanup_interval"])

        time.sleep(interval)
        try:
            threshold = int(get_config("modem_max_threshold"))
            max_age = int(get_config("modem_message_max_age"))
            with get_client() as client:
                messages, total = fetch_modem_messages(client)

                cutoff = (datetime.now() - timedelta(days=max_age)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                # Ensure all messages are stored before deleting anything
                with get_bg_db() as db:
                    for msg in messages:
                        store_message(db, msg)

                to_delete = []

                # Age-based: stored messages older than max_age days
                age_candidates = [m for m in messages if m.get("Date", "9999") < cutoff]
                to_delete.extend(age_candidates)

                # Threshold-based: if still over limit, delete oldest first
                remaining = total - len(to_delete)
                if remaining > threshold:
                    overflow = remaining - threshold
                    not_yet_marked = [m for m in messages if m not in to_delete]
                    not_yet_marked.sort(key=lambda m: m.get("Date", ""))
                    to_delete.extend(not_yet_marked[:overflow])

                deleted = 0
                for msg in to_delete:
                    try:
                        client.sms.delete_sms(int(msg["Index"]))
                        deleted += 1
                    except Exception as e:
                        log.warning(
                            "Cleanup delete failed for index %s: %s",
                            msg.get("Index"),
                            e,
                        )

                if deleted:
                    log.info(
                        "Cleanup: deleted %d messages from modem (threshold=%d, max_age=%dd)",
                        deleted,
                        threshold,
                        max_age,
                    )

        except Exception as e:
            log.error("Cleanup job error: %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════════════════════


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.headers.get("X-Admin-Key") != ADMIN_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)

    return wrapper


# ═══════════════════════════════════════════════════════════════════════════════
#  USSD HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

USSD_POLL_TIMEOUT = 30
USSD_POLL_INTERVAL = 1


def poll_ussd_response(client, timeout=USSD_POLL_TIMEOUT):
    for _ in range(timeout):
        try:
            resp = client.ussd.get()
            if resp and resp.get("content"):
                return resp["content"]
        except Exception:
            pass
        time.sleep(USSD_POLL_INTERVAL)
    raise TimeoutError("USSD response not received within timeout")


# ═══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS — CONFIG
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/config")
@require_auth
def get_all_config():
    """Get all runtime config values."""
    with get_bg_db() as db:
        rows = db.execute("SELECT key, value FROM config").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.post("/config")
@require_auth
def update_config():
    """
    Update runtime config values.
    Body JSON: {"webhook_url": "http://...", "poll_interval": "15"}
    All values accepted as strings. Valid keys: webhook_url, poll_interval,
    cleanup_interval, modem_max_threshold, modem_message_max_age.
    """
    body = request.get_json(force=True) or {}
    valid_keys = set(DEFAULT_CONFIG.keys())
    int_keys = {
        "poll_interval",
        "cleanup_interval",
        "modem_max_threshold",
        "modem_message_max_age",
    }
    updated = {}
    errors = {}
    for k, v in body.items():
        if k in valid_keys:
            if k in int_keys:
                try:
                    iv = int(v)
                    if iv <= 0:
                        raise ValueError("must be > 0")
                    v = iv
                except (TypeError, ValueError):
                    errors[k] = "must be a positive integer"
                    continue
            set_config(k, v)
            updated[k] = v
    return jsonify({"updated": updated, "errors": errors})


@app.get("/health/modem")
@require_auth
def modem_health_endpoint():
    """Health snapshot for n8n monitoring and alerting."""
    return jsonify(get_modem_health_snapshot())


# ═══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS — SMS
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/sms")
@require_auth
def list_sms():
    """
    List SMS from modem inbox (live).
    Query: box=inbox|sent|draft, status=all|unread|read, page=1, count=50
    """
    box_map = {
        "inbox": BoxTypeEnum.LOCAL_INBOX,
        "sent": BoxTypeEnum.LOCAL_SENT,
        "draft": BoxTypeEnum.LOCAL_DRAFT,
    }
    box = box_map.get(request.args.get("box", "inbox"), BoxTypeEnum.LOCAL_INBOX)
    status_flt = request.args.get("status", "all")
    page = parse_positive_int(request.args.get("page", 1), 1)
    count = min(parse_positive_int(request.args.get("count", 50), 50), 50)

    with get_client() as c:
        messages, total = fetch_modem_messages(c, box, page, count)

    if status_flt == "unread":
        messages = [m for m in messages if str(m.get("Smstat", "1")) == "0"]
    elif status_flt == "read":
        messages = [m for m in messages if str(m.get("Smstat", "0")) == "1"]

    return jsonify(
        {"total": total, "page": page, "count": len(messages), "messages": messages}
    )


@app.get("/sms/history")
@require_auth
def sms_history():
    """
    Query full SMS history from SQLite (all ever received, including deleted from modem).
    Query: phone=+251..., search=keyword, from=YYYY-MM-DD, to=YYYY-MM-DD,
           type=all|sms|delivery_report, page=1, limit=50
    """
    db = get_db()
    phone = request.args.get("phone")
    search = request.args.get("search")
    from_dt = request.args.get("from")
    to_dt = request.args.get("to")
    msg_type = request.args.get("type", "all")
    page = parse_positive_int(request.args.get("page", 1), 1)
    limit = min(parse_positive_int(request.args.get("limit", 50), 50), 200)
    offset = (page - 1) * limit

    clauses = []
    params = []

    if phone:
        clauses.append("phone LIKE ?")
        params.append(f"%{phone}%")
    if search:
        clauses.append("content LIKE ?")
        params.append(f"%{search}%")
    if from_dt:
        clauses.append("date >= ?")
        params.append(from_dt)
    if to_dt:
        clauses.append("date <= ?")
        params.append(to_dt + " 23:59:59")
    if msg_type == "sms":
        clauses.append("is_delivery_report = 0")
    elif msg_type == "delivery_report":
        clauses.append("is_delivery_report = 1")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    total = db.execute(f"SELECT COUNT(*) FROM messages {where}", params).fetchone()[0]
    rows = db.execute(
        f"SELECT * FROM messages {where} ORDER BY date DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return jsonify(
        {
            "total": total,
            "page": page,
            "limit": limit,
            "messages": [dict(r) for r in rows],
        }
    )


@app.get("/sms/unread/count")
@require_auth
def unread_count():
    """Quick SMS counts from modem (unread, total, SIM capacity)."""
    with get_client() as c:
        counts = c.sms.sms_count()
    return jsonify(counts)


@app.get("/sms/<int:index>")
@require_auth
def get_sms(index):
    """Fetch a single message from modem by index and mark it read."""
    with get_client() as c:
        messages, _ = fetch_modem_messages(c)
        msg = next((m for m in messages if int(m.get("Index", -1)) == index), None)
        if not msg:
            return jsonify({"error": f"Message index {index} not found"}), 404
        c.sms.set_read(index)
    return jsonify(msg)


@app.post("/sms/send")
@require_auth
def send_sms():
    """
    Send an SMS with optional delivery report request.
    Body JSON: {"to": "+251912345678", "message": "Hello", "delivery_report": true}
    'to' can be a string or list of strings.
    """
    body = request.get_json(force=True)
    if not body or "to" not in body or "message" not in body:
        return jsonify({"error": "Requires 'to' and 'message' fields"}), 400

    recipients = body["to"] if isinstance(body["to"], list) else [body["to"]]
    message = body["message"]
    delivery_report = bool(body.get("delivery_report", True))

    with get_client() as c:
        # huawei-lte-api 1.11.0 does not expose a delivery-report flag in send_sms().
        result = c.sms.send_sms(recipients, message)
    log.info("SMS sent to %s (dr=%s): %s", recipients, delivery_report, result)

    # Store in sent_messages table
    with get_bg_db() as db:
        db.execute(
            "INSERT INTO sent_messages(recipients, content) VALUES(?,?)",
            (json.dumps(recipients), message),
        )

    return jsonify(
        {
            "result": result,
            "to": recipients,
            "message": message,
            "delivery_report": delivery_report,
        }
    )


@app.get("/sms/sent")
@require_auth
def sent_history():
    """List sent messages history from SQLite."""
    db = get_db()
    page = parse_positive_int(request.args.get("page", 1), 1)
    limit = min(parse_positive_int(request.args.get("limit", 50), 50), 200)
    offset = (page - 1) * limit
    total = db.execute("SELECT COUNT(*) FROM sent_messages").fetchone()[0]
    rows = db.execute(
        "SELECT * FROM sent_messages ORDER BY sent_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return jsonify({"total": total, "page": page, "messages": [dict(r) for r in rows]})


@app.post("/sms/mark-read/<int:index>")
@require_auth
def mark_read(index):
    """Mark a specific SMS as read on the modem."""
    with get_client() as c:
        result = c.sms.set_read(index)
    return jsonify({"index": index, "result": result})


@app.delete("/sms/<int:index>")
@require_auth
def delete_sms(index):
    """Delete a single SMS from modem by index."""
    with get_client() as c:
        result = c.sms.delete_sms(index)
    log.info("Deleted modem SMS index %d", index)
    return jsonify({"deleted": index, "result": result})


@app.delete("/sms/inbox/all")
@require_auth
def delete_all_inbox():
    """
    Delete ALL inbox messages from modem. Stores them in SQLite first.
    Requires header: X-Confirm: yes
    """
    if request.headers.get("X-Confirm") != "yes":
        return jsonify(
            {"error": "Add header 'X-Confirm: yes' to confirm bulk delete"}
        ), 400

    with get_client() as c:
        messages, _ = fetch_modem_messages(c)

        with get_bg_db() as db:
            for msg in messages:
                store_message(db, msg)

        deleted = []
        for msg in messages:
            idx = msg.get("Index")
            if idx is not None:
                try:
                    c.sms.delete_sms(int(idx))
                    deleted.append(int(idx))
                except Exception as e:
                    log.warning("Failed to delete index %s: %s", idx, e)

    return jsonify(
        {
            "stored": len(messages),
            "deleted_count": len(deleted),
            "deleted_indexes": deleted,
        }
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS — USSD (automated)
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/ussd/send")
@require_auth
def ussd_send():
    """
    Send a USSD code and wait for the response.
    Body JSON: {"code": "*100#"}
    Returns 423 if a live interactive session is in progress.
    """
    body = request.get_json(force=True)
    if not body or "code" not in body:
        return jsonify({"error": "Requires 'code' field"}), 400

    if not ussd_lock.acquire(blocking=False):
        return jsonify({"error": "A USSD session is in progress. End it first."}), 423

    try:
        with get_client() as c:
            code = str(body["code"])
            log.info("USSD send: %s", code)
            c.ussd.send(code)
            try:
                content = poll_ussd_response(c)
                return jsonify({"code": code, "response": content})
            except TimeoutError:
                return jsonify({"code": code, "error": "Timed out"}), 504
    except Exception as e:
        log.warning("USSD send failed: %s", e)
        return jsonify({"code": body.get("code"), "error": str(e)}), 502
    finally:
        ussd_lock.release()


@app.post("/ussd/session")
@require_auth
def ussd_session():
    """
    Run a full multi-step USSD session in one HTTP call.
    Body JSON: {"steps": ["*999#", "1", "2"]}
    Returns 423 if a live interactive session is in progress.
    """
    body = request.get_json(force=True)
    if not body or "steps" not in body or not body["steps"]:
        return jsonify({"error": "Requires 'steps' list"}), 400

    if not ussd_lock.acquire(blocking=False):
        return jsonify({"error": "A USSD session is in progress. End it first."}), 423

    try:
        with get_client() as c:
            history = []
            for i, step in enumerate(body["steps"]):
                step_text = str(step)
                log.info(
                    "USSD session step %d/%d: %s", i + 1, len(body["steps"]), step_text
                )
                try:
                    c.ussd.send(step_text)
                    content = poll_ussd_response(c)
                    history.append(
                        {"step": i + 1, "input": step_text, "response": content}
                    )
                    if i < len(body["steps"]) - 1:
                        time.sleep(0.5)
                except TimeoutError:
                    history.append(
                        {"step": i + 1, "input": step_text, "error": "Timed out"}
                    )
                    break
                except Exception as e:
                    history.append({"step": i + 1, "input": step_text, "error": str(e)})
                    break

        return jsonify({"steps_run": len(history), "history": history})
    finally:
        ussd_lock.release()


# ═══════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET — LIVE INTERACTIVE USSD
# ═══════════════════════════════════════════════════════════════════════════════


@sock.route("/ussd/live")
def ussd_live(ws):
    """
    Live interactive USSD over WebSocket.

    Protocol (all messages are JSON strings):
      Client → Server:
        {"code": "*999#"}        — start a new session with this USSD code
        {"input": "1"}           — send a menu reply
        {"action": "cancel"}     — cancel session and disconnect
        {"action": "ping"}       — keepalive ping

      Server → Client:
        {"status": "ready"}                   — session started, send your first code
        {"menu": "...response text..."}       — network responded with this menu/text
        {"status": "session_ended"}           — session finished (final response received)
        {"error": "..."}                      — something went wrong
        {"status": "busy"}                    — another session is active, try later
    """
    global ussd_session_ws

    if not ussd_lock.acquire(blocking=False):
        ws.send(
            json.dumps({"status": "busy", "error": "Another USSD session is active"})
        )
        return

    ussd_session_ws = ws
    IDLE_TIMEOUT = 120  # seconds of no input before auto-kill

    try:
        ws.send(
            json.dumps(
                {"status": "ready", "message": 'Send {"code": "*XXX#"} to begin'}
            )
        )
        with get_client() as client:
            last_activity = time.time()

            while True:
                # Check idle timeout
                if time.time() - last_activity > IDLE_TIMEOUT:
                    ws.send(
                        json.dumps(
                            {
                                "status": "timeout",
                                "error": "Session timed out after inactivity",
                            }
                        )
                    )
                    break

                try:
                    raw = ws.receive(timeout=5)
                except Exception:
                    # receive timeout, loop back to check idle
                    continue

                if raw is None:
                    # Client disconnected
                    log.info("USSD live: client disconnected")
                    break

                last_activity = time.time()

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    ws.send(json.dumps({"error": "Invalid JSON"}))
                    continue

                # Ping keepalive
                if msg.get("action") == "ping":
                    ws.send(json.dumps({"status": "pong"}))
                    continue

                # Cancel
                if msg.get("action") == "cancel":
                    try:
                        client.ussd.cancel()
                    except Exception:
                        pass
                    ws.send(json.dumps({"status": "cancelled"}))
                    break

                # Start session or reply
                code = msg.get("code") or msg.get("input")
                if not code:
                    ws.send(
                        json.dumps(
                            {"error": 'Send {"code": "*XXX#"} or {"input": "1"}'}
                        )
                    )
                    continue

                log.info("USSD live send: %s", code)
                client.ussd.send(str(code))

                try:
                    content = poll_ussd_response(client)
                    ws.send(json.dumps({"menu": content}))
                except TimeoutError:
                    ws.send(
                        json.dumps({"error": "No response from network within timeout"})
                    )
                    break

            try:
                client.ussd.cancel()
            except Exception:
                pass

    except Exception as e:
        log.error("USSD live error: %s", e)
        try:
            ws.send(json.dumps({"error": str(e)}))
        except Exception:
            pass
    finally:
        ussd_session_ws = None
        ussd_lock.release()
        log.info("USSD live session ended, lock released")


# ═══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS — DEVICE
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/device/info")
@require_auth
def device_info():
    """Device info, signal strength, and connection status."""
    with get_client() as c:
        monitoring = c.monitoring.status()
        device = c.device.information()
    return jsonify(
        {
            "device": device,
            "signal": monitoring,
            "status": monitoring,
        }
    )


@app.post("/device/reboot")
@require_auth
def device_reboot():
    """Reboot the modem. It will be unreachable for ~30 seconds."""
    if request.headers.get("X-Confirm") != "yes":
        return jsonify({"error": "Add header 'X-Confirm: yes' to confirm reboot"}), 400
    with get_client() as c:
        result = c.device.reboot()
    log.info("Modem reboot triggered: %s", result)
    return jsonify(
        {"result": result, "note": "Modem will be unreachable for ~30 seconds"}
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  REST ENDPOINTS — ROUTES LISTING
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/routes")
def list_routes():
    """List all API endpoints (no auth needed)."""
    routes = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint == "static":
            continue
        fn = app.view_functions[rule.endpoint]
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        routes.append(
            {
                "methods": sorted(
                    m for m in rule.methods if m not in ("HEAD", "OPTIONS")
                ),
                "path": rule.rule,
                "summary": doc,
            }
        )
    return jsonify(routes)


# ═══════════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════════════════


def start_background_threads():
    threading.Thread(target=sms_poller, daemon=True, name="sms-poller").start()
    threading.Thread(target=cleanup_job, daemon=True, name="cleanup-job").start()
    log.info("Background threads started")


def bootstrap_runtime():
    global runtime_started

    with runtime_lock:
        if runtime_started:
            return

        validate_runtime_config()
        init_db()
        start_background_threads()
        runtime_started = True


if __name__ == "__main__":
    bootstrap_runtime()
    log.info("=" * 60)
    log.info("SMS Gateway on http://%s:%d", APP_HOST, APP_PORT)
    log.info("WebSocket USSD live: ws://%s:%d/ussd/live", APP_HOST, APP_PORT)
    log.info("Auth header required: X-Admin-Key")
    log.info("Health endpoint: GET /health/modem")
    log.info("GET /routes for full endpoint list")
    log.info("=" * 60)
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
