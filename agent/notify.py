"""Push a match to your phone via ntfy.sh. Free, no account, no API key.

Install the "ntfy" app (Android/iOS) and subscribe to the topic shown on the
dashboard's Settings page. E-mail forwarding needs a free ntfy account token.
"""
import urllib.request
import config


def push(title, body, url=None, email=False, priority="high"):
    topic = getattr(config, "NTFY_TOPIC", "")
    if not topic:
        return False
    headers = {"Title": title.encode("utf-8"), "Priority": priority, "Tags": "red_car"}
    if url:
        headers["Click"] = url
    token = getattr(config, "NTFY_TOKEN", "")
    address = getattr(config, "NOTIFY_EMAIL", "")
    if email and address and token:      # ntfy.sh refuses e-mail for anonymous publishers
        headers["Email"] = address
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://ntfy.sh/" + topic, data=body.encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status < 300
    except Exception:
        return False


def fmt_km(km):
    return f"{round(km / 1000)}k km" if km else "? km"


def summary(rec):
    """'2018 · 94k km · petrol · automatic · 16 km away (München)'"""
    bits = [str(rec.get("year") or "?"), fmt_km(rec.get("km"))]
    for k in ("fuel", "gearbox"):
        if rec.get(k):
            bits.append(rec[k])
    where = rec.get("city") or rec.get("zip") or ""
    if rec.get("distance_km") is not None:
        where = f"{rec['distance_km']} km away" + (f" ({where})" if where else "")
    if where:
        bits.append(where)
    return " · ".join(bits)


def announce(rec, email=True):
    title = f"{rec.get('price')} € · {rec.get('title', '')[:60]}"
    reasons = (rec.get("reasons") or "").split(" ; ")[:3]
    body = (f"{summary(rec)}\n"
            f"score {rec.get('score')} · {rec.get('source')}\n"
            + ("; ".join(r for r in reasons if r) + "\n" if reasons else "")
            + f"{rec.get('url', '')}")
    return push(title, body, url=rec.get("url"), email=email)
