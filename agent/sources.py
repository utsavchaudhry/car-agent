"""Fetch + parse car portals. No LLM, no browser.

  * AutoScout24   -- the search page embeds its results as JSON (__NEXT_DATA__),
                     so nothing is scraped from markup
  * Kleinanzeigen -- category c216 (Autos); most private sellers, most bargains,
                     most scams
  * mobile.de     -- blocks plain requests (403) and its API is dealer-only, so
                     it arrives through saved-search alert e-mails: mailbox.py

Every parser takes the page text and returns plain dicts, so tests can feed it
saved pages. Searches (make/model) come from settings; the hard filters (price,
km, year, fuel, gearbox, seller, radius) go into the portal's own URL.
"""
import re, json, time, random, datetime, urllib.request, urllib.error, urllib.parse
import config
from text import (clean, num, grp, norm, month_year, mm_yyyy, make_key, model_key,
                  split_title, fuel_key, gearbox_key)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def online(timeout=5):
    for probe in ("https://www.autoscout24.de/robots.txt", "https://one.one.one.one/"):
        try:
            req = urllib.request.Request(probe, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            continue
    return False


def wait_for_network(max_wait=180):
    waited = 0
    while waited < max_wait:
        if online():
            return True
        time.sleep(10)
        waited += 10
    return online()


def get(url, timeout=30, tries=3):
    """A polite GET with backoff. Returns '' only after genuinely retrying."""
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                return ""          # retrying a block or a 404 only hammers the site
            if attempt == tries - 1:
                return ""
        except Exception:
            if attempt == tries - 1:
                return ""
        time.sleep(2 ** attempt + random.uniform(0, 1.5))
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def alive(url, timeout=20):
    """True / False (ad removed) / None (could not tell).

    Both portals answer a removed ad with a 301 to a search page; AutoScout24
    to /lst/<make>/<model>, Kleinanzeigen to /s-autos/<town>/c216...
    """
    if not url or "mobile.de" in url:
        return None
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return False
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location", "")
            path = urllib.parse.urlparse(loc).path or "/"
            # A redirect to another ad page (slug changed) is still alive.
            return not re.search(r"^/lst\b|^/s-(?!anzeige)[a-z-]+/|^/$", path)
        return None
    except Exception:
        return None


def _pause():
    time.sleep(random.uniform(2.5, 5.0))


def _search_terms(search):
    return (search.get("make") or "").strip(), (search.get("model") or "").strip()


# --------------------------------------------------------------- AutoScout24
AS24_FUEL = {"petrol": "B", "diesel": "D", "electric": "E", "hybrid": "2,3", "gas": "L,C"}
# Price bands on the ad page; the market median sits between 2 and 3.
AS24_CATEGORY = {0: "too_low", 1: "top", 2: "good", 3: "fair", 4: "high", 5: "very_high", 6: "very_high"}
AS24_LABEL = {"top-price": "top", "good-price": "good", "fair-price": "fair",
              "somewhat-expensive": "high", "expensive": "very_high",
              "toolow-price": "too_low"}


def _slug_as24(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(s)).strip("-")


def as24_url(search, page=1):
    make, model = _search_terms(search)
    path = "/lst"
    if make:
        path += "/" + _slug_as24(make)
        if model:
            path += "/" + _slug_as24(model)
    q = {"atype": "C", "cy": "D", "sort": "age", "desc": "1",
         "ocs_listing": "exclude", "damaged_listing": "exclude",
         "zip": config.HOME_ZIP, "zipr": str(int(config.RADIUS_KM)),
         "priceto": str(int(config.MAX_PRICE)), "size": "20", "page": str(page)}
    if config.MIN_PLAUSIBLE_PRICE:
        q["pricefrom"] = str(int(config.MIN_PLAUSIBLE_PRICE))
    if config.MAX_KM:
        q["kmto"] = str(int(config.MAX_KM))
    if config.MIN_YEAR:
        q["fregfrom"] = str(int(config.MIN_YEAR))
    fuels = [AS24_FUEL[f] for f in config.FUELS if f in AS24_FUEL]
    if fuels:
        q["fuel"] = ",".join(fuels)
    if config.GEARBOX == "automatic":
        q["gear"] = "A"
    elif config.GEARBOX == "manual":
        q["gear"] = "M"
    sellers = set(config.SELLER_TYPES)
    if sellers == {"private"}:
        q["custtype"] = "P"
    elif sellers == {"dealer"}:
        q["custtype"] = "D"
    return "https://www.autoscout24.de" + path + "?" + urllib.parse.urlencode(q, safe=",")


def _next_data(doc):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', doc or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))["props"]["pageProps"]
    except Exception:
        return None


def parse_as24_list(doc):
    pp = _next_data(doc)
    if pp is None:
        return None                       # not a results page: blocked or changed
    out = []
    for l in pp.get("listings") or []:
        if l.get("isOcs"):
            continue                      # sponsored slot, ignores the radius
        v = l.get("vehicle") or {}
        tr = l.get("tracking") or {}
        loc = l.get("location") or {}
        details = {d.get("ariaLabel"): d.get("data") for d in l.get("vehicleDetails") or []}
        price = (l.get("price") or {}).get("priceRaw")
        km = num(tr.get("mileage")) if tr.get("mileage") not in (None, "", "unknown") else num(v.get("mileageInKm"))
        reg = mm_yyyy(tr.get("firstRegistration") or details.get("Erstzulassung") or "")
        _, year = month_year(reg)
        kw = num(grp(r"(\d+)\s*kW", details.get("Leistung") or ""))
        title = " ".join(x for x in (v.get("make"), v.get("model"),
                                     v.get("modelVersionInput") or v.get("variant")) if x)
        seller_t = (l.get("seller") or {}).get("type") or ""
        out.append(dict(
            uid=f"as24:{l['id']}",
            source="AutoScout24",
            url="https://www.autoscout24.de" + (l.get("url") or f"/angebote/{l['id']}"),
            title=title,
            make=v.get("make") or "",
            model=v.get("modelGroup") or v.get("model") or "",
            variant=" ".join(x for x in (v.get("model"), v.get("variant"), v.get("motorTypeName")) if x),
            price=int(price) if price else None,
            km=int(km) if km is not None else None,
            reg=reg, year=year,
            fuel=fuel_key(v.get("fuel") or details.get("Kraftstoff")),
            gearbox=gearbox_key(v.get("transmission") or details.get("Getriebe")),
            power_kw=int(kw) if kw else None,
            seller="dealer" if seller_t == "Dealer" else "private" if seller_t else "",
            zip=loc.get("zip") or "", city=loc.get("city") or "",
            distance_km=loc.get("distanceToSearchLocationInKm"),
            price_label=AS24_LABEL.get((tr.get("priceLabel") or "").strip(), ""),
            offer_type=v.get("offerType") or "",
            body=" | ".join(x for x in (title, v.get("subtitle") or "",
                                        " ".join(str(d) for d in details.values() if d)) if x),
        ))
    return out


def parse_as24_detail(doc):
    pp = _next_data(doc)
    ld = (pp or {}).get("listingDetails") or {}
    if not ld:
        return {}
    v = ld.get("vehicle") or {}
    equip = []
    for items in (v.get("equipment") or {}).values():
        equip += [i.get("id", "") for i in items or []]
    acc = v.get("hadAccident")
    if acc is None and v.get("damageConditions"):
        acc = any("unfall" in norm(d) for d in v["damageConditions"])
    created = (ld.get("createdTimestampWithOffset") or "")[:10]
    pub = (ld.get("prices") or {}).get("public") or {}
    cat = pub.get("category")
    return dict(
        market_median=pub.get("median"),
        price_label=AS24_CATEGORY.get(cat, "") if not pub.get("isCategoryAbsent") else "",
        text=clean(ld.get("description") or "")[:4000],
        equipment=", ".join(equip),
        accident=None if acc is None else int(bool(acc)),
        owners=v.get("noOfPreviousOwners"),
        service_history=None if v.get("hasFullServiceHistory") is None else int(bool(v["hasFullServiceHistory"])),
        hu=mm_yyyy(v.get("nextVehicleSafetyInspection") or ""),
        posted=created,
        status=ld.get("status") or "",
        power_kw=v.get("rawPowerInKw"),
        body_type=v.get("bodyType") or "",
    )


def autoscout24():
    o = config.SOURCES["autoscout24"]
    out, notes = [], []
    for s in config.SEARCHES:
        for p in range(1, int(o.get("pages", 2)) + 1):
            doc = get(as24_url(s, p))
            rows = parse_as24_list(doc)
            if rows is None:
                notes.append(f"{s.get('label') or 'search'} p{p}: no result data (blocked?)")
                break
            for r in rows:
                r["search"] = s.get("label") or ""
            out += rows
            _pause()
            if len(rows) < 18:
                break                     # last page
    return _uniq(out), notes


# ------------------------------------------------------------- Kleinanzeigen
KA_FUEL = {"petrol": "benzin", "diesel": "diesel", "electric": "elektro", "hybrid": "hybrid", "gas": "lpg"}
KA_RADII = (5, 10, 20, 30, 50, 100, 150, 200)


def _slug_ka(s):
    return re.sub(r"[^a-z0-9]+", "_", norm(s)).strip("_")


def ka_url(search, page=1):
    o = config.SOURCES["kleinanzeigen"]
    make, model = _search_terms(search)
    radius = next((r for r in KA_RADII if r >= int(config.RADIUS_KM)), KA_RADII[-1])
    seg = ""
    sellers = set(config.SELLER_TYPES)
    if sellers == {"private"}:
        seg += "anbieter:privat/"
    elif sellers == {"dealer"}:
        seg += "anbieter:gewerblich/"
    seg += "anzeige:angebote/"
    if page > 1:
        seg += f"seite:{page}/"
    lo = int(config.MIN_PLAUSIBLE_PRICE or 0) or ""
    seg += f"preis:{lo}:{int(config.MAX_PRICE)}/"
    attrs = ""
    if config.MIN_YEAR:
        attrs += f"+autos.ez_i:{int(config.MIN_YEAR)}%2C"
    if config.MAX_KM:
        attrs += f"+autos.km_i:%2C{int(config.MAX_KM)}"
    if make:
        attrs += f"+autos.marke_s:{_slug_ka(make_key(make))}"
        if model:
            attrs += f"+autos.model_s:{_slug_ka(model)}"
    fuels = [KA_FUEL[f] for f in config.FUELS if f in KA_FUEL]
    if len(fuels) == 1:                   # one value only; several are filtered later
        attrs += f"+autos.fuel_s:{fuels[0]}"
    if config.GEARBOX == "automatic":
        attrs += "+autos.shift_s:automatik"
    elif config.GEARBOX == "manual":
        attrs += "+autos.shift_s:manuell"
    return (f"https://www.kleinanzeigen.de/s-autos/{o['location_slug']}/{seg}"
            f"c216{o['location_id']}r{radius}{attrs}")


def _ka_posted(s):
    t = norm(s)
    today = datetime.date.today()
    if "heute" in t:
        return str(today)
    if "gestern" in t:
        return str(today - datetime.timedelta(days=1))
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", t)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def parse_ka_list(doc, search=None):
    """Teasers from a Kleinanzeigen results page. None if it is not one."""
    if not doc or "data-adid" not in doc and "Ergebnis" not in doc:
        return None
    s_make, s_model = _search_terms(search or {})
    out = []
    for m in re.finditer(r'<article[^>]*data-adid="(\d+)"[^>]*data-href="([^"]+)".*?</article>',
                         doc, re.S):
        adid, href, blk = m.group(1), m.group(2), m.group(0)
        blk = re.sub(r"<script.*?</script>|<svg.*?</svg>", " ", blk, flags=re.S)
        title = clean(grp(r"<h3[^>]*>(.*?)</h3>", blk, re.S))
        price_txt = clean(grp(r'text-title3 font-strong text-secondary">([^<]+)<', blk))
        chips = [clean(c) for c in re.findall(r"<span data-dhl-promotion[^>]*>(.*?)</span>", blk, re.S)]
        chips_txt = " ".join(chips)
        km = num(grp(r"([\d.]+)\s*km\b", chips_txt))
        reg = mm_yyyy(grp(r"EZ\s*([\d/]+)", chips_txt))
        _, year = month_year(reg)
        desc = clean(grp(r'<p class="[^"]*text-onSurfaceSubdued[^"]*">(.*?)</p>', blk, re.S))
        loc = clean(grp(r"<span>(\d{5}\s+[^<]+)</span>", blk))
        dist = num(grp(r"\((\d+)\s*km\)", blk))
        posted = _ka_posted(clean(grp(r'<span>((?:Heute|Gestern)[^<]*|\d{2}\.\d{2}\.\d{4})</span>', blk)))
        make, model = (make_key(s_make), s_model) if s_make else split_title(title)
        if s_make and not s_model:
            model = split_title(title)[1]
        out.append(dict(
            uid=f"ka:{adid}",
            source="Kleinanzeigen",
            url="https://www.kleinanzeigen.de" + href,
            title=title,
            make=make, model=model, variant="",
            price=int(num(price_txt)) if num(price_txt) else None,
            km=int(km) if km is not None else None,
            reg=reg, year=year,
            fuel="", gearbox="", power_kw=None,
            seller="",
            zip=grp(r"^(\d{5})", loc), city=re.sub(r"^\d{5}\s*", "", loc),
            distance_km=int(dist) if dist is not None else None,
            price_label="", offer_type="",
            posted=posted,
            body=" | ".join(x for x in (title, price_txt, chips_txt, desc, loc) if x),
        ))
    return out


def parse_ka_detail(doc):
    if not doc:
        return {}
    attrs = {}
    for m in re.finditer(r'<li class="addetailslist--detail">\s*(.*?)\s*'
                         r'<span class="addetailslist--detail--value"[^>]*>(.*?)</span>', doc, re.S):
        attrs[clean(m.group(1))] = clean(m.group(2))
    tags = [clean(t) for t in re.findall(r'<li class="checktag">(.*?)</li>', doc, re.S)]
    out = dict(attrs=attrs, equipment=", ".join(tags))
    zustand = norm(attrs.get("Fahrzeugzustand", ""))
    if zustand:
        out["accident"] = 0 if "unbeschaedigt" in zustand else 1
    out["hu"] = mm_yyyy(attrs.get("HU bis", ""))
    out["fuel"] = fuel_key(attrs.get("Kraftstoffart", ""))
    out["gearbox"] = gearbox_key(attrs.get("Getriebe", ""))
    ps = num(grp(r"(\d+)\s*PS", attrs.get("Leistung", "")))
    out["power_kw"] = int(round(ps * 0.7355)) if ps else None
    out["body_type"] = attrs.get("Fahrzeugtyp", "")
    if attrs.get("Marke"):
        out["make"] = make_key(attrs["Marke"])
    if attrs.get("Modell"):
        out["model"] = attrs["Modell"]
    if attrs.get("Erstzulassung"):
        out["reg"] = mm_yyyy(attrs["Erstzulassung"])
    if any("scheckheft" in norm(t) for t in tags):
        out["service_history"] = 1
    seller = grp(r"(Privater Nutzer|Gewerblicher Nutzer|Gewerblicher Anbieter|Privater Anbieter)", doc)
    if seller:
        out["seller"] = "private" if seller.startswith("Privat") else "dealer"
    out["seller_since"] = grp(r"Aktiv seit\s*(\d{2}\.\d{2}\.\d{4})", doc)
    pm = re.search(r'id="viewad-extra-info".*?(\d{2})\.(\d{2})\.(\d{4})', doc, re.S)
    if pm:
        out["posted"] = f"{pm.group(3)}-{pm.group(2)}-{pm.group(1)}"
    dm = re.search(r'id="viewad-description-text"[^>]*>(.*?)</p>', doc, re.S)
    if dm:
        out["text"] = clean(dm.group(1))[:4000]
    return out


def ka_heading(doc):
    return clean(grp(r"<h1[^>]*>(.*?)</h1>", doc or "", re.S))


def kleinanzeigen():
    o = config.SOURCES["kleinanzeigen"]
    out, notes = [], []
    for s in config.SEARCHES:
        for p in range(1, int(o.get("pages", 2)) + 1):
            doc = get(ka_url(s, p))
            rows = parse_ka_list(doc, s)
            if rows is None:
                notes.append(f"{s.get('label') or 'search'} p{p}: no results page (blocked?)")
                break
            # An unknown model slug does not fail: Kleinanzeigen silently drops
            # it and returns the whole make. Say so instead of scanning noise.
            if p == 1 and s.get("model") and model_key(s["model"]) not in model_key(ka_heading(doc)):
                notes.append(f"{s.get('label') or s['model']}: Kleinanzeigen did not recognise "
                             f"model '{s['model']}' -- try another spelling")
            for r in rows:
                r["search"] = s.get("label") or ""
            out += rows
            _pause()
            if len(rows) < 20:
                break
    return _uniq(out), notes


# ------------------------------------------------------------------- details
def detail(rec):
    """Read the ad page itself. -> dict of extra facts, {} if unreachable."""
    url = rec.get("url") or ""
    if "autoscout24.de" in url:
        return parse_as24_detail(get(url))
    if "kleinanzeigen.de" in url:
        return parse_ka_detail(get(url))
    return {}                             # mobile.de pages are blocked


def _uniq(rows):
    seen, out = set(), []
    for r in rows:
        if r["uid"] in seen:
            continue
        seen.add(r["uid"])
        out.append(r)
    return out


def _mobile_mail():
    import mailbox_mobile
    return mailbox_mobile.fetch()


ALL = [
    ("AutoScout24", "autoscout24", autoscout24),
    ("Kleinanzeigen", "kleinanzeigen", kleinanzeigen),
    ("mobile.de (e-mail)", "mobile_mail", _mobile_mail),
]
POLLED = {"AutoScout24", "Kleinanzeigen"}     # sources whose ads we can re-check


def enabled():
    return [(name, fn) for name, key, fn in ALL
            if config.SOURCES.get(key, {}).get("enabled", True)]
