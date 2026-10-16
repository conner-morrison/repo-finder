#!/usr/bin/env python3
"""repo-finder worker.

A local web UI (http://127.0.0.1:5000) plus a background worker that joins a
relay workspace and then listens on it.

Lifecycle, as shown in the UI:
  unregistered -> waiting      POST {server}/enrol with our name, get a ticket
  waiting      -> no_channel   the server approved us and gave us an enrolled id
  no_channel   -> active       the server put us in a channel
  waiting      -> declined     the request was refused or expired

The relay cannot call us (we have no public address), so every "the server lets
the app know" is a long poll: GET .../enrol/{ticket}?wait=N while waiting, and
GET .../messages?wait=N once enrolled.

Standard library only. Run:  python3 app.py        (debug mode is on by default)
"""

import json
import queue
import re
import uuid
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import analyzer
import finder

APP_NAME = "repo-finder"
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))
DEBUG = os.environ.get("DEBUG", "1") not in ("0", "false", "False", "")
DEFAULT_SERVER = "https://relay-production-7643.up.railway.app/upwork"

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
STATE_FILE = ROOT / "state.json"

LONG_POLL = 25          # seconds the relay may hold a poll open
MIN_POLL_GAP = 3        # never ask more often than this, even if answered at once
RETRY_AFTER_ERROR = 5
LOG_MAX = 300
MESSAGES_MAX = 200


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def debug(*args):
    if DEBUG:
        print(f"[{datetime.now():%H:%M:%S}] [debug]", *args, flush=True)


# --- state ------------------------------------------------------------------
# One dict, saved to state.json after every change, so a restart (including the
# debug reloader's) picks up where it left off: a ticket keeps waiting, an id
# keeps listening.

DEFAULT_STATE = {
    "server_url": DEFAULT_SERVER,
    "name": APP_NAME,
    "label": f"{APP_NAME} on {socket.gethostname()}",
    "status": "unregistered",
    "ticket": None,
    "worker_id": None,
    "channels": [],
    "requested_at": None,
    "enrolled_at": None,
    "channel_at": None,
    "last_contact": None,
    "last_error": None,
    "last_response": None,
    "stats": {"pings": 0, "polls": 0, "errors": 0, "messages": 0},
    "messages": [],
    "members": [],
    "matches": {},
    "log": [],
}

lock = threading.RLock()
wake = threading.Event()


def load_state():
    state = json.loads(json.dumps(DEFAULT_STATE))
    if STATE_FILE.exists():
        try:
            saved = json.loads(STATE_FILE.read_text())
            state.update({k: v for k, v in saved.items() if k in state})
            state["stats"] = {**DEFAULT_STATE["stats"], **(saved.get("stats") or {})}
        except (OSError, ValueError) as exc:
            print(f"state.json unreadable, starting fresh: {exc}", file=sys.stderr)
    return state


STATE = load_state()


def save():
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, indent=2))
    tmp.replace(STATE_FILE)


def log(level, text):
    with lock:
        STATE["log"].append({"at": now(), "level": level, "text": text})
        del STATE["log"][:-LOG_MAX]
        save()
    print(f"[{datetime.now():%H:%M:%S}] [{level}] {text}", flush=True)


def set_status(status, text=None, level="info"):
    with lock:
        changed = STATE["status"] != status
        STATE["status"] = status
        save()
    if changed:
        log(level, text or f"Status is now {status}")


# --- relay client -------------------------------------------------------------

def relay(method, path, body=None, token=None, timeout=15):
    """Call the relay. Returns (http_status, parsed_json_or_text). Raises only on
    network failure; HTTP errors come back as their status and body."""
    url = STATE["server_url"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"{APP_NAME}-worker")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    debug("->", method, url, body if body is not None else "")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            code, raw = res.status, res.read()
    except urllib.error.HTTPError as err:
        code, raw = err.code, err.read()
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text else {}
    except ValueError:
        parsed = text
    debug("<-", code, text[:500])
    with lock:
        STATE["last_contact"] = now()
        STATE["last_response"] = {"at": now(), "request": f"{method} {path}", "status": code, "body": parsed}
        STATE["last_error"] = None
    return code, parsed


