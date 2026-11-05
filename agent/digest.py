#!/usr/bin/env python3
"""Nightly digest. Deterministic, zero tokens.

Writes $DATA_DIR/digests/YYYY-MM-DD.md, drafts a message per new match and
marks them reported. Pushes an alert if nothing at all was fetched in 24h, so
a blocked source cannot pass for a quiet market.
"""
import sys, os, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config, store, contact, notify

DIGESTS = os.path.join(config.DATA_DIR, "digests")


def main():
    always = "--always" in sys.argv
    c = store.conn()
    hits = [dict(r) for r in store.unreported_hits(c)]

    today = datetime.date.today()
    since = (datetime.datetime.now() - datetime.timedelta(hours=24)).isoformat(timespec="seconds")
    scanned = c.execute("SELECT COALESCE(SUM(fetched),0) f, COALESCE(SUM(new),0) n FROM runs "
                        "WHERE ts > ? AND source NOT IN ('scan','digest','seed')", (since,)).fetchone()
    errs = c.execute("SELECT DISTINCT source FROM runs WHERE ts > ? AND "
                     "(note LIKE 'ERROR%' OR note LIKE 'WARNING%' OR note LIKE 'ABORTED%')",
                     (since,)).fetchall()

    if scanned["f"] == 0:
        notify.push("Car agent is not fetching",
                    "No listings fetched in 24h. Check the Activity tab on the dashboard.")
        store.log_run(c, "digest", 0, 0, 0, "ALERT: zero fetched in 24h")
        c.commit(); c.close()
        print(f"{today}: FAULT -- nothing fetched in 24h")
        return

    if not hits and not always:
        store.log_run(c, "digest", 0, 0, 0, "quiet day")
        c.commit(); c.close()
        print(f"{today}: nothing new worth reporting ({scanned['n']} new listings seen)")
        return

    os.makedirs(DIGESTS, exist_ok=True)
    path = os.path.join(DIGESTS, f"{today}.md")
    lines = [f"# Car digest — {today:%A %d %B %Y}", "",
             f"Scanned **{scanned['f']}** listings in the last 24h, **{scanned['n']}** of them new. "
             f"**{len(hits)}** worth a look.", ""]
    if errs:
        lines += ["> Source problems: " + ", ".join(e["source"] for e in errs), ""]

    for i, h in enumerate(hits, 1):
        drafted = contact.write_draft(h)
        lines += [
            f"## {i}. {h['title']}", "",
            "| | |", "|---|---|",
            f"| **Price** | {h['price']} € |",
            f"| **Car** | {notify.summary(h)} |",
            f"| **History** | accident: {({0: 'no', 1: 'YES'}).get(h['accident'], '?')} · "
            f"owners: {h['owners'] or '?'} · HU: {h['hu'] or '?'} · "
            f"service book: {'yes' if h['service_history'] == 1 else '?'} |",
            f"| **Score** | {h['score']}/100 ({h['verdict']}) |",
            f"| **Source** | {h['source']} ({h['seller'] or 'seller ?'}) |",
            "", h["reasons"] or "", "",
            f"- Listing: {h['url']}",
            f"- Draft message (not sent): `outbox/{os.path.basename(drafted)}`", "",
        ]
    lines += ["---", "", "Drafts are **not sent**. Read one, adjust it, then send it from the listing page yourself."]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    c.executemany("UPDATE listings SET drafted=1 WHERE uid=?", [(h["uid"],) for h in hits])
    store.mark_reported(c, [h["uid"] for h in hits])
    store.log_run(c, "digest", scanned["f"], scanned["n"], len(hits), f"wrote {path}")
    c.commit(); c.close()
    print(f"{today}: {len(hits)} match(es) -> {path}")


if __name__ == "__main__":
    main()
