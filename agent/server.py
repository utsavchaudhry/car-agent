"""Dashboard API + background scheduler, in one process.

    uvicorn server:app --host 0.0.0.0 --port 8080

The scheduler runs scan.py / digest.py / rescore.py as subprocesses, so each
run reads fresh settings and a crash in a scraper cannot take the dashboard
down with it.
"""
import os, sys, json, base64, random, secrets, datetime, threading, subprocess

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config, store, contact, notify

FUELS = {"petrol", "diesel", "hybrid", "electric", "gas"}
SELLERS = {"private", "dealer"}

LOG = os.path.join(config.DATA_DIR, "agent.log")
WEB = os.environ.get("WEB_DIR") or os.path.join(os.path.dirname(HERE), "web", "dist")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


def now():
    return datetime.datetime.now()


# ------------------------------------------------------------------ scheduler
class Scheduler:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = None
        self.last = {}                       # job -> {at, ok, output}
        self.next_scan = now() + datetime.timedelta(seconds=45)
        self.last_digest = self._last_digest_date()

    @staticmethod
    def _last_digest_date():
        try:
            c = store.conn()
            row = c.execute("SELECT MAX(ts) t FROM runs WHERE source='digest'").fetchone()
            c.close()
            return row["t"][:10] if row and row["t"] else None
        except Exception:
            return None

    def _interval(self, t):
        s = config.SCHEDULE
        start, end = int(s["day_start_hour"]), int(s["day_end_hour"])
        day = (start <= t.hour < end) if start < end else (t.hour >= start or t.hour < end)
        return int(s["interval_min"]) if day else int(s["night_interval_min"])

    def _compute_next(self, t):
        mins = self._interval(t)
        if mins > 0:
            # Jitter, so the portals never see a request on the exact minute.
            return t + datetime.timedelta(minutes=mins, seconds=random.randint(0, 90))
        # Night scans switched off: sleep until the day window opens.
        start = int(config.SCHEDULE["day_start_hour"])
        nxt = t.replace(hour=start % 24, minute=0, second=0, microsecond=0)
        if nxt <= t:
            nxt += datetime.timedelta(days=1)
        return nxt

    def run(self, job):
        """Run one job synchronously. Returns False if another job is running."""
        if not self.lock.acquire(blocking=False):
            return False
        self.running = job
        started = now()
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, f"{job}.py")],
                               cwd=HERE, capture_output=True, text=True, timeout=1800)
            out = (p.stdout + p.stderr).strip()
            ok = p.returncode == 0
        except subprocess.TimeoutExpired:
            out, ok = "timed out after 30 min", False
        finally:
            self.running = None
            self.lock.release()
        self.last[job] = {"at": started.isoformat(timespec="seconds"), "ok": ok,
                          "output": out[-2000:]}
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{started:%Y-%m-%d %H:%M:%S} [{job}] {'ok' if ok else 'FAILED'}\n{out}\n")
        return True

    def start_background(self, job):
        if self.running:
            return False
        threading.Thread(target=self.run, args=(job,), daemon=True).start()
        return True

    def loop(self):
        while True:
            try:
                t = now()
                if t >= self.next_scan:
                    self.run("scan")
                    self.next_scan = self._compute_next(now())
                t = now()
                due = config.SCHEDULE.get("digest_time") or "23:55"
                if t.strftime("%H:%M") >= due and self.last_digest != t.date().isoformat():
                    if self.run("digest"):
                        self.last_digest = t.date().isoformat()
            except Exception as e:   # never let the scheduler thread die
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(f"{now():%Y-%m-%d %H:%M:%S} [scheduler] {e!r}\n")
            threading.Event().wait(15)


sched = Scheduler()
app = FastAPI(title="Car Agent")


@app.on_event("startup")
def _start():
    if os.environ.get("SCHEDULER", "on") != "off":     # off: dashboard only, for development
        threading.Thread(target=sched.loop, daemon=True).start()