def field(data, *names):
    if isinstance(data, dict):
        for n in names:
            if data.get(n) not in (None, ""):
                return data[n]
    return None


def register():
    """The ping: ask the relay to let us join. The answer is a ticket."""
    with lock:
        body = {"name": STATE["name"], "label": STATE["label"]}
        STATE["stats"]["pings"] += 1
    log("info", f"Registering as '{body['name']}' with {STATE['server_url']}")
    code, data = relay("POST", "/enrol", body)
    ticket = field(data, "ticket", "ref", "id")
    if code >= 300 or not ticket:
        msg = field(data, "message", "detail") or f"HTTP {code}"
        raise RuntimeError(f"Registration refused: {msg}")
    with lock:
        STATE.update(ticket=ticket, worker_id=None, channels=[], requested_at=now(),
                     enrolled_at=None, channel_at=None)
    set_status("waiting", "Registration sent. Waiting for the server to approve it.")
    wake.set()


def poll_enrolment():
    code, data = relay("GET", f"/enrol/{STATE['ticket']}?wait={LONG_POLL}", timeout=LONG_POLL + 15)
    with lock:
        STATE["stats"]["polls"] += 1
    status = str(field(data, "status") or "").lower()
    worker_id = field(data, "worker_id", "id")
    if code == 200 and worker_id and status not in ("pending", "waiting"):
        with lock:
            STATE.update(worker_id=worker_id, ticket=None, enrolled_at=now())
        log("ok", f"Approved. Enrolled id allocated: {worker_id}")
        set_status("no_channel", "Enrolled, but the server has not allocated a channel yet.", "warn")
    elif code == 404 or status in ("unknown", "declined", "rejected", "refused", "expired"):
        with lock:
            STATE["ticket"] = None
        set_status("declined", field(data, "message") or "Registration was declined or expired.", "error")


def channels_from(data):
    """What the relay says about our channels, or None if it said nothing."""
    chans = field(data, "channels", "memberships")
    if isinstance(chans, list):
        return [c.get("name", c.get("channel", str(c))) if isinstance(c, dict) else str(c) for c in chans]
    if isinstance(chans, dict):
        return list(chans)
    return None


def poll_messages():
    code, data = relay("GET", f"/messages?wait={LONG_POLL}", token=STATE["worker_id"], timeout=LONG_POLL + 15)
    with lock:
        STATE["stats"]["polls"] += 1
    status = str(field(data, "status") or "").lower()

    if code == 401:
        with lock:
            STATE.update(worker_id=None, channels=[])
        set_status("unregistered", f"The server no longer knows this id ({field(data, 'message') or status}). "
                                   "Register again.", "error")
        return

    msgs = data if isinstance(data, list) else (field(data, "messages", "items") or [])
    handle_notices(field(data, "notices") or [])
    chans = channels_from(data)
    if chans is None and STATE["channels"]:
        chans = list(STATE["channels"])
    no_channel_hint = code in (403, 409) or ("channel" in status and status not in ("ok", "active")) \
        or (chans is not None and not chans)

    if chans:
        with lock:
            STATE["channels"] = chans
    if msgs or chans:
        if STATE["status"] != "active":
            with lock:
                STATE["channel_at"] = now()
            set_status("active", f"Channel allocated{': ' + ', '.join(chans) if chans else ''}. Working.", "ok")
    elif no_channel_hint and STATE["status"] != "no_channel":
        set_status("no_channel", "The server has taken this worker out of every channel.", "warn")

    if msgs:
        handle_messages(msgs)


