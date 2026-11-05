"""Score a car listing against the settings. Pure functions, no I/O.

Hard filters (price, km, year, fuel, gearbox, seller, distance, block terms)
reject outright. Everything else moves a score that starts at 45; the score
decides HIT (pushed to the phone), MAYBE (dashboard only) or REJECT.

The biggest single signal is the price against the market: the median
AutoScout24 prints on its ad pages, else the median of comparable listings the
agent has already seen (market.py), else AutoScout24's coarse price label.
"""
import datetime
import config
from text import norm, find_term, find_all_terms, month_year

# Before the ad page is read only AutoScout24's coarse label is known. Its
# "top price" band alone spans 5-20 % under the market, so the label earns less
# than the exact discount the ad page reveals.
LABEL_POINTS = {"top": 10, "good": 6, "fair": 0, "high": -8, "very_high": -15}
LABEL_TEXT = {"top": "top price", "good": "good price", "fair": "fair price",
              "high": "above market", "very_high": "well above market", "too_low": "suspiciously cheap"}


def _months_until(mm_yyyy):
    mo, yr = month_year(mm_yyyy or "")
    if not yr:
        return None
    t = datetime.date.today()
    return (yr - t.year) * 12 + ((mo or 6) - t.month)


def market_ratio(rec):
    """price / market median, if a trustworthy median is known.

    market_n is the number of comparables behind our own median; -1 marks the
    median AutoScout24 publishes on the ad page, which is always trusted.
    """
    med, n = rec.get("market_median"), rec.get("market_n") or 0
    if med and rec.get("price") and (n >= 5 or n == -1):
        return rec["price"] / med
    return None


def market_text(rec, ratio):
    pct = round((ratio - 1) * 100)
    where = ("AutoScout24's market price" if rec.get("market_n") == -1
             else f"the median of {rec.get('market_n')} similar cars")
    return f"{abs(pct)} % {'under' if pct < 0 else 'over'} {where} ({rec['market_median']} €)"


