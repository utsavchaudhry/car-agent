#!/usr/bin/env python3
"""One scan pass. Started by the scheduler in server.py. No LLM, no browser.

  1. Fetch every enabled source; store each listing never seen before, graded
     on what the result page shows.
  2. Read the ad page of the most promising new ones (accident history, HU,
     owners, service book, description) and grade them again.
  3. Re-check a few older matches that have scrolled off the first result
     pages, and mark the sold ones GONE.
  4. Push new HITs to the phone.

A search scanned for the first time only fills the database: its first pages
are the existing stock, not news, so they never trigger alerts.
"""
import sys, os, traceback, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config, sources, store, match, scam, market, notify
from text import month_year


def grade(c, r):
    """Market comparison, duplicate check, score and scam screening, in place."""
    if r.get("market_n") != -1:          # -1: AutoScout24's own median, keep it
        r["market_median"], r["market_n"] = market.estimate(c, r)
    r["twin_of"] = market.find_twin(c, r)
    score, verdict, reasons = match.evaluate(r)
    score, verdict, reasons, risk, flags = scam.apply(r, score, verdict, reasons)
    r.update(score=score, verdict=verdict, reasons=" ; ".join(reasons),
             scam_risk=risk, scam_flags="; ".join(flags))
    return r


MERGE_KEYS = ("accident", "owners", "service_history", "hu", "posted", "power_kw", "body_type",
              "fuel", "gearbox", "make", "model", "reg", "seller", "seller_since", "price_label")


def enrich(r, d):
    for k in MERGE_KEYS:
        if d.get(k) not in (None, ""):
            r[k] = d[k]
    if d.get("reg"):
        r["year"] = month_year(r["reg"])[1] or r.get("year")
    if d.get("market_median"):
        r["market_median"], r["market_n"] = int(d["market_median"]), -1
    extra = " | ".join(x for x in (d.get("text"), d.get("equipment")) if x)
    if extra:
        r["body"] = ((r.get("body") or "") + " | " + extra)[:6000]
    r["checked"] = 1
    return r


def main():
    c = store.conn()
    if not sources.wait_for_network():
        store.log_run(c, "scan", 0, 0, 0, "ABORTED: no network after 180s")
        c.commit(); c.close()
        print(f"{datetime.datetime.now():%Y-%m-%d %H:%M}  aborted -- no network")
        return

    now_iso = store.now_iso()
    fresh = []                  # new records this scan, in fetch order
    quiet = set()               # uids that must not alert (first scan of a search)
    seed_keys = set()
    totals = dict(fetched=0, new=0)

    # ------------------------------------------------------------ 1. fetch
    for name, fn in sources.enabled():
        try:
            rows, notes = fn()
        except Exception as e:
            store.log_run(c, name, 0, 0, 0, "ERROR " + (str(e) if isinstance(e, RuntimeError)
                                                        else traceback.format_exc(limit=1).strip()))
            c.commit()
            continue

        new = 0
        for r in rows:
            if not r.get("uid"):
                continue
            old = c.execute("SELECT price, verdict, reasons, reported FROM listings WHERE uid=?",
                            (r["uid"],)).fetchone()
            if old:
                c.execute("UPDATE listings SET last_seen=? WHERE uid=?", (now_iso, r["uid"]))
                _price_cut(c, r, old)
                continue
            new += 1
            r.setdefault("posted", now_iso[:10])
            grade(c, r)
            key = f"{name}:{r.get('search', '')}"
            if name in sources.POLLED and not store.is_seeded(c, key):
                quiet.add(r["uid"])
                seed_keys.add(key)
                r["reported"] = 1
            store.add(c, r)
            fresh.append(r)

        note = "; ".join(notes)
        if not rows and not note and name in sources.POLLED:
            note = "WARNING: source returned 0 rows"
        elif notes:
            note = "WARNING: " + note
        store.log_run(c, name, len(rows), new, 0, note)
        c.commit()
        totals["fetched"] += len(rows)
        totals["new"] += new

    for key in seed_keys:
        store.mark_seeded(c, key)
    if seed_keys:
        store.log_run(c, "seed", 0, len(quiet), 0,
                      f"first scan of {', '.join(sorted(seed_keys))}: stored as stock, no alerts")
    c.commit()

    # ------------------------------------------- 2. read the best ad pages
    # Hard rejects score 0; anything that the ad page could still lift into
    # MAYBE gets read, best first, up to the per-scan budget.
    floor = int(config.MAYBE_SCORE) - 12
    todo = sorted((r for r in fresh if r["score"] >= floor and r["uid"] not in quiet
                   and r["source"] in sources.POLLED), key=lambda r: -r["score"])
    for r in todo[:int(config.MAX_DETAIL_FETCHES)]:
        try:
            d = sources.detail(r)
        except Exception:
            d = {}
        if not d:
            continue
        enrich(r, d)
        grade(c, r)
        store.update(c, r["uid"], **{k: r.get(k) for k in MERGE_KEYS + (
            "year", "body", "checked", "score", "verdict", "reasons", "scam_risk", "scam_flags",
            "market_median", "market_n", "twin_of")})
        c.commit()
        sources._pause()

    # ------------------------------------------------ 3. sold or still there?
    gone = _liveness(c)

    # -------------------------------------------------------------- 4. alert
    # Only cars whose ad page was read: the result page hides accident damage.
    hits = [r for r in fresh if r["verdict"] == "HIT" and r["uid"] not in quiet and not r.get("twin_of")
            and (r.get("checked") or r["source"] not in sources.POLLED)]
    hits.sort(key=lambda r: -r["score"])
    cap = int(config.MAX_ALERTS_PER_SCAN)
    for r in hits[:cap]:
        notify.announce(r)

    counts = {}
    for r in fresh:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    store.log_run(c, "scan", totals["fetched"], totals["new"],
                  counts.get("HIT", 0) + counts.get("MAYBE", 0),
                  f"HIT={counts.get('HIT', 0)} MAYBE={counts.get('MAYBE', 0)} "
                  f"read {min(len(todo), int(config.MAX_DETAIL_FETCHES))} ad pages"
                  + (f", {gone} gone" if gone else "") + (f", alerted {len(hits[:cap])}" if hits else ""))
    c.commit(); c.close()
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M}  fetched={totals['fetched']} new={totals['new']} "
          f"HIT={counts.get('HIT', 0)} MAYBE={counts.get('MAYBE', 0)}"
          + (f" quiet={len(quiet)}" if quiet else "") + (f" gone={gone}" if gone else "")
          + (f" alerted={len(hits[:cap])}" if hits else ""))