def handle_notices(notices):
    """The relay says what happened to us as notices, each sent once, e.g.
    {"kind": "joined", "channel": "github", "message": "added to #github"}."""
    for n in notices:
        if not isinstance(n, dict):
            continue
        kind, ch = str(n.get("kind", "")).lower(), n.get("channel")
        log("info", f"Notice: {n.get('message') or kind}")
        if not ch:
            continue
        with lock:
            if kind in ("joined", "added") and ch not in STATE["channels"]:
                STATE["channels"].append(ch)
            elif kind in ("left", "removed", "kicked", "parted") and ch in STATE["channels"]:
                STATE["channels"].remove(ch)
            save()
    if notices and not STATE["channels"] and STATE["status"] == "active":
        set_status("no_channel", "The server has taken this worker out of every channel.", "warn")


def handle_messages(msgs):
    latest = {}
    with lock:
        for m in msgs:
            entry = {"received_at": now(), **(m if isinstance(m, dict) else {"body": m})}
            STATE["messages"].append(entry)
            ch, seq = entry.get("channel"), entry.get("seq")
            if ch is not None and isinstance(seq, int):
                latest[ch] = max(seq, latest.get(ch, seq))
        del STATE["messages"][:-MESSAGES_MAX]
        STATE["stats"]["messages"] += len(msgs)
        save()
    log("info", f"Received {len(msgs)} message(s)")
    plan_jobs()
    for ch, seq in latest.items():
        code, data = relay("POST", "/ack", {"channel": ch, "seq": seq}, token=STATE["worker_id"])
        if code >= 300:
            log("warn", f"Ack for {ch}#{seq} failed: HTTP {code}")


def worker_loop():
    while True:
        started = time.monotonic()
        try:
            st = STATE["status"]
            if st == "waiting" and STATE["ticket"]:
                poll_enrolment()
            elif st in ("no_channel", "active") and STATE["worker_id"]:
                poll_messages()
            else:
                wake.wait()
                wake.clear()
                continue
        except Exception as exc:  # network trouble: say so and try again shortly
            with lock:
                STATE["last_error"] = {"at": now(), "text": str(exc)}
                STATE["stats"]["errors"] += 1
                save()
            log("error", f"Server unreachable: {exc}")
            wake.wait(RETRY_AFTER_ERROR)
            wake.clear()
            continue
        with lock:
            save()
        gap = MIN_POLL_GAP - (time.monotonic() - started)
        if gap > 0:
            wake.wait(gap)
            wake.clear()


# --- local web server -----------------------------------------------------------

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class BadRequest(ValueError):
    pass


def member_from(body):
    """A member is a name, the months they were with us (from, and to - empty
    while they still are), and how similar a repo must be to count for them."""
    name = str(body.get("name") or "").strip()
    start, end = str(body.get("from") or ""), str(body.get("to") or "")
    if not name:
        raise BadRequest("Name is required")
    if not MONTH.match(start):
        raise BadRequest("'From' must be a month (YYYY-MM)")
    if end and not MONTH.match(end):
        raise BadRequest("'To' must be a month (YYYY-MM)")
    if end and end < start:
        raise BadRequest("'To' can't be before 'From'")
    try:
        similarity = float(body.get("similarity"))
    except (TypeError, ValueError):
        raise BadRequest("Similarity must be a number from 0 to 1")
    if not 0 <= similarity <= 1:
        raise BadRequest("Similarity must be from 0 to 1")
    return {"name": name[:120], "from": start, "to": end or None, "similarity": round(similarity, 2)}


def add_member(body):
    member = member_from(body)
    with lock:
        STATE["members"].append({"id": uuid.uuid4().hex[:12], **member, "added_at": now()})
        save()
    log("info", f"Member added: {member['name']} (similarity ≥ {member['similarity']:.2f})")
    plan_jobs()


def update_member(body):
    member = member_from(body)
    with lock:
        target = next((m for m in STATE["members"] if m["id"] == body.get("id")), None)
        if not target:
            raise BadRequest("No such member")
        target.update(member)
        save()
    log("info", f"Member updated: {member['name']} (similarity ≥ {member['similarity']:.2f})")
    plan_jobs()


