"""Offline tests: text helpers, the mobile.de alert parser, the matcher.

    python3 -m unittest discover -s tests
"""
import os, sys, tempfile, unittest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import config, match, scam                      # noqa: E402
from text import norm, find_term, mm_yyyy, make_key, model_key, fuel_key  # noqa: E402
import mailbox_mobile                           # noqa: E402

# A made-up alert in the shape mobile.de alerts are expected to have: a
# click-tracking link wrapping the listing URL, and a plain listing URL.
ALERT = """<html><body><table>
<tr><td><a href="https://click.mobile.de/ls/click?upn=abc%3Dhttps%253A%252F%252Fsuchen.mobile.de%252Ffahrzeuge%252Fdetails.html%253Fid%253D412345678%2526ref%253Dmail"><img src="x.jpg"></a></td>
<td><a href="https://click.mobile.de/ls/click?upn=abc%3Dhttps%253A%252F%252Fsuchen.mobile.de%252Ffahrzeuge%252Fdetails.html%253Fid%253D412345678%2526ref%253Dmail">Volkswagen Golf 1.5 TSI Life</a><br>
EZ 03/2020, 64.500 km, 110 kW (150 PS), Benzin, Schaltgetriebe<br>
<b>16.490 €</b> Guter Preis<br>Händler, DE-80807 München</td></tr>
<tr><td><a href="https://suchen.mobile.de/auto-inserat/skoda-octavia-combi-2-0-tdi/398765432.html?utm_source=mail">Skoda Octavia Combi 2.0 TDI Style</a><br>
Erstzulassung: 11/2019 · 98.000 km · 110 kW · Diesel · Automatik<br>14.900 € · ab 199 € mtl.<br>Privatanbieter, 85748 Garching</td></tr>
<tr><td><a href="https://www.mobile.de/hilfe">Hilfe</a> <a href="https://click.mobile.de/unsubscribe?x=1">Abmelden</a></td></tr>
</table></body></html>
"""


class Text(unittest.TestCase):
    def test_negation(self):
        self.assertIsNone(find_term(["motorschaden"], norm("Kein Motorschaden, läuft super")))
        self.assertEqual(find_term(["motorschaden"], norm("Motorschaden, an Bastler")), "motorschaden")
        self.assertIsNone(find_term(["teile"], norm("Teilleder Ausstattung")))

    def test_keys(self):
        self.assertEqual(mm_yyyy("Juli 2018"), "07/2018")
        self.assertEqual(mm_yyyy("07-2016"), "07/2016")
        self.assertEqual(make_key("VW"), "volkswagen")
        self.assertEqual(model_key("C-Klasse"), model_key("c_klasse"))
        self.assertEqual(fuel_key("Elektro/Benzin"), "hybrid")


class MobileAlert(unittest.TestCase):
    def test_parse(self):
        rows = mailbox_mobile.parse_alert(ALERT, resolve=False)
        self.assertEqual([r["uid"] for r in rows], ["mde:412345678", "mde:398765432"])
        golf, octavia = rows
        self.assertEqual((golf["price"], golf["km"], golf["reg"], golf["seller"]), (16490, 64500, "03/2020", "dealer"))
        self.assertEqual(golf["price_label"], "good")
        # "ab 199 € mtl." is a finance rate, not the price
        self.assertEqual((octavia["price"], octavia["gearbox"], octavia["seller"]), (14900, "automatic", "private"))


def car(**kw):
    base = dict(uid="t:1", source="AutoScout24", title="VW Golf 1.5 TSI", make="Volkswagen", model="Golf",
                price=12000, km=60000, year=2019, reg="05/2019", fuel="petrol", gearbox="manual",
                seller="dealer", distance_km=20, price_label="", body="")
    base.update(kw)
    return base


class Matcher(unittest.TestCase):
    def test_hard_filters(self):
        self.assertEqual(match.evaluate(car(price=99999))[1], "REJECT")
        self.assertEqual(match.evaluate(car(title="Suche VW Golf"))[1], "REJECT")
        self.assertEqual(match.evaluate(car(body="Motorschaden"))[1], "REJECT")
        self.assertNotEqual(match.evaluate(car(body="kein Motorschaden"))[1], "REJECT")

    def test_market_price_blocks_hit(self):
        good = car(market_median=14000, market_n=-1, accident=0, service_history=1, owners=1, hu="10/2028")
        fair = dict(good, market_median=12000)
        self.assertEqual(match.evaluate(good)[1], "HIT")
        self.assertEqual(match.evaluate(fair)[1], "MAYBE")

    def test_scam(self):
        bait = car(seller="private", source="Kleinanzeigen", price=6000, market_median=14000, market_n=8,
                   body="Ich bin beruflich im Ausland, Lieferung per Spedition")
        risk, flags = scam.assess(bait)
        self.assertGreaterEqual(risk, 60, flags)


if __name__ == "__main__":
    unittest.main()