def evaluate(rec):
    """-> (score 0-100, verdict, [reasons])."""
    reasons = []
    price, km, year = rec.get("price"), rec.get("km"), rec.get("year")
    title = norm(rec.get("title"))
    text = norm(" | ".join(str(rec.get(k) or "") for k in ("title", "body")))

    def reject(why):
        return 0, "REJECT", [why]

    # ---------------------------------------------------------- hard filters
    if not price:
        return reject("no price stated")
    if price < config.MIN_PLAUSIBLE_PRICE:
        return reject(f"{price} € looks like a placeholder price")
    if price > config.MAX_PRICE:
        return reject(f"{price} € over budget ({config.MAX_PRICE} €)")
    if km is not None and config.MAX_KM and km > config.MAX_KM:
        return reject(f"{km:,} km over the limit".replace(",", "."))
    if year and config.MIN_YEAR and year < config.MIN_YEAR:
        return reject(f"first registered {year}, before {config.MIN_YEAR}")
    if rec.get("fuel") and config.FUELS and rec["fuel"] not in config.FUELS:
        return reject(f"fuel {rec['fuel']} not wanted")
    if rec.get("gearbox") and config.GEARBOX in ("automatic", "manual") and rec["gearbox"] != config.GEARBOX:
        return reject(f"{rec['gearbox']} gearbox")
    if rec.get("seller") and rec["seller"] not in config.SELLER_TYPES:
        return reject(f"{rec['seller']} seller")
    if not config.INCLUDE_NEW and (rec.get("offer_type") == "N" or (km is not None and km < 100 and not rec.get("reg"))):
        return reject("new car")
    dist = rec.get("distance_km")
    if dist is not None and dist > config.RADIUS_KM * 1.15:
        return reject(f"{dist} km away")
    term = find_term(config.BLOCK_TERMS_TITLE, title, negatable=False)
    if term:
        return reject(f"title says '{term}'")
    term = find_term(config.BLOCK_TERMS, text)
    if term:
        return reject(f"ad says '{term}'")

    # ----------------------------------------------------------------- score
    # Calibrated so an ordinary car at a fair price lands around 60 (MAYBE at
    # best) and a HIT needs a real discount on top of a sound car.
    score = 45

    # Price against the market -- the reason to be fast.
    label = rec.get("price_label") or ""
    ratio = market_ratio(rec)
    if ratio is not None:
        score += (18 if ratio <= 0.85 else 12 if ratio <= 0.90 else 6 if ratio <= 0.95 else
                  2 if ratio <= 1.0 else -15 if ratio >= 1.20 else -8 if ratio >= 1.10 else 0)
        reasons.append(market_text(rec, ratio))
    elif label in LABEL_POINTS:
        score += LABEL_POINTS[label]
        reasons.append(f"{LABEL_TEXT[label]} ({rec.get('source')})")

    # Cheaper within budget helps a little; the market comparison matters more.
    score += round(max(0, (config.MAX_PRICE - price) / config.MAX_PRICE) * 5)
    if price <= config.PREFER_UNDER:
        score += 2

    # Mileage, overall and per year.
    if km is not None:
        if config.MAX_KM and km <= config.MAX_KM * 0.5:
            score += 2
        if year:
            age = max(0.5, datetime.date.today().year - year + 0.5)
            per_year = km / age
            if per_year <= 10000:
                score += 5
                reasons.append(f"low mileage ({round(per_year / 1000)}k km/year)")
            elif per_year <= 15000:
                score += 2
            elif per_year >= 25000:
                score -= 6
                reasons.append(f"high mileage ({round(per_year / 1000)}k km/year)")
            elif per_year >= 20000:
                score -= 3
    else:
        score -= 3
        reasons.append("mileage not stated")

    if year and config.MIN_YEAR:
        if year >= config.MIN_YEAR + 4:
            score += 3
        elif year >= config.MIN_YEAR + 2:
            score += 1

    # History, from the ad page.
    acc = rec.get("accident")
    if acc == 1:
        score -= int(config.ACCIDENT_PENALTY)
        reasons.append("accident damage declared")
    elif acc == 0:
        score += 3
    if rec.get("service_history") == 1:
        score += 5
        reasons.append("full service history")
    owners = rec.get("owners")
    if owners:
        if owners == 1:
            score += 4
            reasons.append("1 previous owner")
        elif owners == 2:
            score += 1
        elif owners >= 4:
            score -= 5
            reasons.append(f"{owners} previous owners")
    hu = _months_until(rec.get("hu"))
    if hu is not None:
        if hu >= 18:
            score += 3
            reasons.append(f"HU until {rec['hu']}")
        elif hu <= 3:
            score -= 6
            reasons.append("HU due" if hu >= 0 else "HU expired")

    pref = config.PREFER_SELLER
    if pref in ("private", "dealer") and rec.get("seller") == pref:
        score += 4

    if dist is not None:
        if dist <= 30:
            score += 2
        elif dist > config.RADIUS_KM * 0.8:
            score -= 3

    nice = find_all_terms(config.NICE_TO_HAVE, text + " | " + norm(rec.get("equipment") or ""))
    if nice:
        score += min(8, 2 * len(nice))
        reasons.append("has " + ", ".join(nice[:5]))
    soft = find_all_terms(config.SOFT_NEGATIVE, text)
    score -= 8 * len(soft)
    if soft:
        reasons.append("mentions " + ", ".join(soft))

    if rec.get("twin_of"):
        src = rec["twin_of"].split(":")[0]
        reasons.append("same car listed again" if src == rec.get("uid", "").split(":")[0]
                       else "same car also listed on " + {"as24": "AutoScout24", "ka": "Kleinanzeigen",
                                                           "mde": "mobile.de"}.get(src, src))

    score = max(0, min(100, score))
    # A HIT is a deal. A well-kept car at the going rate can wait for the
    # dashboard; only a known discount (or an unknown market) may ping the phone.
    at_market = (ratio is not None and ratio > 0.97) or (ratio is None and label in ("fair", "high", "very_high"))
    if score >= config.HIT_SCORE and at_market:
        verdict = "MAYBE"
        reasons.append("good car, but priced at market")
    elif score >= config.HIT_SCORE:
        verdict = "HIT"
    elif score >= config.MAYBE_SCORE:
        verdict = "MAYBE"
    else:
        verdict = "REJECT"
        reasons.append(f"score below {config.MAYBE_SCORE}")
    return score, verdict, reasons