# --- related jobs -> similar repos -------------------------------------------------
# A job is related to a member when its person is that member and it was posted
# within the member's from-to months. For each related job the finder looks for
# up to two GitHub repos whose similarity reaches the member's value, keeps them
# with the job (shown on its card) and posts them back to the job's channel as a
# reply carrying the job id, which is how the relay ties an answer to a job.

jobs_queue = queue.Queue()
queued = set()
ACTIVE_MATCH = ("queued", "searching")


def same_person(a, b):
    a, b = " ".join(a.lower().split()), " ".join(b.lower().split())
    if not a or not b:
        return False
    # "Zachary" registered, "Zachary Wing" on the job (or the other way) is the same person
    return a == b or (" " not in a and b.split()[0] == a) or (" " not in b and a.split()[0] == b)


def jobs_by_id():
    """Every job seen, merged by job id: the same job is often posted again
    (a rerun) with less in it, so each field comes from whichever post has it."""
    jobs = {}
    for m in STATE["messages"]:
        b = m.get("body") if isinstance(m.get("body"), dict) else {}
        jid = b.get("job")
        if not jid or not b.get("title") or m.get("sender") == STATE["name"]:
            continue
        j = jobs.setdefault(jid, {"job_id": jid, "title": b["title"], "description": "", "person": None,
                                  "channel": m.get("channel"), "ts": m.get("ts"), "seqs": []})
        j["seqs"].append(m.get("seq"))
        if len(b.get("description") or "") > len(j["description"]):
            j["description"], j["title"] = b["description"], b["title"]
        j["person"] = j["person"] or b.get("person")
        if m.get("ts") and (not j["ts"] or m["ts"] < j["ts"]):
            j["ts"] = m["ts"]
    return jobs


def member_for(job):
    """The member this job is related to, and if none, why not."""
    if not job["person"]:
        return None, "no person on this job"
    month = time.strftime("%Y-%m", time.localtime(job["ts"])) if job["ts"] else time.strftime("%Y-%m")
    named = [m for m in STATE["members"] if same_person(m["name"], job["person"])]
    if not named:
        return None, f"{job['person']} is not a registered member"
    for m in named:
        if m["from"] <= month <= (m["to"] or "9999-12"):
            return m, None
    return None, f"posted {month}, outside {job['person']}'s registered period"


def plan_jobs(force=None):
    """Decide what each job needs, and queue the ones that need a search (or a
    reply that has not reached the server yet)."""
    todo = []
    with lock:
        for jid, job in jobs_by_id().items():
            member, why = member_for(job)
            cur = STATE["matches"].get(jid)
            base = {"job_id": jid, "person": job["person"], "channel": job["channel"]}
            if not member:
                if not cur or cur["status"] not in ("found",) + ACTIVE_MATCH:
                    STATE["matches"][jid] = {**base, "status": "unrelated", "reason": why, "updated_at": now()}
                continue
            base.update(member=member["name"], member_id=member["id"])
            if member.get("similarity") is None:
                STATE["matches"][jid] = {**base, "status": "needs_threshold", "updated_at": now()}
                continue
            if cur and cur["status"] in ACTIVE_MATCH:
                continue
            period = [member["from"], member["to"]]
            stale = (not cur or cur["status"] in ("unrelated", "needs_threshold", "error")
                     or cur.get("threshold") != member["similarity"] or cur.get("period") != period
                     or (cur["status"] == "found" and any("analysis" not in r for r in cur.get("repos") or [])))
            if stale or force == jid:
                STATE["matches"][jid] = {**base, "status": "queued", "threshold": member["similarity"],
                                         "period": period, "updated_at": now()}
                todo.append(("search", jid))
            elif cur["status"] == "found" and (cur.get("reply") or {}).get("status") != "sent":
                todo.append(("reply", jid))
        save()
    for item in todo:
        if item not in queued:
            queued.add(item)
            jobs_queue.put(item)


def urls_in(value, out):
    if isinstance(value, dict):
        for v in value.values():
            urls_in(v, out)
    elif isinstance(value, list):
        for v in value:
            urls_in(v, out)
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        out.add(value)
    return out


