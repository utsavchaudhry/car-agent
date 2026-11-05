"""Scam screening. Pure functions.

Car-ad fraud on Kleinanzeigen follows one pattern: a desirable car far below
market, a seller who is "abroad" and wants to ship it via a "Spedition" or
"Treuhand" service, an account a few days old. Each signal adds risk:

    >= 60  SCAM     rejected, never shown as a match
    >= 30  SUSPECT  stays on the dashboard with a warning, never pushed
"""
import datetime
import config
from text import norm, find_all_terms
from match import market_ratio


def _days_since(ddmmyyyy):
    try:
        d = datetime.datetime.strptime(ddmmyyyy, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None
    return (datetime.date.today() - d).days


def assess(rec):
    """-> (risk 0-100, [flags])."""
    risk, flags = 0, []
    ratio = market_ratio(rec)
    if ratio is not None:
        if ratio * 100 < config.SCAM_BELOW_MARKET_PCT:
            risk += 60
            flags.append(f"{round((1 - ratio) * 100)} % below comparable cars")
        elif ratio * 100 < config.SUSPECT_BELOW_MARKET_PCT:
            risk += 30
            flags.append(f"{round((1 - ratio) * 100)} % below comparable cars")
    # Without enough comparables (a new search, a rare model) the market check
    # is silent, so catch the crude bait too: a nearly new car for pocket money.
    year, price = rec.get("year"), rec.get("price")
    if ratio is None and year and price and rec.get("seller") != "dealer":
        age = datetime.date.today().year - year
        floor = 7000 if age <= 2 else 5000 if age <= 4 else 0
        if price < floor:
            risk += 30
            flags.append(f"{price} € is very cheap for a {year} car")
    if rec.get("price_label") == "too_low":
        risk += 35
        flags.append("portal rates the price as too low")

    age = _days_since(rec.get("seller_since"))
    if age is not None and age < config.NEW_SELLER_DAYS and rec.get("seller") != "dealer":
        risk += 30
        flags.append(f"seller account only {age} days old")

    text = norm(" | ".join(str(rec.get(k) or "") for k in ("title", "body")))
    phrases = find_all_terms(config.SCAM_PHRASES, text, negatable=False)
    if phrases:
        # Dealers legitimately deliver and take deposits; scammers pose as private.
        per = 10 if rec.get("seller") == "dealer" else 25
        risk += min(50, per * len(phrases))
        flags.append("says " + ", ".join(f"'{p}'" for p in phrases[:3]))

    return min(100, risk), flags


def verdict(risk):
    return "SCAM" if risk >= 60 else "SUSPECT" if risk >= 30 else "OK"


def apply(rec, score, verdict_, reasons):
    """Fold the scam check into a matcher result. -> (score, verdict, reasons, risk, flags)."""
    risk, flags = assess(rec)
    v = verdict(risk)
    if v == "SCAM":
        return 0, "REJECT", [f"scam risk {risk}: " + "; ".join(flags)], risk, flags
    if flags:
        reasons = reasons + ["caution: " + "; ".join(flags)]
        if v == "SUSPECT" and verdict_ == "HIT":
            verdict_ = "MAYBE"          # stays visible, never reaches the phone
    return score, verdict_, reasons, risk, flags
