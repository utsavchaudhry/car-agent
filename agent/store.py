"""SQLite state. One row per listing ever seen, so nothing is reported twice."""
import sqlite3, os, datetime
import config

DB = os.path.join(config.DATA_DIR, "listings.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    uid             TEXT PRIMARY KEY,
    source          TEXT,
    search          TEXT,
    url             TEXT,
    title           TEXT,
    make            TEXT,
    model           TEXT,
    variant         TEXT,
    price           INTEGER,
    km              INTEGER,
    year            INTEGER,
    reg             TEXT,
    fuel            TEXT,
    gearbox         TEXT,
    power_kw        INTEGER,
    body_type       TEXT,
    seller          TEXT,
    zip             TEXT,
    city            TEXT,
    distance_km     INTEGER,
    price_label     TEXT,
    offer_type      TEXT,
    accident        INTEGER,
    hu              TEXT,
    owners          INTEGER,
    service_history INTEGER,
    seller_since    TEXT,
    posted          TEXT,
    market_median   INTEGER,
    market_n        INTEGER,
    twin_of         TEXT,
    score           INTEGER,
    verdict         TEXT,
    reasons         TEXT,
    scam_risk       INTEGER DEFAULT 0,
    scam_flags      TEXT,
    body            TEXT,
    checked         INTEGER DEFAULT 0,
    first_seen      TEXT,
    last_seen       TEXT,
    alive_checked   TEXT,
    status          TEXT DEFAULT '',
    reported        INTEGER DEFAULT 0,
    drafted         INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_listings_model ON listings(make, model, year);
CREATE TABLE IF NOT EXISTS runs (
    ts TEXT, source TEXT, fetched INTEGER, new INTEGER, matched INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS seeded (key TEXT PRIMARY KEY, ts TEXT);
CREATE TABLE IF NOT EXISTS mail_seen (message_id TEXT PRIMARY KEY, ts TEXT, subject TEXT, listings INTEGER);
"""

# Columns added after the first release go here, so old databases upgrade.
EXTRA_COLS = {}

COLUMNS = None


def conn():
    global COLUMNS
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    # The dashboard reads while a scan writes; WAL lets both happen at once.
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    have = {r["name"] for r in c.execute("PRAGMA table_info(listings)")}
    for col, decl in EXTRA_COLS.items():
        if col not in have:
            c.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")
            have.add(col)
    c.commit()
    COLUMNS = have
    return c


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def seen(c, uid):
    return c.execute("SELECT 1 FROM listings WHERE uid=?", (uid,)).fetchone() is not None


def add(c, rec):
    rec = {k: v for k, v in rec.items() if k in COLUMNS}
    rec.setdefault("first_seen", now_iso())
    rec.setdefault("last_seen", rec["first_seen"])
    cols = ",".join(rec)
    marks = ",".join("?" * len(rec))
    c.execute(f"INSERT OR IGNORE INTO listings ({cols}) VALUES ({marks})", list(rec.values()))


def update(c, uid, **fields):
    fields = {k: v for k, v in fields.items() if k in COLUMNS}
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE listings SET {sets} WHERE uid=?", list(fields.values()) + [uid])


def log_run(c, source, fetched, new, matched, note=""):
    c.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
              (now_iso(), source, fetched, new, matched, note))


def is_seeded(c, key):
    return c.execute("SELECT 1 FROM seeded WHERE key=?", (key,)).fetchone() is not None


def mark_seeded(c, key):
    c.execute("INSERT OR IGNORE INTO seeded VALUES (?,?)", (key, now_iso()))


def unreported_hits(c):
    return c.execute(
        "SELECT * FROM listings WHERE reported=0 AND verdict IN ('HIT','MAYBE') "
        "AND COALESCE(status,'') = '' ORDER BY score DESC, price ASC").fetchall()


def mark_reported(c, uids):
    c.executemany("UPDATE listings SET reported=1 WHERE uid=?", [(u,) for u in uids])