def search_job(jid):
    job = jobs_by_id().get(jid)
    with lock:
        match = STATE["matches"].get(jid)
        if not job or not match:
            return
        match.update(status="searching", updated_at=now())
        save()
    threshold, period = match["threshold"], match["period"]
    log("info", f"Searching GitHub for repos like job {jid} — “{job['title']}” (similarity ≥ {threshold:.2f}, "
                f"created and last pushed within {period[0]} – {period[1] or 'now'})")
    # Repos already posted on the channel (another worker's output for these
    # jobs) are answers, not examples to point at.
    exclude = urls_in([m.get("body") for m in STATE["messages"]], set())
    try:
        res = finder.find_similar(job["title"], job["description"], threshold, exclude=exclude,
                                  period=tuple(period), analyze=analyzer.analyze,
                                  log=lambda t: debug("finder", t))
    except Exception as exc:
        with lock:
            match.update(status="error", error=str(exc), updated_at=now())
            save()
        log("error", f"Search for job {jid} failed: {exc}")
        return
    with lock:
        match.update(status="found" if res["repos"] else "none", repos=res["repos"], best_score=res["best_score"],
                     considered=res["considered"], profile=res["profile"], searched_at=now(), reply=None,
                     error=None, updated_at=now())
        save()
    if res["repos"]:
        log("ok", f"Job {jid}: {len(res['repos'])} similar repo(s) — "
                  + ", ".join(f"{r['name']} ({r['score']:.2f})" for r in res["repos"]))
        send_reply(jid)
    else:
        log("warn", f"Job {jid}: no repo within the period reached {threshold:.2f} "
                    f"(best {res['best_score']:.2f} of {res['considered']})")


def send_reply(jid):
    with lock:
        match = STATE["matches"].get(jid)
        if not match or match["status"] != "found":
            return
        if not STATE["worker_id"] or STATE["status"] != "active":
            match["reply"] = {"status": "pending", "error": "worker is not active on a channel yet", "at": now()}
            save()
            return
        # Each repo goes with its url and what it is made of. Nothing about who
        # owns or wrote it: no owner, contributors, licence or popularity.
        body = {
            "event": "similar_repos",
            "job_id": jid,
            "person": match["person"],
            "worker": STATE["name"],
            "threshold": match["threshold"],
            "period": {"from": match["period"][0], "to": match["period"][1] or "present"},
            "links": [{"url": r["url"], "label": f"{r['name'].split('/')[-1]} · similarity {r['score']:.2f}"}
                      for r in match["repos"]],
            "repos": [{
                "url": r["url"],
                "similarity": r["score"],
                "created": r.get("created"),
                "last_push": r.get("last_push"),
                **(r.get("analysis") or {}),
                "description": analyzer.as_text(r.get("analysis") or {}) or r.get("description"),
            } for r in match["repos"]],
            "at": now(),
        }
        channel = match["channel"]
    code, data = relay("POST", "/publish", {"channel": channel, "body": body}, token=STATE["worker_id"])
    ok = code < 300
    with lock:
        match["reply"] = {"status": "sent" if ok else "failed", "code": code, "at": now(),
                          "seq": field(data, "seq") if ok else None,
                          "error": None if ok else (field(data, "message", "detail") or f"HTTP {code}")}
        save()
    if ok:
        log("ok", f"Replied to job {jid} on #{channel} with {len(body['links'])} repo link(s)")
    else:
        log("error", f"Reply to job {jid} on #{channel} refused: {match['reply']['error']}")


def finder_loop():
    while True:
        item = jobs_queue.get()
        queued.discard(item)
        kind, jid = item
        try:
            search_job(jid) if kind == "search" else send_reply(jid)
        except Exception as exc:
            log("error", f"{kind} for job {jid} failed: {exc}")