# ----------------------------------------------------------------------- auth
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD:
        hdr = request.headers.get("authorization", "")
        ok = False
        if hdr.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
                ok = secrets.compare_digest(pw, PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Car Agent"'})
    return await call_next(request)


# ------------------------------------------------------------------- settings
def _coerce(default, value):
    """Shape `value` like `default`, so the UI cannot store '1300' as a string."""
    if value is None:
        return default
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return int(float(value))
    if isinstance(default, float):
        return float(value)
    if isinstance(default, str):
        return str(value)
    if isinstance(default, list):
        return [str(x).strip() if isinstance(x, str) else x for x in value
                if not (isinstance(x, str) and not x.strip())]
    if isinstance(default, dict):
        if not isinstance(value, dict):
            return default
        out = dict(default)
        for k, v in value.items():
            out[k] = _coerce(default[k], v) if k in default else v
        return out
    return value


@app.get("/api/settings")
def get_settings():
    return config.read()


@app.get("/api/settings/defaults")
def get_defaults():
    return config.defaults()


@app.put("/api/settings")
async def put_settings(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    try:
        merged = _coerce(config.read(), body)
        searches = []
        for x in body.get("searches", merged["searches"]):
            if not isinstance(x, dict):
                raise ValueError("each search needs label/make/model")
            item = {k: str(x.get(k) or "").strip() for k in ("label", "make", "model")}
            if item["model"] and not item["make"]:
                raise ValueError(f"search '{item['label'] or item['model']}': a model needs a make")
            item["label"] = item["label"] or " ".join(v for v in (item["make"], item["model"]) if v) or "Any make"
            searches.append(item)
        merged["searches"] = searches or [{"label": "Any make", "make": "", "model": ""}]
        merged["fuels"] = [f for f in merged["fuels"] if f in FUELS]
        merged["seller_types"] = [f for f in merged["seller_types"] if f in SELLERS] or sorted(SELLERS)
        if merged["gearbox"] not in ("any", "automatic", "manual"):
            raise ValueError("gearbox must be any, automatic or manual")
        if merged["prefer_seller"] not in ("any", "private", "dealer"):
            raise ValueError("prefer_seller must be any, private or dealer")
        if not (len(str(merged["home_zip"])) == 5 and str(merged["home_zip"]).isdigit()):
            raise ValueError("home postcode must have 5 digits")
        if merged["hit_score"] < merged["maybe_score"]:
            raise ValueError("the HIT score must be at least the MAYBE score")
    except (ValueError, TypeError, IndexError, KeyError) as e:
        raise HTTPException(400, f"invalid settings: {e}")
    config.write(merged)
    config.load()
    # Re-grade everything already stored against the new criteria.
    sched.start_background("rescore")
    return merged


# ------------------------------------------------------------------- listings
LIST_COLS = ("uid, source, search, url, title, make, model, price, km, year, reg, fuel, gearbox, "
             "power_kw, seller, zip, city, distance_km, price_label, accident, hu, owners, "
             "service_history, market_median, market_n, twin_of, score, verdict, reasons, "
             "first_seen, last_seen, posted, scam_risk, scam_flags, checked, status")


@app.get("/api/listings")
def listings(verdict: str = "HIT,MAYBE", status: str = "", q: str = "",
             sort: str = "score", dupes: int = 0, limit: int = 200, offset: int = 0):
    where, args = [], []
    if not dupes:
        where.append("twin_of IS NULL")      # the same car posted again elsewhere
    vs = [v for v in verdict.split(",") if v]
    if vs:
        where.append(f"verdict IN ({','.join('?' * len(vs))})")
        args += vs
    if status == "open":
        where.append("COALESCE(status,'') = ''")
    elif status:
        where.append("status = ?")
        args.append(status)
    if q:
        where.append("(title LIKE ? OR city LIKE ? OR zip LIKE ? OR body LIKE ?)")
        args += [f"%{q}%"] * 4
    order = {"score": "score DESC, first_seen DESC",
             "new": "first_seen DESC",
             "price": "price IS NULL, price ASC",
             "km": "km IS NULL, km ASC",
             "year": "year IS NULL, year DESC",
             "distance": "distance_km IS NULL, distance_km ASC"}.get(sort, "score DESC")
    sql = (f"SELECT {LIST_COLS} FROM listings"
           + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY {order} LIMIT ? OFFSET ?")
    c = store.conn()
    total = c.execute("SELECT COUNT(*) n FROM listings"
                      + (" WHERE " + " AND ".join(where) if where else ""), args).fetchone()["n"]
    rows = [dict(r) for r in c.execute(sql, args + [min(limit, 1000), offset])]
    c.close()
    return {"total": total, "items": rows}


@app.get("/api/listings/{uid:path}")
def listing(uid: str):
    c = store.conn()
    r = c.execute("SELECT * FROM listings WHERE uid=?", (uid,)).fetchone()
    c.close()
    if not r:
        raise HTTPException(404, "no such listing")
    rec = dict(r)
    rec["draft_de"] = contact.draft_de(rec)
    rec["body"] = (rec.get("body") or "")[:3000]
    rec["draft_en"] = contact.draft_en(rec)
    return rec


@app.post("/api/listings/{uid:path}/status")
async def set_status(uid: str, request: Request):
    status = (await request.json()).get("status", "")
    if status not in ("", "contacted", "dismissed"):
        raise HTTPException(400, "status must be '', 'contacted' or 'dismissed'")
    c = store.conn()
    n = c.execute("UPDATE listings SET status=? WHERE uid=?", (status, uid)).rowcount
    c.commit(); c.close()
    if not n:
        raise HTTPException(404, "no such listing")
    return {"uid": uid, "status": status}


# --------------------------------------------------------------------- status
@app.get("/api/status")
def status():
    c = store.conn()
    counts = {r["verdict"]: r["n"] for r in
              c.execute("SELECT verdict, COUNT(*) n FROM listings GROUP BY verdict")}
    contacted = c.execute("SELECT COUNT(*) n FROM listings WHERE status='contacted'").fetchone()["n"]
    since = (now() - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
    # Per-source rows carry fetched/new; the 'scan' summary row carries matches.
    day = c.execute("SELECT COALESCE(SUM(CASE WHEN source NOT IN ('scan','seed','digest') THEN fetched END),0) f, "
                    "COALESCE(SUM(CASE WHEN source NOT IN ('scan','seed','digest') THEN new END),0) n, "
                    "COALESCE(SUM(CASE WHEN source = 'scan' THEN matched END),0) m "
                    "FROM runs WHERE ts > ?", (since,)).fetchone()
    last_ok = c.execute("SELECT MAX(ts) t FROM runs WHERE source NOT IN ('scan','seed','digest') "
                        "AND fetched > 0").fetchone()["t"]
    c.close()
    return {
        "counts": counts, "contacted": contacted,
        "last_24h": {"fetched": day["f"], "new": day["n"], "matched": day["m"]},
        "last_successful_fetch": last_ok,
        "running": sched.running,
        "next_scan": sched.next_scan.isoformat(timespec="seconds"),
        "last_jobs": sched.last,
        "ntfy_topic": config.NTFY_TOPIC,
        "imap_password_set": bool(config.IMAP_PASSWORD),
    }


@app.get("/api/runs")
def runs(limit: int = 150):
    c = store.conn()
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM runs ORDER BY ts DESC LIMIT ?", (min(limit, 1000),))]
    c.close()
    return rows


@app.post("/api/scan")
def scan_now():
    if not sched.start_background("scan"):
        raise HTTPException(409, f"busy: {sched.running} is running")
    sched.next_scan = sched._compute_next(now())
    return {"started": True}


@app.post("/api/notify-test")
def notify_test():
    ok = notify.push("Car agent test",
                     "If you can read this on your phone, notifications work.")
    if not ok:
        raise HTTPException(502, "ntfy.sh did not accept the message")
    return {"sent": True, "topic": config.NTFY_TOPIC}


@app.post("/api/mail-test")
def mail_test():
    import mailbox_mobile
    try:
        n = mailbox_mobile.test()
    except Exception as e:
        raise HTTPException(502, f"mailbox: {e}")
    o = config.SOURCES["mobile_mail"]
    return {"ok": True, "matching": n,
            "text": f"Connected. {n} e-mail(s) from '{o.get('from_contains')}' in the last "
                    f"{o.get('lookback_days')} day(s)."}


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- dashboard
if os.path.isdir(WEB):
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
else:
    @app.get("/")
    def _no_ui():
        return JSONResponse({"error": f"dashboard not built: {WEB} missing"}, 503)