def _price_cut(c, r, old):
    """A known car came back cheaper: record it and re-grade."""
    if not (r.get("price") and old["price"]) or r["price"] >= old["price"] * 0.97:
        return
    row = dict(c.execute("SELECT * FROM listings WHERE uid=?", (r["uid"],)).fetchone())
    row["price"] = r["price"]
    if r.get("price_label"):
        row["price_label"] = r["price_label"]
    grade(c, row)
    row["reasons"] = f"price cut {old['price']} → {r['price']} €" + (" ; " + row["reasons"] if row["reasons"] else "")
    became_hit = row["verdict"] == "HIT" and old["verdict"] != "HIT"
    store.update(c, r["uid"], price=row["price"], price_label=row.get("price_label"), score=row["score"],
                 verdict=row["verdict"], reasons=row["reasons"], scam_risk=row["scam_risk"],
                 scam_flags=row["scam_flags"], market_median=row["market_median"], market_n=row["market_n"],
                 **({"reported": 0} if became_hit else {}))
    if became_hit and not row.get("status"):
        notify.announce(row)


def _liveness(c):
    """Matches drop off the first result pages within hours, so 'not seen in
    the last scan' says nothing. Ask the ad page itself instead."""
    six_h = (datetime.datetime.now() - datetime.timedelta(hours=6)).isoformat(timespec="seconds")
    twelve_h = (datetime.datetime.now() - datetime.timedelta(hours=12)).isoformat(timespec="seconds")
    polled = tuple(sources.POLLED)
    rows = c.execute(
        f"SELECT uid, url FROM listings WHERE verdict IN ('HIT','MAYBE') AND COALESCE(status,'') != 'dismissed' "
        f"AND source IN ({','.join('?' * len(polled))}) AND last_seen < ? "
        f"AND (alive_checked IS NULL OR alive_checked < ?) ORDER BY score DESC LIMIT ?",
        polled + (six_h, twelve_h, int(config.MAX_LIVENESS_CHECKS))).fetchall()
    gone = 0
    for row in rows:
        state = sources.alive(row["url"])
        now = store.now_iso()
        if state is False:
            store.update(c, row["uid"], verdict="GONE", alive_checked=now)
            gone += 1
        elif state is True:
            store.update(c, row["uid"], alive_checked=now, last_seen=now)
        else:
            store.update(c, row["uid"], alive_checked=now)
        sources._pause()
    # Alert-only sources cannot be re-checked; treat a month-old match as sold.
    month = (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat(timespec="seconds")
    gone += c.execute(
        f"UPDATE listings SET verdict='GONE' WHERE verdict IN ('HIT','MAYBE') "
        f"AND source NOT IN ({','.join('?' * len(polled))}) AND first_seen < ?", polled + (month,)).rowcount
    c.commit()
    return gone


if __name__ == "__main__":
    main()
