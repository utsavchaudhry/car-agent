import React, { useEffect, useState } from "react";
import { api } from "./api.js";

// Immutable set at a dotted path: set(obj, "sources.kleinanzeigen.pages", 3)
function setIn(obj, path, value) {
  const [k, ...rest] = path.split(".");
  return { ...obj, [k]: rest.length ? setIn(obj[k] || {}, rest.join("."), value) : value };
}
function getIn(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
}

const FUELS = [
  ["petrol", "Petrol"],
  ["diesel", "Diesel"],
  ["hybrid", "Hybrid"],
  ["electric", "Electric"],
  ["gas", "LPG / CNG"],
];

export default function Settings() {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [pwSet, setPwSet] = useState(null);

  useEffect(() => {
    api.settings().then((d) => {
      setS(d);
      setSaved(d);
    });
    api.status().then((st) => setPwSet(st.imap_password_set)).catch(() => {});
  }, []);

  if (!s) return <p className="muted">Loading…</p>;

  const dirty = JSON.stringify(s) !== JSON.stringify(saved);
  const bind = (path) => ({ value: getIn(s, path), onChange: (v) => setS(setIn(s, path, v)) });

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      const d = await api.saveSettings(s);
      setS(d);
      setSaved(d);
      setMsg({ ok: true, text: "Saved. Stored listings are being re-graded against the new criteria." });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function run(fn, okText) {
    setMsg(null);
    try {
      const r = await fn();
      setMsg({ ok: true, text: r?.text || okText });
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    }
  }

  async function resetAll() {
    if (!confirmReset) return setConfirmReset(true);
    const d = await api.defaults();
    setS({ ...d, ntfy_topic: s.ntfy_topic, ntfy_token: s.ntfy_token, notify_email: s.notify_email, buyer: s.buyer,
           sources: { ...d.sources, mobile_mail: s.sources.mobile_mail } });
    setConfirmReset(false);
    setMsg({ ok: true, text: "Defaults loaded. Press Save to apply them." });
  }

  const toggleIn = (path, value) => (on) => {
    const cur = getIn(s, path) || [];
    setS(setIn(s, path, on ? [...new Set([...cur, value])] : cur.filter((x) => x !== value)));
  };

  return (
    <div className="settings">
      <Section
        title="What to look for"
        hint="Each search runs on AutoScout24 and Kleinanzeigen. Leave make and model empty to search every car. Make and model are written as on the portals, e.g. VW / Golf, Mercedes-Benz / C-Klasse, Mazda / CX-5."
      >
        <div className="full">
          <SearchTable value={s.searches} onChange={(v) => setS(setIn(s, "searches", v))} />
        </div>
        <Text label="Your postcode" {...bind("home_zip")} />
        <Num label="Radius (km)" {...bind("radius_km")} />
      </Section>

      <Section title="Budget & car" hint="These go straight into the portal searches, so cars outside them are never even fetched.">
        <Num label="Max price (€)" {...bind("max_price")} />
        <Num label="Bonus if under (€)" {...bind("prefer_under")} />
        <Num label="Ignore prices below (€)" hint="Placeholder prices like “1 € VB”" {...bind("min_plausible_price")} />
        <Num label="Max mileage (km)" {...bind("max_km")} />
        <Num label="First registered from (year)" {...bind("min_year")} />
        <Field label="Gearbox">
          <select value={s.gearbox} onChange={(e) => setS(setIn(s, "gearbox", e.target.value))}>
            <option value="any">Any</option>
            <option value="automatic">Automatic</option>
            <option value="manual">Manual</option>
          </select>
        </Field>
        <Group label="Fuel (none ticked = any)">
          <div className="row">
            {FUELS.map(([k, l]) => (
              <Check key={k} label={l} value={(s.fuels || []).includes(k)} onChange={toggleIn("fuels", k)} />
            ))}
          </div>
        </Group>
        <Group label="Sellers">
          <div className="row">
            <Check label="Private" value={s.seller_types.includes("private")} onChange={toggleIn("seller_types", "private")} />
            <Check label="Dealer" value={s.seller_types.includes("dealer")} onChange={toggleIn("seller_types", "dealer")} />
            <Check label="Include new cars" {...bind("include_new")} />
          </div>
        </Group>
      </Section>

      <Section
        title="Scoring"
        hint="Every car starts at 50. The price against the market moves it most: AutoScout24's own rating, or else the median of similar cars the agent has seen. Mileage per year, accident history, service book, owners, HU and equipment add or subtract. HITs go to your phone; MAYBEs stay on the dashboard."
      >
        <Num label="HIT from score" {...bind("hit_score")} />
        <Num label="MAYBE from score" {...bind("maybe_score")} />
        <Num label="Minus for accident damage" {...bind("accident_penalty")} />
        <Field label="Prefer seller">
          <select value={s.prefer_seller} onChange={(e) => setS(setIn(s, "prefer_seller", e.target.value))}>
            <option value="any">No preference</option>
            <option value="private">Private (+5)</option>
            <option value="dealer">Dealer (+5)</option>
          </select>
        </Field>
        <Lines label="Nice to have (+3 each, max +12)" {...bind("nice_to_have")} rows={6} />
        <Lines label="Minus 8 points" {...bind("soft_negative")} rows={6} />
      </Section>

      <Section title="Filters" hint="One term per line. Matched as whole words, case-insensitive, umlauts folded (ü = ue). In the ad text, a negated mention (“kein Motorschaden”) does not count.">
        <Lines label="Reject if in the title" {...bind("block_terms_title")} rows={10} />
        <Lines label="Reject if in the ad text" {...bind("block_terms")} rows={10} />
      </Section>

      <Section title="Scam filter" hint="Car fraud follows a pattern: far below market, seller “abroad”, delivery by a “Spedition” or “Treuhand”, a brand-new account. Risk 60+ is rejected; 30–59 stays on the dashboard with a warning but never alerts.">
        <Num label="Scam if price under (% of market)" hint="of the median of similar cars" {...bind("scam_below_market_pct")} />
        <Num label="Suspect if price under (% of market)" {...bind("suspect_below_market_pct")} />
        <Num label="New seller account (days)" hint="Kleinanzeigen accounts younger than this are flagged" {...bind("new_seller_days")} />
        <Lines label="Scam phrases (+25 each, +10 for dealers)" {...bind("scam_phrases")} rows={8} />
      </Section>

      <Section title="Sources" hint="AutoScout24 and Kleinanzeigen are fetched directly. mobile.de blocks scripts, so it arrives through its own saved-search alert e-mails, read from a mailbox you choose.">
        <Check label="AutoScout24" {...bind("sources.autoscout24.enabled")} />
        <Num label="Pages per search (20 cars each)" {...bind("sources.autoscout24.pages")} />
        <span />
        <hr className="full" />
        <Check label="Kleinanzeigen" {...bind("sources.kleinanzeigen.enabled")} />
        <Text label="Location slug" {...bind("sources.kleinanzeigen.location_slug")} />
        <Text label="Location id" hint="the l1234 part of a search URL" {...bind("sources.kleinanzeigen.location_id")} />
        <Num label="Pages per search (25 ads each)" {...bind("sources.kleinanzeigen.pages")} />
        <hr className="full" />
        <Check label="mobile.de (alert e-mails)" {...bind("sources.mobile_mail.enabled")} />
        <Text label="IMAP server" {...bind("sources.mobile_mail.imap_host")} />
        <Text label="Mailbox user" hint="the e-mail address" {...bind("sources.mobile_mail.imap_user")} />
        <Text label="Folder" {...bind("sources.mobile_mail.folder")} />
        <Text label="Sender contains" {...bind("sources.mobile_mail.from_contains")} />
        <Num label="Look back (days)" {...bind("sources.mobile_mail.lookback_days")} />
        <Group label="Password">
          <div className="row">
            <span className={pwSet ? "ok small" : "bad small"}>
              {pwSet ? "IMAP_PASSWORD is set." : "IMAP_PASSWORD is not set."}
            </span>
            <span className="muted small">It lives in the .env file next to docker-compose.yml, never in these settings.</span>
            <button className="btn small" onClick={() => run(api.testMail)} disabled={dirty} title={dirty ? "Save first" : ""}>
              Test mailbox
            </button>
          </div>
        </Group>
      </Section>

      <Section title="Pace" hint="A scan makes about 2 requests per search per portal, plus up to the ad-page and re-check budgets below, spaced a few seconds apart. Set night interval to 0 to pause overnight.">
        <Num label="Scan every (min), daytime" {...bind("schedule.interval_min")} />
        <Num label="Day starts (hour)" {...bind("schedule.day_start_hour")} />
        <Num label="Day ends (hour)" {...bind("schedule.day_end_hour")} />
        <Num label="Scan every (min), night" {...bind("schedule.night_interval_min")} />
        <Num label="Ad pages read per scan" {...bind("max_detail_fetches")} />
        <Num label="Sold-checks per scan" {...bind("max_liveness_checks")} />
        <Field label="Nightly digest at">
          <input type="time" value={s.schedule.digest_time} onChange={(e) => setS(setIn(s, "schedule.digest_time", e.target.value))} />
        </Field>
      </Section>

      <Section title="Phone notifications" hint="Free, via ntfy.sh. Install the ntfy app, tap +, and subscribe to the topic below. Anyone who knows the topic can read it, so keep it private.">
        <Text label="ntfy topic" {...bind("ntfy_topic")} />
        <Field label=" ">
          <div className="row">
            <a className="btn small" href={`https://ntfy.sh/${s.ntfy_topic}`} target="_blank" rel="noreferrer">Open topic</a>
            <button className="btn small" onClick={() => run(api.testNotify, "Test notification sent. Check your phone.")} disabled={dirty} title={dirty ? "Save first" : ""}>
              Send test
            </button>
          </div>
        </Field>
        <Num label="Max alerts per scan" {...bind("max_alerts_per_scan")} />
        <Text label="ntfy token (optional)" hint="Only needed for e-mail forwarding" {...bind("ntfy_token")} />
        <Text label="Forward to e-mail (optional)" {...bind("notify_email")} />
      </Section>

      <Section title="Buyer" hint="Only used to fill in the draft messages. Stored on this machine, never sent anywhere by the agent.">
        <Text label="Name" {...bind("buyer.name")} />
        <Text label="Phone" {...bind("buyer.phone")} />
        <span />
        <Text label="When you can view (German)" {...bind("buyer.availability_de")} />
        <Text label="When you can view (English)" {...bind("buyer.availability_en")} />
      </Section>

      <div className="savebar">
        {msg && <span className={msg.ok ? "ok" : "bad"}>{msg.text}</span>}
        <span className="grow" />
        <button className="btn ghost small" onClick={resetAll}>
          {confirmReset ? "Click again to reset search to defaults" : "Reset to defaults"}
        </button>
        <button className="btn ghost" onClick={() => setS(saved)} disabled={!dirty || busy}>Discard</button>
        <button className="btn primary" onClick={save} disabled={!dirty || busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function Section({ title, hint, children }) {
  return (
    <section className="panel">
      <h2>{title}</h2>
      {hint && <p className="muted small">{hint}</p>}
      <div className="grid">{children}</div>
    </section>
  );
}

function Field({ label, hint, children, full }) {
  return (
    <label className={"field" + (full ? " full" : "")}>
      <span className="lbl">{label}</span>
      {children}
      {hint && <span className="muted small">{hint}</span>}
    </label>
  );
}

// A labelled group of controls. A <div>, not a <label>: nested labels
// would send every click to the first checkbox.
function Group({ label, children }) {
  return (
    <div className="field full">
      <span className="lbl">{label}</span>
      {children}
    </div>
  );
}

function Num({ label, hint, value, onChange, step }) {
  return (
    <Field label={label} hint={hint}>
      <input type="number" step={step || "1"} value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
    </Field>
  );
}

function Text({ label, hint, value, onChange }) {
  return (
    <Field label={label} hint={hint}>
      <input type="text" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

function Check({ label, value, onChange }) {
  return (
    <label className="check">
      <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

// Lists are edited as text so trailing blank lines survive typing; the server
// drops empty entries on save.
function Lines({ label, value, onChange, rows = 6 }) {
  return (
    <Field label={label} full>
      <textarea rows={rows} value={(value || []).join("\n")} onChange={(e) => onChange(e.target.value.split("\n"))} />
    </Field>
  );
}

function SearchTable({ value, onChange }) {
  const rows = value || [];
  const set = (i, key, v) => onChange(rows.map((r, j) => (j === i ? { ...r, [key]: v } : r)));
  return (
    <div className="table-edit">
      <div className="lbl">Searches ({rows.length})</div>
      <div className="tbl three">
        <div className="tr th"><span>Label</span><span>Make</span><span>Model</span><span /></div>
        {rows.map((r, i) => (
          <div className="tr" key={i}>
            <input value={r.label} placeholder="e.g. Golf" onChange={(e) => set(i, "label", e.target.value)} />
            <input value={r.make} placeholder="any" onChange={(e) => set(i, "make", e.target.value)} />
            <input value={r.model} placeholder="any" onChange={(e) => set(i, "model", e.target.value)} />
            <button className="x" title="Remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>×</button>
          </div>
        ))}
      </div>
      <button className="btn small" onClick={() => onChange([...rows, { label: "", make: "", model: "" }])}>+ Add search</button>
    </div>
  );
}
