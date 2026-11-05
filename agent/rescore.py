#!/usr/bin/env python3
"""Re-grade every stored listing. The dashboard runs this after each settings
change. Every fact the matcher uses is a column, so a rescore sees exactly what
the scan saw -- plus a market comparison that has grown since.

Listings already reported keep reported=1 so nothing is pushed twice; one that
newly becomes a match is set back to unreported so it shows in the digest.
A listing marked GONE stays gone.
"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import store
from scan import grade


def main():
    c = store.conn()
    rows = c.execute("SELECT * FROM listings").fetchall()
    changed = 0
    for r in rows:
        rec = dict(r)
        grade(c, rec)
        verdict = "GONE" if r["verdict"] == "GONE" and rec["verdict"] != "REJECT" else rec["verdict"]
        if rec["score"] != r["score"] or verdict != r["verdict"] or rec["scam_risk"] != (r["scam_risk"] or 0) \
                or rec["reasons"] != r["reasons"]:
            changed += 1
            newly_good = verdict in ("HIT", "MAYBE") and r["verdict"] == "REJECT"
            store.update(c, r["uid"], score=rec["score"], verdict=verdict, reasons=rec["reasons"],
                         scam_risk=rec["scam_risk"], scam_flags=rec["scam_flags"],
                         market_median=rec["market_median"], market_n=rec["market_n"],
                         twin_of=rec["twin_of"], **({"reported": 0} if newly_good else {}))
    c.commit()
    counts = {row["verdict"]: row["n"] for row in
              c.execute("SELECT verdict, COUNT(*) n FROM listings GROUP BY verdict")}
    c.close()
    print(f"rescored {len(rows)} listings, {changed} changed -> {counts}")


if __name__ == "__main__":
    main()
