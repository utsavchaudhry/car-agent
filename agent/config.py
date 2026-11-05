"""Search settings.

Defaults live in defaults.json next to this file. Your own values live in
$DATA_DIR/settings.json, which the dashboard writes. Every key is exposed here
upper-cased, so `max_price` is read as `config.MAX_PRICE`.

Call load() again after settings.json changes to pick up the new values.

Secrets do NOT live here: the mailbox password for mobile.de alerts comes from
the IMAP_PASSWORD environment variable, so the dashboard never shows it.
"""
import os, json, copy, secrets

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(os.path.dirname(HERE), "data")
DEFAULTS_PATH = os.path.join(HERE, "defaults.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# Lists of objects are replaced wholesale, never merged element by element.
REPLACE_WHOLE = {"searches"}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in REPLACE_WHOLE:
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def defaults():
    with open(DEFAULTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def read():
    """Effective settings: defaults overlaid with settings.json."""
    s = defaults()
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s = _merge(s, json.load(f))
    # First run: invent a private ntfy topic so push works out of the box.
    if not s.get("ntfy_topic"):
        s["ntfy_topic"] = "car-agent-" + secrets.token_urlsafe(9).lower().replace("_", "x").replace("-", "x")
        write(s)
    return s


def write(s):
    """Store only what differs from defaults.json, so improved defaults (a new
    block term, a better threshold) still reach anyone who has saved before."""
    base = defaults()
    s = {k: v for k, v in s.items() if base.get(k) != v}
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_PATH)


def load():
    g = globals()
    for k, v in read().items():
        g[k.upper()] = v


IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")

load()
