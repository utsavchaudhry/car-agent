"""Draft a first message to a seller. Drafts only -- the agent never sends.

The portals forbid automated messaging, and the first message to a seller is
where you decide whether a car is worth a trip. Drafts appear on the dashboard
and in $DATA_DIR/outbox/; copy, adjust, and send from the listing page.
"""
import os, re, datetime
import config

OUTBOX = os.path.join(config.DATA_DIR, "outbox")


def _b():
    return config.BUYER


def _car(rec):
    bits = [rec.get("title") or "Fahrzeug"]
    facts = []
    if rec.get("price"):
        facts.append(f"{rec['price']:,} €".replace(",", "."))
    if rec.get("km") is not None:
        facts.append(f"{rec['km']:,} km".replace(",", "."))
    if rec.get("reg"):
        facts.append(f"EZ {rec['reg']}")
    return bits[0] + (f" ({', '.join(facts)})" if facts else "")


def _questions_de(rec):
    q = []
    if rec.get("accident") is None:
        q.append("Ist das Fahrzeug unfallfrei?")
    if rec.get("service_history") != 1:
        q.append("Gibt es ein Scheckheft bzw. Rechnungen zu Inspektionen und Reparaturen?")
    q.append("Wann war die letzte Inspektion, und stehen größere Arbeiten an "
             "(Zahnriemen/Steuerkette, Bremsen, Reifen)?")
    if not rec.get("owners"):
        q.append("Wie viele Vorbesitzer hatte der Wagen?")
    if not rec.get("hu"):
        q.append("Bis wann läuft die HU?")
    return q


def _questions_en(rec):
    q = []
    if rec.get("accident") is None:
        q.append("Has the car ever had accident damage?")
    if rec.get("service_history") != 1:
        q.append("Is there a service book or invoices for services and repairs?")
    q.append("When was the last service, and is any major work coming up "
             "(timing belt/chain, brakes, tyres)?")
    if not rec.get("owners"):
        q.append("How many previous owners has it had?")
    if not rec.get("hu"):
        q.append("When is the next HU/TÜV due?")
    return q


def _sig():
    B = _b()
    return "\n".join(x for x in (B.get("name", ""), B.get("phone", "")) if x)


def draft_de(rec):
    B = _b()
    lines = ["Hallo,", "",
             f"ich interessiere mich für Ihren {_car(rec)}. Ist das Fahrzeug noch zu haben?", "",
             "Vorab ein paar kurze Fragen:"]
    lines += [f"- {q}" for q in _questions_de(rec)]
    lines += ["",
              f"Wenn alles passt, würde ich mir den Wagen gern {B.get('availability_de') or 'zeitnah'} "
              "ansehen und eine kurze Probefahrt machen.", "",
              "Viele Grüße"]
    sig = _sig()
    if sig:
        lines.append(sig)
    return "\n".join(lines)


def draft_en(rec):
    B = _b()
    lines = ["Hello,", "",
             f"I'm interested in your {_car(rec)}. Is it still available?", "",
             "A few quick questions first:"]
    lines += [f"- {q}" for q in _questions_en(rec)]
    lines += ["",
              f"If everything fits, I'd like to see the car {B.get('availability_en') or 'soon'} "
              "and take a short test drive.", "",
              "Best regards"]
    sig = _sig()
    if sig:
        lines.append(sig)
    return "\n".join(lines)


def write_draft(rec):
    os.makedirs(OUTBOX, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (rec.get("title") or "car").lower()).strip("-")[:40]
    path = os.path.join(OUTBOX, f"{datetime.date.today()}_{rec['uid'].replace(':', '-')}_{slug}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{rec.get('url', '')}\n\n--- Deutsch ---\n{draft_de(rec)}\n\n--- English ---\n{draft_en(rec)}\n")
    return path
