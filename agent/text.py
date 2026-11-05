"""Text helpers shared by the parsers and the matcher. Pure functions."""
import re, html, functools

# Ads are full of typographic punctuation that looks like ASCII and matches
# none of it (non-breaking hyphens, soft hyphens, narrow spaces). Fold them all.
UML = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                     "Ä": "ae", "Ö": "oe", "Ü": "ue", "é": "e", "ë": "e", "š": "s", "Š": "s",
                     "‐": "-", "‑": "-", "‒": "-", "–": "-",
                     "—": "-", "―": "-", "−": "-", "­": "",
                     " ": " ", " ": " ", "’": "'", "‘": "'"})


def norm(s):
    return (s or "").translate(UML).lower()


def clean(s):
    """HTML fragment -> one line of plain text."""
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>", " \n ", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def num(s):
    """'12.345 €' -> 12345.0, '1.234,56' -> 1234.56. German number format."""
    if s is None:
        return None
    s = html.unescape(str(s))
    s = re.sub(r"[^\d,.]", "", s)
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def grp(pat, text, flags=0, n=1):
    m = re.search(pat, text or "", flags)
    return m.group(n) if m else ""


@functools.lru_cache(maxsize=None)
def term_re(term):
    """A filter term as a whole-word pattern ("teile" must not hit "Teilleder")."""
    t = norm(term).strip()
    pat = re.escape(t).replace(r"\ ", r"\s+")
    if t[:1].isalnum():
        pat = r"\b" + pat
    if t[-1:].isalnum():
        pat = pat + r"\b"
    return re.compile(pat)


# "kein Motorschaden", "ohne Ölverlust", "frei von Hagelschaden" state the
# ABSENCE of the problem. Sellers write that a lot, so a plain match would
# reject or penalise exactly the cars that are fine.
_NEGATION = re.compile(r"\b(?:kein|keine|keinen|keiner|ohne|nicht|nie|frei\s+von|no)\b[\w\s,/-]{0,25}$")


def find_term(terms, text, negatable=True):
    """-> first term found in `text` (already norm()'d), skipping negated mentions."""
    for term in terms or []:
        for m in term_re(term).finditer(text):
            if negatable and not norm(term).startswith(("ohne", "kein", "nicht")):
                before = text[max(0, m.start() - 30):m.start()]
                if _NEGATION.search(before):
                    continue
            return term
    return None


def find_all_terms(terms, text, negatable=True):
    return [t for t in (terms or []) if find_term([t], text, negatable)]


# --------------------------------------------------------------- vehicle keys
MONTHS = {"januar": 1, "jan": 1, "februar": 2, "feb": 2, "maerz": 3, "marz": 3, "mar": 3,
          "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
          "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
          "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12}


def month_year(s):
    """'07/2016', '07-2016', 'Juli 2018', '2018' -> (month|None, year|None)."""
    t = norm(s)
    m = re.search(r"\b(\d{1,2})\s*[/.-]\s*((?:19|20)\d{2})\b", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b([a-z]+)\.?\s+((?:19|20)\d{2})\b", t)
    if m and m.group(1) in MONTHS:
        return MONTHS[m.group(1)], int(m.group(2))
    m = re.search(r"\b((?:19|20)\d{2})\b", t)
    if m:
        return None, int(m.group(1))
    return None, None


def mm_yyyy(s):
    mo, yr = month_year(s)
    if not yr:
        return ""
    return f"{mo:02d}/{yr}" if mo else str(yr)


MAKE_ALIASES = {
    "vw": "volkswagen", "volkswagen": "volkswagen",
    "mercedes": "mercedes-benz", "mercedes-benz": "mercedes-benz", "mercedes benz": "mercedes-benz",
    "mercedes_benz": "mercedes-benz", "benz": "mercedes-benz",
    "alfa": "alfa-romeo", "alfa romeo": "alfa-romeo", "alfa-romeo": "alfa-romeo",
    "land rover": "land-rover", "land-rover": "land-rover", "landrover": "land-rover",
    "citroen": "citroen", "skoda": "skoda", "seat": "seat", "cupra": "cupra",
    "bmw": "bmw", "audi": "audi", "opel": "opel", "ford": "ford", "toyota": "toyota",
    "honda": "honda", "mazda": "mazda", "nissan": "nissan", "hyundai": "hyundai",
    "kia": "kia", "renault": "renault", "peugeot": "peugeot", "fiat": "fiat",
    "volvo": "volvo", "mini": "mini", "dacia": "dacia", "suzuki": "suzuki",
    "mitsubishi": "mitsubishi", "subaru": "subaru", "tesla": "tesla", "porsche": "porsche",
    "jeep": "jeep", "lexus": "lexus", "smart": "smart", "jaguar": "jaguar", "mg": "mg",
    "byd": "byd", "ds": "ds", "polestar": "polestar", "chevrolet": "chevrolet", "lancia": "lancia",
    "saab": "saab", "infiniti": "infiniti", "ssangyong": "ssangyong", "alpine": "alpine",
}


def make_key(make):
    t = norm(make).strip()
    return MAKE_ALIASES.get(t, re.sub(r"\s+", "-", t))


def model_key(model):
    """'C-Klasse' / 'C Klasse' / 'c_klasse' -> 'cklasse'; 'CX-5' -> 'cx5'."""
    return re.sub(r"[^a-z0-9]", "", norm(model))


def split_title(title):
    """Best-effort (make, model) from a free-text title like 'VW Golf 1.4 TSI'."""
    t = norm(title)
    words = re.findall(r"[a-z0-9][a-z0-9\-]*", t)
    for i in range(len(words)):
        for span in (2, 1):
            cand = " ".join(words[i:i + span])
            if cand in MAKE_ALIASES:
                rest = words[i + span:i + span + 1]
                return MAKE_ALIASES[cand], (rest[0] if rest else "")
    return "", ""


def fuel_key(s):
    """Portal fuel label -> petrol | diesel | hybrid | electric | gas | other | ''."""
    t = norm(s)
    if not t:
        return ""
    if "elektro" in t and ("/" in t or "benzin" in t or "diesel" in t) or "hybrid" in t or "plug-in" in t:
        return "hybrid"
    if "elektro" in t or t in ("electric", "ev"):
        return "electric"
    if "diesel" in t:
        return "diesel"
    if "benzin" in t or "super" in t or "petrol" in t:
        return "petrol"
    if "lpg" in t or "cng" in t or "gas" in t:
        return "gas"
    return "other"


def gearbox_key(s):
    t = norm(s)
    if not t:
        return ""
    if "automat" in t or "dsg" in t or "tiptronic" in t or "halbautomatik" in t:
        return "automatic"
    if "schalt" in t or "manuell" in t or "manual" in t:
        return "manual"
    return ""
