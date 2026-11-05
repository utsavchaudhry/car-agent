"""mobile.de through its own saved-search alert e-mails.

mobile.de answers every scripted request with 403 and its Search API is only
for dealer accounts. What it does offer every user is a saved search ("Suche
speichern") that e-mails new matches. This module reads those e-mails over IMAP
(read-only: nothing is marked read, moved or deleted), pulls each listing link
out, and turns the text around it into the same record the scrapers produce.

Set up: save a search on mobile.de with e-mail notification on, use a mailbox
the agent may read (a Gmail app password works), put host/user in Settings and
the password in the IMAP_PASSWORD environment variable.

The alert layout is not documented. The parser therefore keys on what any
layout must contain -- a link per listing -- and reads price, km and first
registration from the text next to it. The last few raw alerts are kept in
$DATA_DIR/mail_samples/ so the parser can be checked against the real thing.
"""
import os, re, html, email, imaplib, datetime, urllib.parse, urllib.request, urllib.error
from email.header import decode_header, make_header
import config
from text import num, grp, norm, mm_yyyy, month_year, split_title, fuel_key, gearbox_key

SAMPLES = os.path.join(config.DATA_DIR, "mail_samples")
_ID_PATTERNS = (
    re.compile(r"mobile\.de/[^\s\"'<>]*?[?&](?:id|adId|ad_id|listingId)=(\d{6,})", re.I),
    re.compile(r"mobile\.de/auto-inserat/[^\s\"'<>]*?/(\d{6,})\.html", re.I),
    re.compile(r"mobile\.de/fahrzeuge/details\.html\?[^\s\"'<>]*?id=(\d{6,})", re.I),
    re.compile(r"mobile\.de/[^\s\"'<>]*?/(\d{8,})(?:\.html|[/?#]|$)", re.I),
)


def _id_from(url, depth=0):
    for pat in _ID_PATTERNS:
        m = pat.search(url)
        if m:
            return m.group(1)
    if depth < 3 and "%" in url:                 # tracking links wrap the target
        return _id_from(urllib.parse.unquote(url), depth + 1)
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _resolve(url, cache, budget):
    """Follow a click-tracking link hop by hop -- without ever requesting the
    mobile.de page itself -- until a listing id shows up in a Location."""
    if url in cache:
        return cache[url]
    cache[url] = ""
    if budget[0] <= 0:
        return ""
    opener = urllib.request.build_opener(_NoRedirect)
    cur = url
    for _ in range(4):
        host = urllib.parse.urlparse(cur).hostname or ""
        if host.endswith("suchen.mobile.de") or host.endswith("m.mobile.de") or host == "www.mobile.de":
            break
        budget[0] -= 1
        try:
            opener.open(urllib.request.Request(cur, method="HEAD",
                                               headers={"User-Agent": "Mozilla/5.0"}), timeout=10)
            break                                # 200: no redirect, give up
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
            if not loc:
                break
            cur = urllib.parse.urljoin(cur, loc)
            lid = _id_from(cur)
            if lid:
                cache[url] = lid
                return lid
        except Exception:
            break
    return ""


def _to_tokens(body_html):
    """Flatten the HTML into text, with each link replaced by a marker token."""
    links = []

    def keep(m):
        links.append((html.unescape(m.group(1)), m.group(2)))
        return f" \x00{len(links) - 1}\x00 " + m.group(2) + " "

    s = re.sub(r"<(style|script|head)[^>]*>.*?</\1>", " ", body_html, flags=re.S | re.I)
    s = re.sub(r'<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', keep, s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</(?:p|div|tr|td|li|h\d)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return s, links


_PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{3,6})\s*(?:€|EUR)(?!\s*(?:/|mtl|monatl|pro\s+monat|p\.\s?m))", re.I)
_KM = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{1,6})\s*km\b(?!\s*/\s*h)", re.I)
_EZ = re.compile(r"(?:EZ|Erstzulassung)\s*:?\s*(\d{1,2}\s*/\s*\d{4})", re.I)
_REG = re.compile(r"\b(\d{2}/(?:19|20)\d{2})\b")
_KW = re.compile(r"(\d{2,3})\s*kW", re.I)
_PLZ = re.compile(r"\b(?:DE[- ])?(\d{5})\s+([A-ZÄÖÜ][\wäöüß.\- ]{1,40})")


