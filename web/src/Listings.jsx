import React, { useEffect, useState } from "react";
import { api, ago } from "./api.js";

const VERDICTS = [
  ["HIT,MAYBE", "Matches"],
  ["HIT", "Hits"],
  ["MAYBE", "Maybe"],
  ["REJECT", "Rejected"],
  ["GONE", "Gone"],
  ["", "All"],
];
const STATUSES = [
  ["open", "Open"],
  ["contacted", "Contacted"],
  ["dismissed", "Dismissed"],
  ["", "Any status"],
];
const SORTS = [
  ["score", "Best score"],
  ["new", "Newest"],
  ["price", "Cheapest"],
  ["km", "Lowest km"],
  ["year", "Newest car"],
  ["distance", "Closest"],
];
const LABELS = {
  top: ["top price", "good"],
  good: ["good price", "good"],
  fair: ["fair price", ""],
  high: ["above market", "bad"],
  very_high: ["well above market", "bad"],
  too_low: ["suspiciously cheap", "bad"],
};
const SOURCES = { as24: "AutoScout24", ka: "Kleinanzeigen", mde: "mobile.de" };
const PAGE = 50;

const euro = (n) => (n == null ? "?" : n.toLocaleString("de-DE") + " €");
const kmFmt = (n) => (n == null ? "? km" : n.toLocaleString("de-DE") + " km");