def public_state():
    with lock:
        s = json.loads(json.dumps(STATE))
    s.update(app=APP_NAME, debug=DEBUG, host=f"http://{HOST}:{PORT}")
    if not DEBUG:
        s.pop("last_response", None)
    return s


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/0.1"

    def log_message(self, fmt, *args):
        if DEBUG and not self.path.startswith("/api/state"):
            debug("http", self.address_string(), fmt % args)

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        if self.path.startswith("/api/state"):
            return self.send_json(public_state())
        name = "index.html" if self.path in ("/", "") else self.path.lstrip("/").split("?")[0]
        file = (STATIC / name).resolve()
        if STATIC not in file.parents or not file.is_file():
            return self.send_json({"error": "not found"}, 404)
        types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types.get(file.suffix, "application/octet-stream") + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store" if DEBUG else "max-age=60")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.read_json()
        try:
            if self.path == "/api/register":
                if STATE["status"] in ("waiting", "no_channel", "active"):
                    return self.send_json({"error": "already registered or waiting"}, 409)
                register()
            elif self.path == "/api/cancel":
                with lock:
                    STATE.update(ticket=None, worker_id=None, channels=[])
                set_status("unregistered", "Registration forgotten locally.", "warn")
            elif self.path == "/api/profile":
                registered = STATE["status"] in ("waiting", "no_channel", "active")
                with lock:
                    for key in ("label",) if registered else ("name", "label", "server_url"):
                        if isinstance(body.get(key), str) and body[key].strip():
                            STATE[key] = body[key].strip()
                    save()
                log("info", "Profile updated")
            elif self.path == "/api/members":
                add_member(body)
            elif self.path == "/api/members/update":
                update_member(body)
            elif self.path == "/api/members/delete":
                with lock:
                    STATE["members"] = [m for m in STATE["members"] if m["id"] != body.get("id")]
                    save()
                plan_jobs()
            elif self.path == "/api/jobs/search":
                if body.get("job_id") not in jobs_by_id():
                    raise BadRequest("No such job")
                plan_jobs(force=body["job_id"])
            elif self.path == "/api/clear-log":
                with lock:
                    STATE["log"], STATE["messages"] = [], []
                    save()
            else:
                return self.send_json({"error": "not found"}, 404)
        except BadRequest as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            log("error", str(exc))
            return self.send_json({"error": str(exc)}, 502)
        self.send_json(public_state())


def serve():
    with lock:   # a search cut short by a restart starts again
        for m in STATE["matches"].values():
            if m["status"] in ACTIVE_MATCH:
                m["status"] = "error"
                m["error"] = "interrupted by a restart"
    threading.Thread(target=worker_loop, name="worker", daemon=True).start()
    threading.Thread(target=finder_loop, name="finder", daemon=True).start()
    plan_jobs()
    wake.set()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f" * {APP_NAME} worker on http://{HOST}:{PORT}  (debug={'on' if DEBUG else 'off'})", flush=True)
    print(f" * relay: {STATE['server_url']}  status: {STATE['status']}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


# --- debug reloader ---------------------------------------------------------------
# Like Flask's: the parent watches the source files and restarts the child when
# one changes. State survives through state.json.

def watched_mtimes():
    files = list(ROOT.glob("*.py")) + list(STATIC.glob("*"))
    return {f: f.stat().st_mtime for f in files if f.is_file()}


def run_with_reloader():
    print(" * Debug mode: on — restarting on file changes", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    while True:
        env = {**os.environ, "REPO_FINDER_CHILD": "1"}
        child = subprocess.Popen([sys.executable, __file__] + sys.argv[1:], env=env)
        before = watched_mtimes()
        try:
            while child.poll() is None:
                time.sleep(1)
                if watched_mtimes() != before:
                    print(" * Detected change, reloading", flush=True)
                    child.terminate()
                    child.wait()
                    break
            else:
                sys.exit(child.returncode)
        except KeyboardInterrupt:
            child.terminate()
            child.wait()
            return


if __name__ == "__main__":
    if DEBUG and os.environ.get("REPO_FINDER_CHILD") != "1":
        run_with_reloader()
    else:
        serve()
