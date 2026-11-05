# Car Agent

Watches AutoScout24, Kleinanzeigen and (through its alert e-mails) mobile.de for used cars. It scores every new listing against your criteria and the market price, and pushes real deals to your phone. It also drafts the first message to the seller. Preferences and results live in a web dashboard. No AI calls, no browser automation.

## How each portal is read

| Portal | How | Notes |
|---|---|---|
| AutoScout24 | Search page, embedded JSON (`__NEXT_DATA__`) | The ad page also gives AutoScout24's market median, accident history, owners, HU and service book |
| Kleinanzeigen | Search page HTML (category `c216`) | Mostly private sellers, so the most bargains and the most scams. The ad page gives HU, damage, seller account age |
| mobile.de | Saved-search alert e-mails, read over IMAP | mobile.de returns 403 to scripts, and its Search API is dealer-only. The mailbox is opened read-only. |

Hard filters (price, km, year, fuel, gearbox, seller type, radius) go into the portal's own search URL, so cars outside them are never fetched.

## Run it

Needs Docker and git (on Windows/macOS: Docker Desktop, with "Start when you sign in" turned on).

```bash
git clone https://github.com/utsavchaudhry/car-agent.git
cd car-agent
docker compose up -d --build
```

Dashboard: **http://localhost:8081** (to use another port, change the left number in `docker-compose.yml`).

1. **Settings → What to look for**: add one row per make/model (e.g. `VW` / `Golf`, `Mercedes-Benz` / `C-Klasse`, `Mazda` / `CX-5`). Empty make = every car. Set **your postcode** and radius, then budget, km, year, fuel and gearbox, and press **Save**. The defaults are a placeholder: any car up to 15,000 € within 100 km of Garching near Munich.
2. **Settings → Sources → Kleinanzeigen**: set your town. Search for cars on kleinanzeigen.de in your town and copy two parts of the URL. In `kleinanzeigen.de/s-autos/muenchen/c216l6411`, `muenchen` is the location slug and `l6411` is the location id.
3. Install the **ntfy** app, tap **+**, subscribe to the topic under **Settings → Phone notifications**, then press **Send test**.

The first scan of a search only fills the database (that stock is not news), so alerts start from the second scan.

## mobile.de

1. On mobile.de, run your search, press **Suche speichern**, and turn e-mail notifications on.
2. Pick a mailbox the agent may read. For Gmail, create an app password (Google account → Security → 2-Step Verification → App passwords).
3. Create a file named `.env` next to `docker-compose.yml` containing `IMAP_PASSWORD=<the app password>`, then run `docker compose up -d`.
4. **Settings → Sources**: tick mobile.de, fill in server and user, **Save**, then press **Test mailbox**.

The alert layout is undocumented, so the parser keys on the listing links and reads price, km and first registration from the text around them. The last five raw alerts are kept in `data/mail_samples/` for checking.

## Scoring

Every car starts at 45. The price against the market counts most: AutoScout24's published median when the ad page has one, otherwise the median of similar cars the agent has seen (same make and model, ±1 year, similar km). Mileage per year, accident damage, service book, owners, HU, equipment you want, and distance add or subtract points.

- **HIT** (default 80+): pushed to your phone. Only cars whose ad page was read, and never one priced at market.
- **MAYBE** (60+): dashboard only.
- **Scam screening**: far below market, seller "abroad" / "Spedition" / "Treuhand", or a brand-new Kleinanzeigen account. Risk 60+ is rejected; 30–59 stays visible with a warning but never alerts.
- **Duplicates**: the same car on two portals, or at several branches of one dealer, is matched by exact mileage. Only the first copy alerts; the rest are hidden unless you tick **Duplicates**.

Sold cars are noticed by re-opening matched ads; a removed ad redirects to a search page and is marked GONE.

## Use it fairly

This is a personal search assistant, not a crawler. Each scan makes a handful of requests, spaced a few seconds apart, every 15 minutes by default. Keep it that way. The portals' terms do not allow automated access, so you run it at your own risk. Messages to sellers are only drafted; you send them yourself.

## Optional

Password-protect the dashboard: add `DASHBOARD_PASSWORD=choose-something` to `.env`, then run `docker compose up -d`.

Update: `git pull && docker compose up -d --build`. Logs: `docker compose logs -f`.

Tests: `python3 -m unittest discover -s tests` (no network needed).