export default function Listings({ onChange }) {
  const [verdict, setVerdict] = useState("HIT,MAYBE");
  const [status, setStatus] = useState("open");
  const [sort, setSort] = useState("score");
  const [q, setQ] = useState("");
  const [dupes, setDupes] = useState(false);
  const [qDebounced, setQDebounced] = useState("");
  const [data, setData] = useState({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = setTimeout(() => setQDebounced(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  function load(offset = 0) {
    setLoading(true);
    api
      .listings({ verdict, status, sort, q: qDebounced, dupes: dupes ? 1 : 0, limit: PAGE, offset })
      .then((d) => setData((prev) => (offset ? { ...d, items: [...prev.items, ...d.items] } : d)))
      .finally(() => setLoading(false));
  }

  useEffect(() => load(0), [verdict, status, sort, qDebounced, dupes]);

  async function mark(uid, s) {
    await api.setStatus(uid, s);
    setData((d) => ({
      total: d.total,
      items: d.items
        .map((x) => (x.uid === uid ? { ...x, status: s } : x))
        .filter((x) => !status || (status === "open" ? !x.status : x.status === status)),
    }));
    onChange?.();
  }

  return (
    <div>
      <div className="filters">
        <div className="seg">
          {VERDICTS.map(([v, label]) => (
            <button key={label} className={verdict === v ? "on" : ""} onClick={() => setVerdict(v)}>
              {label}
            </button>
          ))}
        </div>
        <div className="row">
          <input
            className="search"
            placeholder="Search model, town, postcode, equipment…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map(([v, l]) => (
              <option key={l} value={v}>{l}</option>
            ))}
          </select>
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS.map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
          <label className="check small" title="The same car posted again, at another branch or on another portal">
            <input type="checkbox" checked={dupes} onChange={(e) => setDupes(e.target.checked)} />
            Duplicates
          </label>
        </div>
      </div>

      <p className="muted small">{loading ? "Loading…" : `${data.total} listing(s)`}</p>

      {!loading && data.items.length === 0 && (
        <div className="empty">
          Nothing here yet. Scans run in the background; matches show up here and on your phone.
        </div>
      )}

      <div className="cards">
        {data.items.map((x) => (
          <Card key={x.uid} x={x} onMark={mark} />
        ))}
      </div>

      {data.items.length < data.total && (
        <button className="btn wide" onClick={() => load(data.items.length)} disabled={loading}>
          Load more
        </button>
      )}
    </div>
  );
}

function Card({ x, onMark }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [lang, setLang] = useState("de");
  const [copied, setCopied] = useState(false);

  function toggle() {
    if (!open && !detail) api.listing(x.uid).then(setDetail);
    setOpen(!open);
  }

  function copy() {
    const text = lang === "de" ? detail.draft_de : detail.draft_en;
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  const where = [x.zip, x.city].filter(Boolean).join(" ");
  const meta = [
    x.reg || x.year,
    kmFmt(x.km),
    x.fuel,
    x.gearbox,
    x.power_kw && `${x.power_kw} kW (${Math.round(x.power_kw * 1.36)} PS)`,
    x.distance_km != null ? `${x.distance_km} km away${where ? ` · ${where}` : ""}` : where,
  ].filter(Boolean);

  const label = LABELS[x.price_label];
  const facts = [
    label && <span key="l" className={"chip " + label[1]}>{label[0]}</span>,
    x.market_median && (x.market_n >= 5 || x.market_n === -1) && (
      <span
        key="m"
        className="chip"
        title={x.market_n === -1 ? "AutoScout24's market price for this car" : `median of ${x.market_n} similar listings`}
      >
        market ~{euro(x.market_median)}
      </span>
    ),
    x.accident === 1 && <span key="a" className="chip bad">accident damage</span>,
    x.accident === 0 && <span key="a0" className="chip good">no accident</span>,
    x.service_history === 1 && <span key="s" className="chip good">service book</span>,
    x.owners != null && <span key="o" className="chip">{x.owners} owner{x.owners === 1 ? "" : "s"}</span>,
    x.hu && <span key="h" className="chip">HU {x.hu}</span>,
    x.seller && <span key="se" className="chip">{x.seller}</span>,
    x.twin_of && <span key="t" className="chip">duplicate of a {SOURCES[x.twin_of.split(":")[0]] || ""} ad</span>,
  ].filter(Boolean);

  return (
    <article className={`card v-${x.verdict} ${x.status ? "s-" + x.status : ""}`}>
      <div className="card-head">
        <span className={`score v-${x.verdict}`} title={x.verdict}>{x.score}</span>
        <div className="grow">
          <a href={x.url} target="_blank" rel="noreferrer" className="title">
            {x.title || "(no title)"}
          </a>
          <div className="meta">
            <b className="price">{euro(x.price)}</b> · {meta.join(" · ")}
          </div>
          {facts.length > 0 && <div className="chips">{facts}</div>}
        </div>
      </div>
      {x.reasons && <p className="reasons">{x.reasons}</p>}
      {x.scam_risk >= 30 && <p className="warn">⚠ Scam risk {x.scam_risk}: {x.scam_flags}</p>}
      <div className="card-foot">
        <span className="muted small">
          {x.source}
          {x.search ? ` · ${x.search}` : ""} · seen {ago(x.first_seen)}
          {!x.checked && x.source !== "mobile.de" && " · ad page not read"}
          {x.status && <span className={"tag " + x.status}>{x.status}</span>}
        </span>
        <div className="actions">
          <button className="btn small" onClick={toggle}>{open ? "Hide message" : "Draft message"}</button>
          {x.status !== "contacted" && (
            <button className="btn small" onClick={() => onMark(x.uid, "contacted")}>Contacted</button>
          )}
          {x.status !== "dismissed" && (
            <button className="btn small ghost" onClick={() => onMark(x.uid, "dismissed")}>Dismiss</button>
          )}
          {x.status && (
            <button className="btn small ghost" onClick={() => onMark(x.uid, "")}>Reopen</button>
          )}
        </div>
      </div>
      {open && (
        <div className="draft">
          {!detail ? (
            <p className="muted">Loading…</p>
          ) : (
            <>
              <div className="draft-bar">
                <div className="seg small">
                  <button className={lang === "de" ? "on" : ""} onClick={() => setLang("de")}>Deutsch</button>
                  <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>English</button>
                </div>
                <button className="btn small" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
              </div>
              <pre>{lang === "de" ? detail.draft_de : detail.draft_en}</pre>
              <p className="muted small">
                Not sent. Read it, adjust it, then send it from the listing page yourself. Your name, phone and
                viewing times come from Settings → Buyer.
              </p>
            </>
          )}
        </div>
      )}
    </article>
  );
}