def parse_alert(body_html, resolve=True):
    """One alert e-mail -> list of listing records."""
    text, links = _to_tokens(body_html or "")
    cache, budget = {}, [40]
    ids = []
    for i, (href, _) in enumerate(links):
        lid = _id_from(href)
        if not lid and resolve and re.search(r"mobile\.de|click|track|link|email", href, re.I) \
                and not re.search(r"abmelden|unsubscribe|einstellungen|datenschutz|impressum|"
                                  r"agb|hilfe|app-store|play\.google|facebook|instagram", href, re.I):
            lid = _resolve(href, cache, budget)
        ids.append(lid)

    # Walk the text; a listing's block runs from its first link marker to the
    # first marker of a different listing.
    parts = re.split(r"\x00(\d+)\x00", text)
    blocks, order, cur = {}, [], None
    pre = parts[0]
    for k in range(1, len(parts), 2):
        lid = ids[int(parts[k])]
        chunk = parts[k + 1]
        if lid and lid != cur:
            cur = lid
            if lid not in blocks:
                order.append(lid)
                blocks[lid] = {"text": "", "titles": []}
        if cur:
            blocks[cur]["text"] += " " + chunk
            if lid == cur:
                link_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", links[int(parts[k])][1])).strip()
                if link_text:
                    blocks[cur]["titles"].append(html.unescape(link_text))
    del pre

    out = []
    for lid in order:
        b = blocks[lid]
        t = re.sub(r"[ \t]+", " ", b["text"]).strip()
        # Title: the longest link text that is not just a price or a button.
        titles = [x for x in b["titles"] if not _PRICE.fullmatch(x.strip())
                  and not re.fullmatch(r"(?i)(details?|ansehen|zum (fahrzeug|angebot|inserat)|mehr|anzeigen)\W*", x.strip())]
        title = max(titles, key=len) if titles else t.split("\n")[0][:120]
        pm = _PRICE.search(t)
        km = _KM.search(t)
        reg = grp(_EZ, t) or grp(_REG, t)
        reg = mm_yyyy(reg.replace(" ", "")) if reg else ""
        _, year = month_year(reg) if reg else (None, None)
        kw = _KW.search(t)
        plz = _PLZ.search(t)
        make, model = split_title(title)
        nt = norm(t)
        seller = "private" if "privatanbieter" in nt or "privater anbieter" in nt else \
                 "dealer" if "haendler" in nt or "gewerblich" in nt else ""
        out.append(dict(
            uid=f"mde:{lid}",
            source="mobile.de",
            url=f"https://suchen.mobile.de/fahrzeuge/details.html?id={lid}",
            title=title.strip(),
            make=make, model=model, variant="",
            price=int(num(pm.group(1))) if pm else None,
            km=int(num(km.group(1))) if km else None,
            reg=reg, year=year,
            fuel=fuel_key(grp(r"(?i)\b(Benzin|Diesel|Elektro(?:/Benzin|/Diesel)?|Hybrid[^\s,]*|Autogas|Erdgas|LPG|CNG)\b", t)),
            gearbox=gearbox_key(grp(r"(?i)\b(Automatik|Schaltgetriebe|Halbautomatik)\b", t)),
            power_kw=int(kw.group(1)) if kw else None,
            seller=seller,
            zip=plz.group(1) if plz else "", city=plz.group(2).strip() if plz else "",
            distance_km=None,
            price_label=_mobile_rating(nt),
            offer_type="",
            body=re.sub(r"\s+", " ", t)[:2000],
        ))
    return out


def _mobile_rating(nt):
    """mobile.de prints its own price rating ("Sehr guter Preis" ...)."""
    for label, key in (("sehr guter preis", "top"), ("guter preis", "good"),
                       ("fairer preis", "fair"), ("erhoehter preis", "high"),
                       ("hoher preis", "very_high")):
        if label in nt:
            return key
    return ""


# --------------------------------------------------------------------- IMAP
def _html_of(msg):
    html_part, text_part = None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if not payload:
            continue
        s = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/html" and html_part is None:
            html_part = s
        elif ctype == "text/plain" and text_part is None:
            text_part = s
    if html_part:
        return html_part
    # Plain-text alert: make the bare URLs look like links for the parser.
    return re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', html.escape(text_part or ""))


def connect():
    o = config.SOURCES["mobile_mail"]
    if not (o.get("imap_host") and o.get("imap_user") and config.IMAP_PASSWORD):
        raise RuntimeError("mailbox not configured: set host + user in Settings and IMAP_PASSWORD in .env")
    m = imaplib.IMAP4_SSL(o["imap_host"], int(o.get("imap_port", 993)), timeout=30)
    m.login(o["imap_user"], config.IMAP_PASSWORD)
    typ, _ = m.select(f'"{o.get("folder") or "INBOX"}"', readonly=True)
    if typ != "OK":
        raise RuntimeError(f"cannot open folder {o.get('folder')!r}")
    return m


def search_ids(m):
    o = config.SOURCES["mobile_mail"]
    since = (datetime.date.today() - datetime.timedelta(days=int(o.get("lookback_days", 3))))
    crit = f'(SINCE "{since:%d-%b-%Y}" FROM "{o.get("from_contains") or "mobile.de"}")'
    typ, data = m.search(None, crit)
    return data[0].split() if typ == "OK" and data and data[0] else []


def fetch():
    """-> (records, notes). Each e-mail is parsed once (tracked by Message-ID)."""
    import store
    c = store.conn()
    notes, out = [], []
    m = connect()
    try:
        nums = search_ids(m)
        for n in nums:
            typ, data = m.fetch(n, "(BODY.PEEK[])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1])
            mid = msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('Subject')}"
            if c.execute("SELECT 1 FROM mail_seen WHERE message_id=?", (mid,)).fetchone():
                continue
            subject = str(make_header(decode_header(msg.get("Subject") or "")))
            body = _html_of(msg)
            rows = parse_alert(body)
            _keep_sample(subject, body)
            c.execute("INSERT OR IGNORE INTO mail_seen VALUES (?,?,?,?)",
                      (mid, datetime.datetime.now().isoformat(timespec="seconds"), subject, len(rows)))
            if not rows:
                notes.append(f"no listings found in mail '{subject[:60]}' (layout changed?)")
            out += rows
        c.commit()
    finally:
        c.close()
        try:
            m.logout()
        except Exception:
            pass
    seen, uniq = set(), []
    for r in out:
        if r["uid"] not in seen:
            seen.add(r["uid"])
            uniq.append(r)
    return uniq, notes


def _keep_sample(subject, body, keep=5):
    os.makedirs(SAMPLES, exist_ok=True)
    name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".html"
    with open(os.path.join(SAMPLES, name), "w", encoding="utf-8") as f:
        f.write(f"<!-- {html.escape(subject)} -->\n{body}")
    files = sorted(os.listdir(SAMPLES))
    for old in files[:-keep]:
        os.remove(os.path.join(SAMPLES, old))


def test():
    """For the dashboard's 'Test mailbox' button."""
    m = connect()
    try:
        return len(search_ids(m))
    finally:
        m.logout()
