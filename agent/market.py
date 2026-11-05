"""What similar cars cost, from everything the agent has already seen.

AutoScout24 rates its own dealer prices, but private sellers, Kleinanzeigen and
mobile.de alerts come without a rating. For those the agent compares the ask
with the median of comparable listings in its own database: same make and
model, first registered within a year either side, similar mileage.
"""
import datetime, statistics
from text import make_key, model_key

MIN_COMPS = 5


def comparables(c, rec, days=120):
    mk, md = make_key(rec.get("make")), model_key(rec.get("model"))
    year, km = rec.get("year"), rec.get("km")
    if not (mk and md and year and km is not None):
        return []
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    lo, hi = km * 0.7 - 10000, km * 1.3 + 10000
    rows = c.execute(
        "SELECT make, model, price FROM listings WHERE year BETWEEN ? AND ? AND km BETWEEN ? AND ? "
        "AND price > 0 AND uid != ? AND twin_of IS NULL AND COALESCE(scam_risk,0) < 30 AND COALESCE(last_seen, first_seen) > ?",
        (year - 1, year + 1, lo, hi, rec.get("uid", ""), since)).fetchall()
    return [r["price"] for r in rows if make_key(r["make"]) == mk and model_key(r["model"]) == md]


def estimate(c, rec):
    """-> (median price or None, number of comparables)."""
    prices = comparables(c, rec)
    if len(prices) < MIN_COMPS:
        return None, len(prices)
    return int(statistics.median(prices)), len(prices)


def find_twin(c, rec, days=60):
    """The same car seen before: on another portal (dealers list everywhere) or
    on the same one (dealer chains post one car at every branch).

    Same make, same exact mileage and year, price within 5 %. Mileage is the
    fingerprint: two different cars rarely share it to the kilometre.
    """
    km, price = rec.get("km"), rec.get("price")
    if not km or km < 1000 or not price:
        return None
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    # Only an EARLIER listing can be the original; otherwise a rescore would
    # mark the first copy as a duplicate of the second as well.
    first = rec.get("first_seen") or "9999"
    rows = c.execute(
        "SELECT uid, source, make, year, price FROM listings WHERE km = ? AND uid != ? "
        "AND first_seen > ? AND (first_seen < ? OR (first_seen = ? AND uid < ?)) "
        "AND price BETWEEN ? AND ? ORDER BY first_seen",
        (km, rec.get("uid", ""), since, first, first, rec.get("uid", ""),
         price * 0.95, price * 1.05)).fetchall()
    mk = make_key(rec.get("make"))
    for r in rows:
        if mk and r["make"] and make_key(r["make"]) != mk:
            continue
        if rec.get("year") and r["year"] and r["year"] != rec["year"]:
            continue
        return r["uid"]
    return None
