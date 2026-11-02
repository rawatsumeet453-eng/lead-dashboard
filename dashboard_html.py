"""HTML renderer for campaign_dashboard.py — one self-contained file, no internet needed."""
from __future__ import annotations

import html
import json

from lead_quality import fmt_inr

E = lambda x: html.escape("" if x is None else str(x))  # noqa: E731

VERDICT_STYLE = {  # status palette + icon + label (never colour alone)
    "SCALE": ("good", "▲"), "OPTIMIZE": ("warn", "◆"), "REVIEW ADS + AUDIENCE": ("serious", "●"),
    "PAUSE / FIX": ("critical", "■"), "LOW DATA": ("muted", "○"),
}
OWNER_STYLE = {"Sales": "serious", "Campaign": "critical", "Healthy": "good"}
DECISION_STYLE = {"KEEP — BEST": ("good", "▲"), "KEEP": ("good", "✓"), "WATCH": ("warn", "◆"), "REMOVE": ("critical", "■"),
                  "LOW DATA": ("muted", "○"), "IGNORE": ("muted", "–")}
PRIORITY_STYLE = {"P1": ("critical", "■"), "P2": ("warn", "◆"), "P3": ("muted", "○")}
OPT_STYLE = {"SCALE": ("good", "▲"), "MAKE MORE": ("good", "▲"), "FIX SALES": ("serious", "☎"), "CLEAN FUNNEL": ("warn", "◆"),
             "OPTIMISE": ("warn", "◆"), "FIX ADS + FORM": ("critical", "■"), "FIX TRACKING FIRST": ("critical", "■"), "PAUSE": ("critical", "■"),
             "REFRESH": ("serious", "↻"), "EXCLUDE (test)": ("critical", "▼"), "RE-CHECK": ("warn", "◆"), "LEARNING — HOLD": ("muted", "⏳"),
             "LOW DATA": ("muted", "○"), "NO LEAD FORM": ("muted", "○"), "PASS": ("good", "✓"), "FAIL": ("critical", "✗"), "N/A": ("muted", "–")}
FATIGUE_STYLE = {"Fatigued — refresh": ("critical", "■"), "Watch": ("warn", "◆"), "Healthy": ("good", "▲"), "Not delivering": ("muted", "–")}


def pill(text, kind="muted", icon=""):
    return f'<span class="pill {kind}">{E(icon)} {E(text)}</span>'


MOVE_STYLE = {"SCALE ↑": ("good", "▲"), "HOLD — PUSH VISITS": ("warn", "◆"), "OPTIMIZE": ("warn", "◆"), "TEST — LOW DATA": ("muted", "○"),
              "FIX OR CUT ↓": ("critical", "▼"), "CUT": ("critical", "■"), "MAKE MORE LIKE THIS": ("good", "▲"), "REFRESH (fatigued)": ("serious", "↻"),
              "PAUSE": ("critical", "■"), "KEEP — BEST": ("good", "▲"), "WATCH": ("warn", "◆"), "REMOVE": ("critical", "■"),
              "NO LEAD FORM": ("muted", "○")}


def move_pill(m):
    k, i = MOVE_STYLE.get(m, ("muted", "○"))
    return pill(m, k, i)


def verdict_pill(v):
    k, i = VERDICT_STYLE.get(v, ("muted", "○"))
    return pill(v, k, i)


def owner_pill(o):
    k = "warn"
    for key, val in OWNER_STYLE.items():
        if o.startswith(key):
            k = val
    if o.startswith("Both"):
        k = "serious"
    return pill(("Problem: " + o) if o != "Healthy" else "No major problem", k, "⚑" if o != "Healthy" else "✓")


def opt_pill(v):
    k, i = OPT_STYLE.get(v, ("muted", "○"))
    return pill(v, k, i)


def optimisation_html(r):
    o = r.get("optimisation")
    if not o:
        try:
            from optimizer import optimise
            o = optimise(r)
        except Exception as e:  # noqa: BLE001
            o = {"error": f"{type(e).__name__}: {e}"}
    if o.get("error"):
        return f'<section><p class="muted">Optimisation could not run: {E(o["error"])}</p></section>'
    pr = lambda x: pill(x, *PRIORITY_STYLE.get(x, ("muted", "○")))  # noqa: E731
    gate_rows = [[f'<b>{E(g["rule"])}</b>', E(g["name"]), opt_pill(g["status"]), E(g["detail"])] for g in o["gates"]]
    act_rows = [[str(i), pr(a["priority"]), f'<b>{E(a["owner"])}</b>', f'<b>{E(a["action"])}</b>', E(a["why"]), E(a["rule"])]
                for i, a in enumerate(o["actions"], 1)]
    camp_rows = [[f'<b>{E(c["campaign"])}</b>' + ('<div class="tiny">merge (R15)</div>' if c.get("consolidate") else ""),
                  num(c["leads"]), num(c["leads_14d"]), pc(c["strict_quality_pct"]), pc(c["visit_ratio_pct"]), fmt_inr(c["cost_per_quality"]),
                  opt_pill(c["verdict"]) + f'<div class="tiny">{E(c["rule"])}</div>', f'<b>{E(c["owner"])}</b>', E(c["action"]) + f'<div class="tiny">{E(c["why"])}</div>']
                 for c in o["campaigns"]]
    ad_rows = [[f'<b>{E(a["ad"])}</b><div class="tiny">{E(a["campaign"])}</div>', E(a["angle"]), num(a["leads"]), fmt_inr(a["spend"]),
                pc(a["strict_quality_pct"]), opt_pill(a["verdict"]) + f'<div class="tiny">{E(a["rule"])}</div>', E(a["action"]) + f'<div class="tiny">{E(a["why"])}</div>']
               for a in o["ads"]]
    pl_rows = [[f'<b>{E(p["placement"])}</b>', pc(p["share_spend_pct"]), fmt_inr(p["est_cost_per_quality"]), opt_pill(p["verdict"]), E(p["why"])] for p in o["placements"]]
    rule_rows = [[f'<b>{E(x["rule"])}</b>', E(x["when"]), opt_pill(x["verdict"])] for x in o["rules"]]
    owners = {}
    for a in o["actions"]:
        owners[a["owner"]] = owners.get(a["owner"], 0) + 1
    return f"""
<section class="band"><h2>🤖 Optimisation verdict — {opt_pill(o['verdict'])}</h2><p>{E(o['verdict_why'])}</p>
<p class="tiny">17 rules, all on <b>strict quality</b> (status quality minus leads whose feedback says otherwise). {E(o.get('days') or '—')} days of data.</p>
<div class="kpis">{''.join(card(k, str(n), "actions") for k, n in owners.items())}</div></section>
<section><h2>✅ This week — do these, in this order</h2>
{table(["#", "Priority", "Owner", "Action", "Why (from the data)", "Rule"], act_rows) if act_rows else '<p class="muted">No action triggered — keep running.</p>'}</section>
<section><h2>🚦 Health checks (account)</h2>{table(["Rule", "Check", "Status", "Detail"], gate_rows)}</section>
<section><h2>📣 Campaign verdicts</h2>{table(["Campaign", "Leads", "Leads (14d)", "Strict Q %", "Visit %", "Cost / quality", "Verdict", "Owner", "Do this"], camp_rows)}</section>
<section><h2>🎬 Ads to pause / refresh / copy</h2>{table(["Ad", "Angle", "Leads", "Spend", "Strict Q %", "Verdict", "Do this"], ad_rows) if ad_rows else '<p class="muted">No ad needs action yet (or no spend data).</p>'}</section>
<section><h2>📍 Placements</h2>{table(["Placement", "Spend share", "Est. cost / quality", "Verdict", "Why"], pl_rows) if pl_rows else '<p class="muted">No placement over the limit (or no placement data).</p>'}</section>
<section><h2>📖 The 17 rules</h2>{table(["Rule", "When", "Verdict"], rule_rows, "small")}
<p class="tiny">Budget changes max 20% at a time · judge on 7 days · don't edit campaigns in their first 3 days · judge on cost per quality lead and cost per visit, not CPL.</p></section>"""


def fatigue_pill(st):
    k, i = FATIGUE_STYLE.get(st, ("muted", "○"))
    return pill(st, k, i)


def tgt(value, target, label):
    if value is None:
        return '<span class="pill muted">—</span>'
    ok = value >= target
    return pill(f"{label} {value}% {'≥' if ok else '<'} {target}%", "good" if ok else "critical", "✓" if ok else "✗")


def num(x, d=0):
    if x is None:
        return "—"
    return f"{x:,.{d}f}"


def pc(x):
    return "—" if x is None else f"{x}%"


def bar(p, maxv=100, kind="series"):
    w = 0 if not p or not maxv else max(2, min(100, p * 100 / maxv))
    return f'<div class="bar"><span class="{kind}" style="width:{w:.1f}%"></span></div>'


def delta(new, old, good_up=True, neutral=False):
    if new is None or old in (None, 0):
        return '<span class="d muted">—</span>'
    ch = (new - old) / old * 100
    if abs(ch) < 0.5:
        return '<span class="d muted">● 0%</span>'
    up = ch > 0
    cls = "muted" if neutral else ("goodtxt" if up == good_up else "badtxt")
    return f'<span class="d {cls}">{"▲" if up else "▼"} {abs(ch):.0f}%</span>'


def table(headers, rows, cls=""):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) or \
        f'<tr><td colspan="{len(headers)}" class="muted">No data</td></tr>'
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def card(label, value, sub=""):
    return f'<div class="kpi"><div class="kl">{E(label)}</div><div class="kv">{value}</div><div class="ks">{sub}</div></div>'


def ul(items):
    items = [i for i in items if i]
    return "<ul>" + "".join(f"<li>{E(i)}</li>" for i in items) + "</ul>" if items else '<p class="muted">—</p>'


def funnel(j):
    steps = j["steps"]
    top = steps[0]["leads"] or 1
    brk = j.get("break")
    shades = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab"]
    out = []
    for i, s in enumerate(steps):
        is_break = brk and s["stage"] == brk["to"]
        w = max(1.5, s["leads"] * 100 / top) if s["leads"] else 0
        conv = "" if s["step_conv_pct"] is None else f'{s["step_conv_pct"]}% of previous'
        bm = "" if not s.get("benchmark_pct") else f' · benchmark {s["benchmark_pct"]}%'
        tip = f'{s["stage"]}: {s["leads"]} leads ({s["pct_of_leads"]}% of all){(" — " + conv) if conv else ""}{bm}'
        lost = s.get("lost") or []
        if lost:
            chips = "".join(f'<span class="lost">−{x["leads"]} {E(x["reason"])}</span>' for x in lost[:5])
            out.append(f'<div class="frl"><div></div><div class="lostrow">{chips}</div></div>')
        out.append(
            f'<div class="fr{" brk" if is_break else ""}" title="{E(tip)}"><div class="fl">{E(s["stage"])}</div>'
            f'<div class="ft"><span style="width:{w:.1f}%;background:{shades[min(i, 6)]}"></span></div>'
            f'<div class="fv"><b>{s["leads"]}</b> <span class="muted">{s["pct_of_leads"]}%</span></div>'
            f'<div class="fc">{E(conv)}{" ⚠ BREAK" if is_break else ""}</div></div>')
    note = ""
    if brk:
        note = (f'<div class="callout"><b>Journey breaks here:</b> {E(brk["from"])} → {E(brk["to"])} — only {brk["conv_pct"]}% moved on '
                f'(benchmark {brk["benchmark_pct"]}%), {brk["lost"]} leads lost. <b>Owner: {E(brk["owner"])}</b>.</div>')
    return '<div class="funnel">' + "".join(out) + "</div>" + note


def daily_chart(daily):
    if not daily:
        return ""
    mx = max([d["leads"] for d in daily] + [1])
    cols = []
    for d in daily:
        h1, h2 = d["leads"] * 100 / mx, d["quality"] * 100 / mx
        tip = f'{d["date"]}: {d["leads"]} leads, {d["quality"]} quality' + (f', spend {fmt_inr(d["spend"])}' if d.get("spend") is not None else "")
        cols.append(f'<div class="dc" title="{E(tip)}"><div class="dbars"><span class="b1" style="height:{h1:.0f}%"></span>'
                    f'<span class="b2" style="height:{h2:.0f}%"></span></div><div class="dl">{E(d["date"][:6])}</div></div>')
    return ('<div class="legend"><span><i class="sw b1"></i>All leads</span><span><i class="sw b2"></i>Quality leads</span>'
            f'<span class="muted">max {mx} per bar · hover a bar for numbers</span></div><div class="daily">' + "".join(cols) + "</div>")


def lead_table(rows, show_feedback=True):
    hdr = ["Date", "Days old", "Name", "Campaign", "Ad", "Platform", "Latest status", "Counted as", "Why"]
    if show_feedback:
        hdr.append("Feedback check")
    out = []
    for r in rows:
        cells = [E(r["date"]), num(r["age_days"]), E(r["name"]), E(r["campaign"]), E(r["ad"]), E(r["platform"]), E(r["latest"]),
                 E(r["stage"]), E(r["proof"])]
        if show_feedback:
            cells.append(pill(r["doubt"], "warn", "⚠") if r["doubt"] else "")
        out.append(cells)
    return table(hdr, out, "small")


# ======================================================================================
def period_cards(Lw, Pw, th, client=False):
    """KPI cards for one period vs the previous one. Used by Summary AND the Client Report, so both always match."""
    if Pw is None or Pw.get("partial"):   # previous window not fully covered by the data -> no % change
        Pw = {k: None for k in Lw}
    pend = f'{Lw["pending_calls"]}/{Lw["leads"]} still being called' if Lw.get("leads") else ""
    money = "".join([
        card("Spend", fmt_inr(Lw["spend"]), delta(Lw["spend"], Pw["spend"], neutral=True)),
        card("Leads (CRM)", num(Lw["leads"]), delta(Lw["leads"], Pw["leads"])),
        card("CPL", fmt_inr(Lw["cpl"]), delta(Lw["cpl"], Pw["cpl"], good_up=False)),
        card("Quality leads", num(Lw["quality"]), delta(Lw["quality"], Pw["quality"])),
        card("Quality %", pc(Lw["quality_pct"]), delta(Lw["quality_pct"], Pw["quality_pct"]) + (f'<div class="tiny">{E(pend)}</div>' if pend else "")),
        card("Cost / Quality lead", fmt_inr(Lw["cost_per_quality"]), delta(Lw["cost_per_quality"], Pw["cost_per_quality"], good_up=False)),
    ])
    visits = "".join([
        card("Will Visit (planned)", num(Lw["will_visit"]), delta(Lw["will_visit"], Pw["will_visit"])),
        card("Visited (incl. booked)", num(Lw["site_visits"]), delta(Lw["site_visits"], Pw["site_visits"])),
        card("Booked", num(Lw["booked"]), delta(Lw["booked"], Pw["booked"])),
        card("Visit %", pc(Lw["visit_ratio_pct"]), delta(Lw["visit_ratio_pct"], Pw["visit_ratio_pct"]) + f'<div class="tiny">target {th["target_visit_ratio_pct"]}%</div>'),
        card("Cost / Visit", fmt_inr(Lw["cost_per_visit"]), delta(Lw["cost_per_visit"], Pw["cost_per_visit"], good_up=False)),
        (card("Cost / Booking", fmt_inr(Lw["cost_per_booking"]), delta(Lw["cost_per_booking"], Pw["cost_per_booking"], good_up=False)) if client else
         card("Strict quality %", pc(Lw["strict_quality_pct"]), delta(Lw["strict_quality_pct"], Pw["strict_quality_pct"]) + '<div class="tiny">without doubtful</div>')),
    ])
    ld = Lw["leads"] or 0
    f = lambda v: f'{v} ({pc(round(100 * v / ld, 1)) if ld else "—"})'  # noqa: E731
    strip = (f'<p class="fstrip"><b>Lead funnel:</b> Leads {ld} → Quality {f(Lw["quality"])} → Will Visit+ {f(Lw["will_visit_plus"])} '
             f'→ Visited {f(Lw["site_visits"])} → Booked {f(Lw["booked"])}</p>')
    return money, visits, strip


def account_period(acc):
    """The account totals in the same shape as a compare window (no previous period)."""
    a = dict(acc)
    a.setdefault("pending_calls", acc.get("not_called", 0) + acc.get("followup", 0))
    return a


def client_html(r):
    """Client-safe report: same numbers as Summary (same objects), no lead names / phones / internal flags."""
    acc, th, ins = r["account"], r["rules"]["thresholds"], (r.get("insights") or {})
    sel = r.get("selected")
    last = sel["last"] if sel else account_period(acc)
    prev = sel["prev"] if sel and not sel.get("is_month") else None     # one calendar month -> no other month shown
    money, visits, strip = period_cards(last, prev, th, client=True)
    has_spend = bool(acc.get("spend"))
    rng = f'{r["period"]["from"]} – {r["period"]["to"]}'
    cmp_line = (f'<span class="cmpnote"> · compared with previous {sel["days"]} days ({E(sel["prev"]["from"])} – {E(sel["prev"]["to"])})</span>'
                if sel and not sel.get("is_month") and not sel["prev"].get("partial") else "")
    camps = [c for c in r["campaigns"] if c["leads"] or (c.get("spend") or 0) > 0]
    c_rows = [[f'<b>{E(c["campaign"] if c["campaign"] != "(no campaign name)" else "Other / not tagged")}</b>', fmt_inr(c["spend"]), num(c["leads"]),
               fmt_inr(c["cpl"]), num(c["quality"]), pc(c["quality_pct"]), fmt_inr(c["cost_per_quality"]), num(c["will_visit"]), num(c["site_visits"]),
               num(c["booked"])] for c in camps]
    named = [a for a in r["ads"] if a["ad"] != "(no ad name)" and a["leads"]]
    top_ads = sorted(named, key=lambda a: (-a["quality"], -a["leads"]))[:8]
    multi = len({a["campaign"] for a in top_ads}) > 1
    a_rows = [[f'<b>{E(a["ad"])}</b>' + (f'<div class="tiny">{E(a["campaign"])}</div>' if multi else ""), E(a.get("format", "")), fmt_inr(a.get("spend")), num(a["leads"]), fmt_inr(a.get("cpl")), num(a["quality"]),
               pc(a["quality_pct"]), fmt_inr(a.get("cost_per_quality")), num(a["site_visits"])] for a in top_ads]
    call = "".join([
        card("Leads called", num(acc["called"]), f'of {acc["leads"]}'),
        card("Ringing / busy", num(acc["followup"]), "being called again"),
        card("Not reached %", pc(acc["not_reached_pct"]), "of leads called"),
        card("Not called yet", num(acc["not_called"])),
        card("Interested / details shared", num(acc["details_callback"] + acc["interested"]), "being followed up for a visit"),
        card("Not relevant (dead)", num(acc["dead"])),
    ])
    dead_rows = [[E(k), num(v), pc(round(100 * v / acc["leads"], 1) if acc["leads"] else 0)] for k, v in list(acc["dead_reasons"].items())[:6]]
    # plain-English summary for a developer client (agent can override with insights.client_summary / client_next_steps)
    summ = ins.get("client_summary") or []
    if not summ:
        summ.append(f'{acc["leads"]} leads' + (f' at {fmt_inr(acc["cpl"])} per lead ({fmt_inr(acc["spend"])} spent).' if has_spend else '.'))
        summ.append(f'{acc["quality"]} quality leads ({acc["quality_pct"]}%) — people who shared details, asked for a call back, showed interest, '
                    f'planned or made a site visit. Target {th["scale_quality_pct"]}%.' + (f' Cost per quality lead {fmt_inr(acc["cost_per_quality"])}.' if has_spend else ''))
        summ.append(f'{acc["will_visit"]} site visits planned, {acc["site_visits"]} visited' + (f', {acc["booked"]} booked.' if acc["booked"] else '.')
                    + f' Visit ratio {acc["visit_ratio_pct"]}% (target {th["target_visit_ratio_pct"]}%).')
        if camps:
            b = max(camps, key=lambda c: (c["quality"], -(c["cost_per_quality"] or 0)))
            summ.append(f'Best campaign: {b["campaign"]} — {b["quality"]} quality leads ({b["quality_pct"]}%).')
        if top_ads:
            summ.append(f'Best ad: {top_ads[0]["ad"]} — {top_ads[0]["quality"]} quality of {top_ads[0]["leads"]} leads.')
    steps = ins.get("client_next_steps") or []
    if not steps:
        if acc["will_visit"]:
            steps.append(f'Confirm the {acc["will_visit"]} planned site visits with a reminder the day before and on the morning of the visit.')
        if acc["details_callback"] + acc["interested"]:
            steps.append(f'Follow up the {acc["details_callback"] + acc["interested"]} interested / details-shared leads with fixed site-visit slots.')
        if acc["quality_pct"] < th["scale_quality_pct"]:
            steps.append('Refresh the ad creatives and add qualifying questions (budget, possession timeline) to the lead form to raise lead quality.')
        elif acc["visit_ratio_pct"] >= th["target_visit_ratio_pct"]:
            steps.append('Increase budget gradually (15–20% at a time) on the best campaign while quality stays above target.')
        else:
            steps.append('Keep budget steady until more of the quality leads convert into site visits.')
        if acc["not_reached_pct"] >= 25:
            steps.append('Call every new lead within 5 minutes and retry unanswered leads at different times of the day.')
    return f"""
<section class="band client-head"><div class="ch"><h2>🏢 {E(r['client'].replace("-", " ").title() if r['client'].islower() else r['client'])} — Meta Ads lead report</h2>
<button class="btn noprint" onclick="clientPdf()">⬇ Download client PDF</button></div>
<p class="muted">Period: <b>{E(rng)}</b>{cmp_line} · Generated {E(r['generated_at'])}</p>
<div class="kpis">{money}</div><h3>🏠 Site visits & bookings</h3><div class="kpis">{visits}</div>{strip}</section>
<section><h2>Summary</h2>{ul(summ)}</section>
<section><h2>Campaign performance</h2>{table(["Campaign", "Spend", "Leads", "CPL", "Quality leads", "Quality %", "Cost / quality lead", "Will visit", "Visited", "Booked"], c_rows)}</section>
<section><h2>Top ads</h2>{table(["Ad", "Format", "Spend", "Leads", "CPL", "Quality leads", "Quality %", "Cost / quality lead", "Visited"], a_rows) if a_rows else '<p class="muted">Ad names not available in this data.</p>'}</section>
<section class="two"><div><h2>Calling status</h2><div class="kpis k3">{call}</div></div>
<div><h2>Why leads were not relevant</h2>{table(["Reason", "Leads", "% of all leads"], dead_rows) if dead_rows else '<p class="muted">No dead leads.</p>'}</div></section>
<section><h2>Next steps</h2>{ul(steps)}</section>
<p class="tiny">Quality leads = Details shared, Call back, Interested, Will visit, Visited, Booked. Quality % and Visit % are of all leads. Duplicate and test leads are removed.</p>"""


def reconcile_html(r):
    rows = [[("✅" if x["ok"] else "❌"), E(x["name"]), E(x["a"]), E(x["b"])] for x in (r.get("reconcile") or [])]
    if not rows:
        return ""
    bad = sum(1 for x in r["reconcile"] if not x["ok"])
    head = ("✅ All tabs use the same numbers." if not bad else f"❌ {bad} check(s) do not match — tell Claude before sending this report.")
    return f'<section><h2>🔁 Accuracy check — every tab must match</h2><p>{head}</p>{table(["", "Check", "Value", "Should equal"], rows, "small")}</section>'


def render(r: dict, start_tab: str = "summary") -> str:
    acc, cmp, th = r["account"], r["compare"], r["rules"]["thresholds"]
    ins = r.get("insights") or {}
    N = r["rules"]["compare_days"]
    L, P = cmp["last"], cmp["prev"]

    # ---------------- SUMMARY
    def period_block(n, c, title=None, on=None):
        money, visits, strip = period_cards(c["last"], c["prev"], th)
        if c.get("full_month") and not title:
            title = f'📅 Full month ({E(c["last"]["from"])} – {E(c["last"]["to"])}) — same numbers as the rest of the report'
        title = title or (f'📅 Last {n} days ({E(c["last"]["from"])} – {E(c["last"]["to"])}) '
                          f'<span class="cmpnote">vs previous {n} days ({E(c["prev"]["from"])} – {E(c["prev"]["to"])})</span>')
        on = (n == str(N)) if on is None else on
        return (f'<div class="pblock{" on" if on else ""}" data-n="{n}"><h2>{title}</h2>'
                + ('<p class="tiny cmpnote">Previous period starts before your data — upload a longer date range to see the change %.</p>' if c["prev"].get("partial") and not c.get("full_month") else "")
                + f'<div class="kpis">{money}</div><h3>🏠 Visits</h3><div class="kpis">{visits}</div>{strip}</div>')
    sel = r.get("selected")
    if sel and not sel.get("is_month"):
        periods_html = period_block(str(sel["days"]), sel, on=True, title=(
            f'📅 Selected period: {E(sel["from"])} – {E(sel["to"])} ({sel["days"]} days) '
            f'<span class="cmpnote">vs previous {sel["days"]} days ({E(sel["prev"]["from"])} – {E(sel["prev"]["to"])})</span>'))
    else:
        comps = r.get("compares") or {str(N): cmp}
        pbtns = "".join(f'<button class="pbtn{" on" if k == str(N) else ""}" data-n="{k}" onclick="sp(\'{k}\')">{k} days</button>' for k in sorted(comps, key=int))
        periods_html = (f'<div class="pbar">Recent trend (within your data): {pbtns}</div>'
                        + "".join(period_block(k, v) for k, v in sorted(comps.items(), key=lambda x: int(x[0]))))
    kpis2 = "".join([
        card("Ringing / Busy", num(acc["followup"]), "call again — different time"),
        card("Not reached %", pc(acc["not_reached_pct"]), "of leads called"),
        card("Not called yet", num(acc["not_called"]), "call today"),
        card("Details / Call back / Interested", num(acc["details_callback"] + acc["interested"]), "push for a visit"),
        card("Will Visit (planned)", num(acc["will_visit"]), "confirm the slot"),
        card("Dead leads", num(acc["dead"]), (", ".join(f"{k} {v}" for k, v in list(acc["dead_reasons"].items())[:2]))),
    ])
    has_spend = bool(acc.get("spend"))
    auto = [
        (f'{acc["leads"]} leads at {fmt_inr(acc["cpl"])} per lead from {fmt_inr(acc["spend"])} spend ({r["period"]["from"]} – {r["period"]["to"]}).'
         if has_spend else f'{acc["leads"]} leads ({r["period"]["from"]} – {r["period"]["to"]}). Add the Meta Ads export to see spend, CPL and cost per quality lead.'),
        f'{acc["quality"]} quality leads = {acc["quality_pct"]}% of all leads (target {th["scale_quality_pct"]}%)'
        + (f', {fmt_inr(acc["cost_per_quality"])} per quality lead.' if has_spend else '.')
        + (f' Strict quality (without {acc["doubtful_quality"]} doubtful) = {acc["strict_quality"]} ({acc["strict_quality_pct"]}%).' if acc["doubtful_quality"] else ''),
        f'{acc["followup"] + acc["not_called"]} leads not connected yet ({acc["followup"]} ringing / busy, {acc["not_called"]} not called).'
        + (f' Top drop reason: {next(iter(acc["dead_reasons"]))}.' if acc["dead_reasons"] else ""),
    ]
    if r["campaigns"]:
        best = r["campaigns"][0]
        auto.append(f'Best campaign: {best["campaign"]} — {best["quality"]} quality leads ({best["quality_pct"]}%)' + (f' at {fmt_inr(best["cost_per_quality"])} each.' if has_spend else '.'))
    named_ads = [a for a in r["ads"] if a["ad"] != "(no ad name)"]
    if named_ads:
        ba = named_ads[0]
        auto.append(f'Best ad: {ba["ad"]} ({ba["angle"]}, {ba["format"]}) — {ba["quality"]} quality of {ba["leads"]} leads.')
    if acc["journey"]["break"]:
        b = acc["journey"]["break"]
        auto.append(f'Lead journey breaks at {b["from"]} → {b["to"]} ({b["conv_pct"]}% vs {b["benchmark_pct"]}% benchmark) — {b["owner"]} side.')
    if acc["doubtful_quality"]:
        auto.append(f'{acc["doubtful_quality"]} "quality" leads look doubtful from the sales feedback (not genuine / going elsewhere) — see Lead List.')
    summary = ins.get("summary") or auto
    dg = acc["diagnosis"]
    problems = [("Sales", x) for x in dg["sales_issues"]] + [("Campaign", x) for x in dg["campaign_issues"]]
    problems.sort(key=lambda x: -x[1]["affected"])
    prob_rows = [[pill(o, "serious" if o == "Sales" else "critical", "⚑"), E(x["issue"]), E(x["fix"])] for o, x in problems[:6]]

    def row(label, key, fmtf, good_up=True, neutral=False, meaning=""):
        get = lambda d: (d.get(key) if key in d else (d.get("meta") or {}).get(key))  # noqa: E731
        return [E(label), fmtf(get(acc)), fmtf(get(L)), fmtf(get(P)), delta(get(L), get(P), good_up, neutral), E(meaning)]
    numbers = table(["Metric", "Full period", f"Last {N} days", f"Previous {N} days", "Change", "Meaning"], [
        row("Spend", "spend", fmt_inr, neutral=True, meaning="Money spent"),
        row("Impressions", "impr", num, neutral=True, meaning="Times the ad was shown"),
        row("CPM", "cpm", fmt_inr, good_up=False, meaning="Cost per 1,000 impressions — rising = audience tired / competition"),
        row("Link clicks", "clicks", num, meaning="People who clicked"),
        row("CTR (link) %", "ctr_pct", lambda x: pc(x), meaning="Low = weak hook / creative"),
        row("CPC", "cpc", fmt_inr, good_up=False, meaning="Cost per click"),
        row("Leads (CRM)", "leads", num, meaning="After removing duplicates and test leads"),
        row("Leads (Meta)", "meta_leads", num, meaning="What Meta reports — should match CRM"),
        row("CPL", "cpl", fmt_inr, good_up=False, meaning="Spend ÷ CRM leads"),
        row("Quality leads", "quality", num, meaning="Details / Call back / Interested / Will visit / Visited / Booked"),
        row("Quality %", "quality_pct", pc, meaning=f"Quality ÷ total leads — target {th['scale_quality_pct']}%+"),
        row("Cost / Quality lead", "cost_per_quality", fmt_inr, good_up=False, meaning="Spend ÷ quality leads"),
        row("Ringing / Busy", "followup", num, good_up=False, meaning="Called, not picked up"),
        row("Not called yet", "not_called", num, good_up=False, meaning="No status at all"),
        row("Dead leads", "dead", num, good_up=False, meaning="Not interested / wrong fit / junk"),
    ])
    camp_rows = []
    for i, c in enumerate(r["campaigns"], 1):
        camp_rows.append([str(i), f'<b>{E(c["campaign"])}</b>', fmt_inr(c["spend"]), num(c["leads"]), fmt_inr(c["cpl"]), num(c["quality"]),
                          pc(c["quality_pct"]), bar(c["quality_pct"]), fmt_inr(c["cost_per_quality"]), owner_pill(c["diagnosis"]["owner"]),
                          verdict_pill(c["verdict"])])
    summary_html = f"""
<section class="band">{periods_html}<h3>📞 Calling status (full period)</h3><div class="kpis alt">{kpis2}</div></section>
<section class="targets"><h2>🎯 Goal check — {th['scale_quality_pct']}% quality leads · {th['target_visit_ratio_pct']}% site visits</h2>
<div class="tg">{tgt(acc['quality_pct'], th['scale_quality_pct'], 'Quality')} {tgt(acc['strict_quality_pct'], th['scale_quality_pct'], 'Strict quality')} {tgt(acc['visit_ratio_pct'], th['target_visit_ratio_pct'], 'Visits')}
<span class="muted">Full period · {acc['quality']} quality ({acc['strict_quality']} strict, without doubtful) and {acc['site_visits']} visits from {acc['leads']} leads</span></div>
<div class="tw"><table class="small"><thead><tr><th>Campaign</th><th>Leads</th><th>Quality</th><th>Visits</th><th>Move</th></tr></thead><tbody>
{''.join(f"<tr><td><b>{E(g['campaign'])}</b></td><td>{g['leads']}</td><td>{tgt(g['quality_pct'], th['scale_quality_pct'], 'Q')}</td><td>{tgt(g['visit_ratio_pct'], th['target_visit_ratio_pct'], 'V')}</td><td>{move_pill(g['move'])}</td></tr>" for g in (r.get('growth') or {}).get('campaigns', []))}
</tbody></table></div></section>
<section><h2>🧠 In simple English</h2>{ul(summary)}</section>
<section><h2>⚑ What is going wrong — and who fixes it</h2>
{table(["Owner", "Problem", "Fix"], prob_rows) if prob_rows else '<p class="muted">No major problem found.</p>'}</section>
<section><h2>🏆 Campaigns — ranked by quality leads</h2>{table(["#", "Campaign", "Spend", "Leads", "CPL", "Quality", "Quality %", "Quality bar", "Cost / Quality", "Problem", "Verdict"], camp_rows)}</section>
<section class="two"><div><h2>🧭 Lead journey — where leads drop</h2>{funnel(acc['journey'])}</div>
<div><h2>📈 Leads vs quality leads over time</h2>{daily_chart(r['daily'])}</div></section>
<section><h2>📋 All numbers</h2>{numbers}</section>"""

    # ---------------- CAMPAIGNS
    cblocks = []
    ic = (ins.get("campaigns") or {})
    for c in r["campaigns"]:
        d, m = c["diagnosis"], c["meta"]
        note = ic.get(c["campaign"]) or {}
        good = note.get("good") or d["good"]
        bad = note.get("bad") or [("[Sales] " if x in d["sales_issues"] else "[Campaign] ") + x["issue"] for x in d["sales_issues"] + d["campaign_issues"]]
        sol = note.get("solution") or [x["fix"] for x in sorted(d["sales_issues"] + d["campaign_issues"], key=lambda x: -x["affected"])]
        owner = note.get("owner") or d["owner"]
        k = "".join([
            card("Total leads", num(c["leads"]), f'Meta: {num(m.get("meta_leads"))}'),
            card("Amount spent", fmt_inr(c["spend"])),
            card("Cost per lead", fmt_inr(c["cpl"])),
            card("Quality leads", num(c["quality"]), f'{c["doubtful_quality"]} doubtful' if c["doubtful_quality"] else ""),
            card("Quality %", pc(c["quality_pct"]), bar(c["quality_pct"])),
            card("Cost / Quality lead", fmt_inr(c["cost_per_quality"])),
            card("Visited", num(c["site_visits"]), f'{c["visit_ratio_pct"]}% of leads'),
            card("Cost per visit", fmt_inr(c["cost_per_visit"])),
        ])
        bests = []
        if c.get("best_ad"):
            b = c["best_ad"]
            bests.append(f'<b>Best ad:</b> {E(b["name"])} — {b["quality"]} quality of {b["leads"]} ({b["quality_pct"]}%)'
                         + (f', {fmt_inr(b["cost_per_quality"])} per quality lead' if b.get("cost_per_quality") else ""))
        if c.get("best_platform"):
            b = c["best_platform"]
            bests.append(f'<b>Best platform:</b> {E(b["name"])} — {b["quality"]} quality of {b["leads"]} ({b["quality_pct"]}%)')
        if c.get("best_placement"):
            b = c["best_placement"]
            bests.append(f'<b>Cheapest placement (Meta leads):</b> {E(b["name"])} — {b["meta_leads"]} leads at {fmt_inr(b["meta_cpl"])}')
        meta_line = (f'CTR {pc(m.get("ctr_pct"))} · CPM {fmt_inr(m.get("cpm"))} · CPC {fmt_inr(m.get("cpc"))} · '
                     f'Click → lead {pc(m.get("click_to_lead_pct"))} · Calls pending {c["calls_pending"]}')
        dead = ", ".join(f"{k2} ({v})" for k2, v in c["dead_reasons"].items()) or "—"
        cblocks.append(f"""
<section class="camp"><div class="ch"><h2>{E(c['campaign'])}</h2>{verdict_pill(c['verdict'])} {owner_pill(owner)}</div>
<p class="muted">{E(c['verdict_reason'])}</p><div class="kpis k8">{k}</div>
<div class="two"><div><h3>Lead journey</h3>{funnel(c['journey'])}</div>
<div><h3>Best performers</h3>{('<p>' + '</p><p>'.join(bests) + '</p>') if bests else '<p class="muted">Not enough quality leads yet.</p>'}
<h3>Meta health</h3><p>{meta_line}</p><h3>Dead reasons</h3><p>{E(dead)}</p></div></div>
<div class="three"><div class="gbox"><h3>✓ What is good</h3>{ul(good)}</div><div class="bbox"><h3>✗ What is bad</h3>{ul(bad)}</div>
<div class="sbox"><h3>➜ Solution</h3>{ul(sol)}</div></div></section>""")
    campaigns_html = "".join(cblocks) or '<p class="muted">No campaigns.</p>'

    # ---------------- ADS & ANGLES
    ad_rows = []
    for a in r["ads"]:
        f = a["fatigue"]
        ad_rows.append([f'<b>{E(a["ad"])}</b><div class="tiny">{E(a["campaign"])}</div>', E(a["angle"]), E(a["format"]), fmt_inr(a["spend"]),
                        num(a["leads"]), fmt_inr(a["cpl"]), num(a["quality"]), pc(a["quality_pct"]), bar(a["quality_pct"]),
                        num(a["site_visits"]), pc((a.get("meta") or {}).get("ctr_pct")), fatigue_pill(f["status"]) + f'<div class="tiny">{E(", ".join(f["signals"]))}</div>'])
    ang_rows = [[f'<b>{E(x["angle"])}</b>', num(x["ads"]), fmt_inr(x["spend"]), num(x["leads"]), num(x["quality"]), pc(x["quality_pct"]),
                 bar(x["quality_pct"]), fmt_inr(x["cost_per_quality"]), num(x["site_visits"])] for x in r["angles"]]
    fmt_rows = [[E(x["format"]), fmt_inr(x["spend"]), num(x["leads"]), num(x["quality"]), pc(x["quality_pct"]), fmt_inr(x["cost_per_quality"])] for x in r["formats"]]
    ads_html = f"""
<section><h2>🎯 Ad angles — which message brings quality</h2>
<p class="muted">Angle is read from the ad name (rename ads like "Price_Video_1" for best results, or set it in config.json → ad_angles).</p>
{table(["Angle", "Ads", "Spend", "Leads", "Quality", "Quality %", "", "Cost / Quality", "Visits"], ang_rows)}
{('<h3>Expert view on angles</h3>' + ul(ins.get('angles'))) if ins.get('angles') else ''}
{('<h3>Next creatives to make</h3>' + ul(ins.get('next_creatives'))) if ins.get('next_creatives') else ''}</section>
<section><h2>🎞 Video vs Image</h2>{table(["Format", "Spend", "Leads", "Quality", "Quality %", "Cost / Quality"], fmt_rows)}</section>
<section><h2>🧪 Every ad — quality + fatigue</h2>
<p class="muted">Fatigue = last 7 days vs previous 7 days (CTR drop, CPL rise, CPM rise, fewer new people reached). Needs ₹2,000+ spend and 3,000+ impressions in the last 7 days to judge.</p>
{table(["Ad", "Angle", "Format", "Spend", "Leads", "CPL", "Quality", "Quality %", "", "Visits", "CTR", "Fatigue"], ad_rows)}</section>"""

    # ---------------- PLACEMENT & PLATFORM
    def dpill(dv):
        k, i = DECISION_STYLE.get(dv, ("muted", "○"))
        return pill(dv, k, i)
    grp_rows = [[f'<b>{E(g["group"])}</b>', fmt_inr(g["spend"]), pc(g["share_spend_pct"]), num(g["clicks"]), pc(g["ctr_pct"]),
                 pc(g["click_to_lead_pct"]), num(g["meta_leads"]), num(g["est_quality"], 1), pc(g["est_quality_pct"]), bar(g["est_quality_pct"] or 0),
                 fmt_inr(g["est_cost_per_quality"])] for g in r.get("placement_groups", [])]
    pl_rows = [[f'<b>{E(p["placement"])}</b><div class="tiny">{E(p["platform"])}</div>', fmt_inr(p["spend"]), pc(p["share_spend_pct"]),
                num(p["clicks"]), pc(p["ctr_pct"]), fmt_inr(p["cpm"]), pc(p.get("click_to_lead_pct")), num(p["meta_leads"]), fmt_inr(p["meta_cpl"]),
                num(p.get("est_leads"), 1), num(p.get("est_quality"), 1), pc(p.get("est_quality_pct")), fmt_inr(p.get("est_cost_per_quality")),
                dpill(p.get("decision", "")) + f'<div class="tiny">{E(p.get("decision_why", ""))}</div>'] for p in r["placements"]]
    pf_rows = [[f'<b>{E(p["platform"] or "Unknown — add the Meta lead download")}</b>', num(p["leads"]), num(p["quality"]), pc(p["quality_pct"]), bar(p["quality_pct"]), num(p["site_visits"]),
                num(p["doubtful"]), fmt_inr(p["spend"]), fmt_inr(p["cost_per_quality"])] for p in r["platforms"]]
    acpq = acc.get("cost_per_quality")
    placement_html = f"""
<section class="band"><h2>🧭 How to read placement quality</h2>
<p>Meta never tells which placement a lead came from. So each ad's CRM leads and quality are <b>split across placements in the same ratio as
that ad's Meta leads</b> (e.g. Video 2 got 62% of its Meta leads from Reels → 62% of its CRM leads and quality are counted under Reels).
Totals always match the CRM. Treat it as a <b>strong estimate</b> — for an exact answer run a Reels-only vs Feed+Stories ad set test.</p>
<p class="tiny">Account cost per quality lead: {fmt_inr(acpq)} — placements are compared against this.</p></section>
<section><h2>📦 Reels vs Feed vs Stories</h2>
{table(["Group", "Spend", "Share", "Clicks", "CTR", "Click → Lead", "Meta leads", "Est. quality", "Est. quality %", "", "Est. cost / quality"], grp_rows)}</section>
<section><h2>📍 Every placement — traffic, leads, quality and decision</h2>
{table(["Placement", "Spend", "Share", "Clicks", "CTR", "CPM", "Click → Lead", "Meta leads", "Meta CPL", "Est. CRM leads", "Est. quality", "Est. quality %", "Est. cost / quality", "Decision"], pl_rows, "small")}
<p class="tiny">KEEP — BEST = 15%+ cheaper quality than account · WATCH = 1.3×+ account cost per quality · REMOVE = CPM 4×+ account / multi-submit / spend with 0 leads · IGNORE = under 3% of spend (leave to Meta) · Don't cut more than 1-2 placements at a time — fewer placements = higher CPM.</p></section>
<section><h2>📱 Platform — exact quality from CRM (Facebook vs Instagram)</h2>
{table(["Platform", "Leads", "Quality", "Quality %", "", "Visits", "Doubtful", "Spend*", "Cost / Quality*"], pf_rows)}
<p class="tiny">* Spend per platform only counts placements that name the platform; a combined "Feed" placement is not split.</p></section>"""

    # ---------------- VISITS & BOOKINGS
    vk = "".join([
        card("Site visits (Visited + Booked)", num(acc["site_visits"])), card("Visit %", pc(acc["visit_ratio_pct"]), f'target {th["target_visit_ratio_pct"]}%'),
        card("Cost / Site visit", fmt_inr(acc["cost_per_visit"])), card("Bookings", num(acc["booked"])),
        card("Cost / Booking", fmt_inr(acc["cost_per_booking"])), card("Visit → Booking %", pc(acc["visit_to_booking_pct"])),
    ])
    v_rows = [[f'<b>{E(c["campaign"])}</b>', num(c["leads"]), num(c["will_visit"]), num(c["site_visits"]), pc(c["visit_ratio_pct"]), fmt_inr(c["cost_per_visit"]),
               num(c["booked"]), fmt_inr(c["cost_per_booking"])] for c in r["campaigns"]]
    lists = r["lists"]
    visits_html = f"""
<section class="band"><h2>🏠 Site visits & bookings</h2><div class="kpis">{vk}</div></section>
<section><h2>By campaign</h2>{table(["Campaign", "Leads", "Will visit", "Visited (+booked)", "Visit %", "Cost / visit", "Bookings", "Cost / booking"], v_rows)}</section>
<section class="leadlist"><h2>📌 Will Visit — confirm these slots ({len(lists['will_visit'])})</h2>{lead_table(lists['will_visit'])}</section>
<section class="leadlist"><h2>🔥 Push for a visit — Details / Call back / Interested ({len(lists['push_for_visit'])})</h2>{lead_table(lists['push_for_visit'])}</section>
<section class="leadlist"><h2>✅ Visited / Booked ({len(lists['visited_booked'])})</h2>{lead_table(lists['visited_booked'])}</section>"""

    # ---------------- LEAD LIST
    buckets = sorted({x["bucket"] for x in lists["all"]})
    opts = "".join(f'<option value="{E(b)}">{E(b)}</option>' for b in buckets)
    all_rows = "".join(
        f'<tr data-b="{E(x["bucket"])}" data-d="{1 if x["doubt"] else 0}"><td>{E(x["date"])}</td><td>{E(x["name"])}</td><td>{E(x["campaign"])}</td>'
        f'<td>{E(x["ad"])}</td><td>{E(x["platform"])}</td><td>{E(x["latest"])}</td><td>{E(x["bucket"])}</td><td>{E(x["stage"])}</td>'
        f'<td>{E(x["proof"])}</td><td>{pill(x["doubt"], "warn", "⚠") if x["doubt"] else ""}</td><td class="tiny">{E(x["feedback"])}</td></tr>'
        for x in lists["all"])
    leadlist_html = f"""
<section><h2>🔎 Every lead — how it was counted, and why</h2>
<div class="filters"><label>Bucket <select id="fb" onchange="flt()"><option value="">All</option>{opts}</select></label>
<label><input type="checkbox" id="fd" onchange="flt()"> Only doubtful quality</label><span id="fcount" class="muted"></span></div>
<div class="tw"><table class="small" id="lt"><thead><tr><th>Date</th><th>Name</th><th>Campaign</th><th>Ad</th><th>Platform</th><th>Latest status</th>
<th>Bucket</th><th>Stage</th><th>Why (proof)</th><th>Feedback check</th><th>Feedback</th></tr></thead><tbody>{all_rows}</tbody></table></div></section>
<section><h2>📵 Not called yet ({len(lists['not_called'])})</h2>{lead_table(lists['not_called'], False)}</section>"""

    # ---------------- DATA CHECKS
    icon = {"fixed": "✅", "warn": "⚠️", "info": "ℹ️"}
    chk = "".join(f'<li class="{E(c["type"])}"><span>{icon.get(c["type"], "•")}</span> {E(c["text"])}</li>' for c in r["checks"])
    rules = r["rules"]
    checks_html = f"""
<section><h2>🧮 How the numbers are counted</h2><ul>
<li><b>Quality %</b> = {E(rules['quality_pct'])}. Quality = {E(', '.join(rules['quality_stages']))}. <b>CAPI sheets:</b> read from the Final Status column (the CAPI script already keeps a lead quality once it was quality). <b>Sheets without CAPI / empty Final Status:</b> found in Current Status or any Follow-up — once quality, always quality. Feedback is only used to flag doubtful leads.</li>
<li><b>Visit ratio</b> = {E(rules['visit_ratio'])}.</li>
<li><b>Verdict</b>: Quality {th['scale_quality_pct']}%+ and visits {th['target_visit_ratio_pct']}%+ = SCALE · {th['optimize_quality_pct']}–{th['scale_quality_pct']}% = OPTIMIZE · {th['review_quality_pct']}–{th['optimize_quality_pct']}% = REVIEW ADS + AUDIENCE · below {th['review_quality_pct']}% = PAUSE / FIX · under {th['low_data_leads']} leads = LOW DATA (judge on CTR / CPM / CPL).</li>
<li><b>Same rules everywhere</b> — campaigns, ads and the account use the same formula and the same low-data limit.</li></ul></section>
{reconcile_html(r)}
<section><h2>🛠 Data checks for this report</h2><ul class="checks">{chk}</ul>
<p class="tiny">Files used: {E(json.dumps(r['files']))}</p></section>"""

    # ---------------- ACTION PLAN
    acts = ins.get("actions") or r.get("actions") or []
    src_note = "Written by the agent after reading the data and sales feedback." if ins.get("actions") else "Auto-generated from the numbers (rules). Run /campaign-dashboard in Claude for expert-written actions."
    def apill(pr):
        k, i = PRIORITY_STYLE.get(pr, ("muted", "○"))
        return pill(pr, k, i)
    owners = {}
    for a in acts:
        owners[a.get("owner", "—")] = owners.get(a.get("owner", "—"), 0) + 1
    act_rows = [[str(i), apill(a.get("priority", "")), f'<b>{E(a.get("owner", ""))}</b>', E(a.get("when", "")), f'<b>{E(a.get("action", ""))}</b>',
                 E(a.get("why", "")), E(a.get("impact", ""))] for i, a in enumerate(acts, 1)]
    G = r.get("growth") or {}
    g_c = [[f'<b>{E(x["campaign"])}</b>', move_pill(x["move"]), num(x["leads"]), f'{x["quality"]} ({pc(x["quality_pct"])})', pc(x["visit_ratio_pct"]),
            fmt_inr(x["cost_per_quality"]), E(x["why"])] for x in G.get("campaigns", [])]
    g_a = [[f'<b>{E(x["ad"])}</b><div class="tiny">{E(x["campaign"])}</div>', move_pill(x["move"]), E(x["angle"]), E(x["format"]), E(x["why"])] for x in G.get("ads", [])]
    g_an = [[f'<b>{E(x["angle"])}</b>', f'{x["quality"]} of {x["leads"]} ({pc(x["quality_pct"])})', fmt_inr(x["cost_per_quality"]), num(x["site_visits"])] for x in G.get("angles", [])]
    g_p = [[f'<b>{E(x["placement"])}</b>', move_pill(x["move"]), E(x["why"])] for x in G.get("placements", [])]
    growth_html = f"""
<section class="band"><h2>📈 Growth plan — put money where quality comes from</h2>
<p class="muted">Goal: {G.get('targets', {}).get('quality_pct', 30)}% quality leads and {G.get('targets', {}).get('visit_ratio_pct', 4)}% site visits. Scale what already hits it, copy what works, cut what does not.</p></section>
<section><h2>1 · Campaigns — scale the ones that give quality</h2>{table(["Campaign", "Move", "Leads", "Quality", "Visits", "Cost / quality", "Why"], g_c)}</section>
<section class="two"><div><h2>2 · Ads — make more of what brings quality</h2>{table(["Ad", "Move", "Angle", "Format", "Why"], g_a)}</div>
<div><h2>Winning angles</h2>{table(["Angle", "Quality", "Cost / quality", "Visits"], g_an)}
{('<h3>Next creatives</h3>' + ul(ins.get('next_creatives'))) if ins.get('next_creatives') else '<p class="tiny">Brief 2-3 new ads in the top angle: same message, new first 3 seconds / new visual.</p>'}</div></section>
<section><h2>3 · Placements — keep the ones that give quality</h2>{table(["Placement", "Move", "Why"], g_p) if g_p else '<p class="muted">Add a Meta export with the Placement breakdown to see this.</p>'}</section>"""
    actions_html = growth_html + f"""
<section class="band"><h2>✅ Action plan — what to do, who does it, by when</h2>
<p class="muted">{E(src_note)}</p>
<div class="kpis">{''.join(card(o, str(n), "actions") for o, n in owners.items())}</div></section>
<section>{table(["#", "Priority", "Owner", "When", "Action", "Why (from the data)", "Expected result"], act_rows)}
<p class="tiny">P1 = do today / this week, money or leads are being lost now · P2 = this week, improves cost or quality · P3 = housekeeping that keeps reports accurate.</p></section>"""

    expert = ""
    if ins.get("notes"):
        expert = f'<section class="expert"><h2>🧑‍💼 Expert notes</h2>{ul(ins["notes"])}</section>'

    bad = [x for x in (r.get("reconcile") or []) if not x["ok"]]
    if bad:
        expert = ('<section class="bbox"><h2>❌ Numbers do not match between tabs</h2>' + ul([f'{x["name"]}: {x["a"]} vs {x["b"]}' for x in bad])
                  + '<p class="tiny">Do not send this report — see Data Checks.</p></section>') + expert
    tabs = [("summary", "Summary", expert + summary_html), ("client", "Client Report", client_html(r)), ("optimise", "Optimisation", optimisation_html(r)), ("actions", "Action Plan", actions_html), ("campaigns", "Campaigns", campaigns_html), ("ads", "Ads & Angles", ads_html),
            ("placement", "Platform & Placement", placement_html), ("visits", "Visits & Bookings", visits_html),
            ("leads", "Lead List", leadlist_html), ("checks", "Data Checks", checks_html)]
    keys = [k for k, _, _ in tabs]
    first = start_tab if start_tab in keys else "summary"
    nav = "".join(f'<button class="tab{" on" if k == first else ""}" data-t="{k}" onclick="sh(\'{k}\')">{E(t)}</button>' for k, t, _ in tabs)
    panes = "".join(f'<div class="pane{" on" if k == first else ""}" id="p-{k}"><h1 class="ptitle">{E(t)}</h1>{body}</div>' for k, t, body in tabs)
    rg = r.get("range") or {}
    qbtns = "".join(f'<button class="qb" data-q="{n}" onclick="qn({n})">{n} days</button>' for n in (7, 14)) + \
        '<button class="qb" data-q="month" onclick="qn(\'month\')" title="1st to last day of the month">Full month</button>' + \
        "".join(f'<button class="qb" data-q="{n}" onclick="qn({n})">{n} days</button>' for n in (45, 60))
    rbar = (f'<div class="rbar noprint"><span class="rl">Period:</span>{qbtns}<button class="qb" data-q="all" onclick="qn(\'all\')">All data</button>'
            f'<span class="rl">From</span><input type="date" id="rf" value="{E(rg.get("from") or "")}" min="{E(rg.get("data_min") or "")}" max="{E(rg.get("data_max") or "")}">'
            f'<span class="rl">To</span><input type="date" id="rt" value="{E(rg.get("to") or "")}" min="{E(rg.get("data_min") or "")}" max="{E(rg.get("data_max") or "")}">'
            f'<button class="qb go" onclick="apply()">Apply</button>'
            f'<label class="rl"><input type="checkbox" id="rc" checked onchange="cmpToggle()"> Compare with previous period</label><span id="rmsg" class="tiny"></span></div>')
    rjs = json.dumps({"from": rg.get("from"), "to": rg.get("to"), "data_min": rg.get("data_min"), "data_max": rg.get("data_max"),
                      "custom": bool(rg.get("custom")), "start": first})
    title = f'{r["client"].replace("-", " ").title()} — Meta Lead Ads Dashboard'
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)}</title><style>{CSS}</style></head><body class="viz-root">
<header><h1>📊 {E(title)}</h1><div class="sub">Data: {E(r['period']['from'])} – {E(r['period']['to'])} · Generated {E(r['generated_at'])} ·
Quality % = quality ÷ total leads</div>{rbar}<nav>{nav}</nav></header><main>{panes}</main>
<script>
const RANGE={rjs};let CUR=RANGE.start;
function sh(k){{CUR=k;document.querySelectorAll('.pane').forEach(p=>p.classList.toggle('on',p.id==='p-'+k));
document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on',b.dataset.t===k));window.scrollTo(0,0);}}
function sp(n){{document.querySelectorAll('.pblock').forEach(b=>b.classList.toggle('on',b.dataset.n===n));
document.querySelectorAll('.pbtn').forEach(b=>b.classList.toggle('on',b.dataset.n===n));}}
function flt(){{const b=document.getElementById('fb').value,d=document.getElementById('fd').checked;let n=0;
document.querySelectorAll('#lt tbody tr').forEach(tr=>{{const ok=(!b||tr.dataset.b===b)&&(!d||tr.dataset.d==='1');tr.style.display=ok?'':'none';if(ok)n++;}});
document.getElementById('fcount').textContent=n+' leads';}}
flt();
function ymd(s){{const a=s.split('-').map(Number);return Date.UTC(a[0],a[1]-1,a[2]);}}
function iso(t){{return new Date(t).toISOString().slice(0,10);}}
function msg(t){{document.getElementById('rmsg').textContent=t;}}
function go(f,t){{
  if(window.parent&&window.parent!==window){{window.parent.postMessage({{type:'ld-range',from:f,to:t,tab:CUR}},'*');msg('Updating…');}}
  else msg('Period change works on the website (upload the files there).');}}
function qn(n){{if(n==='all'){{go('','');return;}}if(!RANGE.data_max)return;
  if(n==='month'){{const m=(RANGE.custom&&RANGE.to?RANGE.to:RANGE.data_max).slice(0,7);const a=m.split('-').map(Number);
    go(m+'-01',iso(Date.UTC(a[0],a[1],0)));return;}}
  const t=ymd(RANGE.data_max);go(iso(t-(n-1)*864e5),RANGE.data_max);}}
function apply(){{const f=document.getElementById('rf').value,t=document.getElementById('rt').value;
  if(f&&t&&f>t){{msg('From date is after To date.');return;}}go(f,t);}}
function cmpToggle(){{document.body.classList.toggle('nocmp',!document.getElementById('rc').checked);}}
function clientPdf(){{sh('client');document.body.classList.add('client-print');setTimeout(()=>{{window.print();setTimeout(()=>document.body.classList.remove('client-print'),800);}},150);}}
(function(){{let on='all';
  if(RANGE.custom&&RANGE.from&&RANGE.from.slice(8)==='01'&&RANGE.to===iso(Date.UTC(+RANGE.from.slice(0,4),+RANGE.from.slice(5,7),0)))on='month';
  else if(RANGE.custom&&RANGE.to===RANGE.data_max&&RANGE.from){{const n=Math.round((ymd(RANGE.to)-ymd(RANGE.from))/864e5)+1;if([7,14,45,60].includes(n))on=String(n);else on='';}}
  else if(RANGE.custom)on='';
  document.querySelectorAll('.qb[data-q]').forEach(b=>b.classList.toggle('on',b.dataset.q===on));
  if(location.hash==='#clientpdf')setTimeout(clientPdf,400);}})();
</script></body></html>"""


CSS = """
.viz-root{color-scheme:light;--surface:#fcfcfb;--panel:#ffffff;--band:#f3f6fb;--line:#e3e2de;--text:#0b0b0b;--text2:#52514e;--muted:#7a7974;
--head:#1f2a37;--series:#2a78d6;--series2:#eb6834;--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--critical:#d03b3b;--goodtxt:#006300;--badtxt:#b42318}
@media (prefers-color-scheme:dark){.viz-root{color-scheme:dark;--surface:#1a1a19;--panel:#222221;--band:#262a30;--line:#3a3a37;--text:#f0efec;
--text2:#c3c2b7;--muted:#9a998f;--head:#0f1620;--series:#3987e5;--series2:#d95926;--goodtxt:#5cc85c;--badtxt:#f08a8a}}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{background:var(--head);color:#fff;padding:14px 20px 0;position:sticky;top:0;z-index:5}
h1{margin:0;font-size:20px}.sub{opacity:.8;font-size:12px;margin:4px 0 10px}
nav{display:flex;gap:4px;overflow-x:auto}.tab{background:transparent;color:#cfd6df;border:0;padding:9px 14px;font:inherit;cursor:pointer;border-radius:8px 8px 0 0;white-space:nowrap}
.tab.on{background:var(--surface);color:var(--text);font-weight:600}
.pbar{display:flex;gap:6px;align-items:center;margin-bottom:10px;font-size:13px;color:var(--muted)}
.pbtn{border:1px solid var(--line);background:var(--surface);color:var(--text);padding:5px 12px;border-radius:16px;cursor:pointer;font:inherit}
.pbtn.on{background:var(--head);color:#fff;border-color:var(--head);font-weight:600}
.pblock{display:none}.pblock.on{display:block}.fstrip{margin:10px 0 4px;font-size:13px}
.rbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:8px 0 6px;font-size:13px;color:#cfd6df}
.rbar .rl{margin-left:6px}.rbar input[type=date]{font:inherit;padding:3px 6px;border-radius:6px;border:1px solid #4a5563;background:#2b3644;color:#fff}
.qb{border:1px solid #4a5563;background:transparent;color:#e6ebf1;padding:4px 10px;border-radius:14px;cursor:pointer;font:inherit}
.qb.on{background:#fff;color:#1f2a37;font-weight:600}.qb.go{background:var(--series);border-color:var(--series);color:#fff}
#rmsg{color:#ffd27a;margin-left:6px}
.nocmp .cmpnote,.nocmp .kpi .d{display:none}
.client-head .ch{display:flex;justify-content:space-between;align-items:center;gap:10px}
.btn{border:1px solid var(--line);background:var(--panel);color:var(--text);padding:6px 12px;border-radius:8px;cursor:pointer;font:inherit}
.kpis.k3{grid-template-columns:repeat(3,1fr)}
main{max-width:1280px;margin:0 auto;padding:16px}.pane{display:none}.pane.on{display:block}
section{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:0 0 14px}
section.band{background:var(--band)}h2{font-size:16px;margin:0 0 10px}h3{font-size:14px;margin:12px 0 6px;color:var(--text2)}
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.kpis.k8{grid-template-columns:repeat(8,1fr)}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px;text-align:center}
.kl{font-size:12px;color:var(--text2)}.kv{font-size:24px;font-weight:700;margin:4px 0}.ks{font-size:12px;color:var(--muted)}
.tiny{font-size:11px;color:var(--muted)}.muted{color:var(--muted)}
.d{font-weight:600;font-size:12px}.goodtxt{color:var(--goodtxt)}.badtxt{color:var(--badtxt)}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th{background:var(--head);color:#fff;text-align:left;padding:7px 8px;font-weight:600;white-space:nowrap}
td{border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top}table.small{font-size:12px}tbody tr:hover{background:var(--band)}
.bar{width:110px;height:10px;background:var(--line);border-radius:4px;overflow:hidden}.bar span{display:block;height:100%;border-radius:0 4px 4px 0}
.bar .series{background:var(--series)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid;white-space:nowrap}
.pill.good{color:var(--goodtxt);border-color:var(--good);background:color-mix(in srgb,var(--good) 12%,transparent)}
.pill.warn{color:#8a5a00;border-color:var(--warn);background:color-mix(in srgb,var(--warn) 18%,transparent)}
.pill.serious{color:#a5441b;border-color:var(--serious);background:color-mix(in srgb,var(--serious) 15%,transparent)}
.pill.critical{color:var(--badtxt);border-color:var(--critical);background:color-mix(in srgb,var(--critical) 12%,transparent)}
.pill.muted{color:var(--text2);border-color:var(--line)}
@media (prefers-color-scheme:dark){.pill.warn{color:#fab219}.pill.serious{color:#ec835a}}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}.three{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:10px}
section.two{display:grid}.gbox,.bbox,.sbox{border:1px solid var(--line);border-radius:8px;padding:8px 12px}
.gbox{border-left:4px solid var(--good)}.bbox{border-left:4px solid var(--critical)}.sbox{border-left:4px solid var(--series)}
ul{margin:4px 0;padding-left:20px}li{margin:3px 0}
.funnel .fr{display:grid;grid-template-columns:150px 1fr 70px 130px;gap:8px;align-items:center;padding:3px 0}
.fl{font-size:12px}.ft{height:18px;background:var(--line);border-radius:4px;overflow:hidden}.ft span{display:block;height:100%;border-radius:0 4px 4px 0}
.fv{font-size:12px}.fc{font-size:11px;color:var(--muted)}.fr.brk .fc{color:var(--badtxt);font-weight:700}.fr.brk .fl{font-weight:700}
.callout{margin-top:8px;padding:8px 10px;border-left:4px solid var(--critical);background:color-mix(in srgb,var(--critical) 8%,transparent);border-radius:6px;font-size:13px}
.legend{display:flex;gap:14px;font-size:12px;margin-bottom:6px;flex-wrap:wrap}.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
.sw.b1,.dbars .b1{background:var(--series)}.sw.b2,.dbars .b2{background:var(--series2)}
.daily{display:flex;gap:2px;align-items:flex-end;height:170px;border-bottom:1px solid var(--line);overflow:hidden}
.dc{flex:1 1 0;min-width:0;display:flex;flex-direction:column;align-items:center;height:100%;cursor:default}
.dbars{flex:1;width:100%;display:flex;gap:2px;align-items:flex-end;justify-content:center}.dbars span{width:45%;border-radius:3px 3px 0 0;min-height:0}
.dl{font-size:9px;color:var(--muted);writing-mode:vertical-rl;transform:rotate(180deg);height:38px}
.dc:hover .dbars span{outline:2px solid var(--surface)}
.ch{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.ch h2{margin:0 6px 0 0}
.filters{display:flex;gap:16px;align-items:center;margin-bottom:8px;flex-wrap:wrap}select{font:inherit;padding:3px 6px}
.checks{list-style:none;padding:0}.checks li{padding:6px 8px;border-bottom:1px solid var(--line)}.checks li.warn{background:color-mix(in srgb,var(--warn) 10%,transparent)}
.expert{border-left:4px solid var(--series)}
.ptitle{display:none}
@media print{
  @page{size:A4 landscape;margin:10mm}
  .viz-root{color-scheme:light;--surface:#fff;--panel:#fff;--band:#f3f6fb;--line:#d9d8d3;--text:#0b0b0b;--text2:#52514e;--muted:#6b6a65;--head:#1f2a37}
  body{font-size:11px;background:#fff}
  header{position:static;padding:10px 14px}nav{display:none}
  main{max-width:none;padding:0}
  .pane{display:block!important;page-break-before:always}.pane:first-child{page-break-before:auto}
  .ptitle{display:block;font-size:18px;margin:4px 0 10px;border-bottom:2px solid var(--head);padding-bottom:4px}
  section{break-inside:avoid-page;page-break-inside:avoid;margin-bottom:10px}
  section.camp{break-inside:auto;page-break-inside:auto}
  table{font-size:10px}th,td{padding:4px 6px}
  .tw{overflow:visible}.kv{font-size:18px}
  .filters{display:none}.noprint,.rbar{display:none!important}
  .client-print .pane{display:none!important}.client-print #p-client{display:block!important;page-break-before:auto}
  .client-print header .sub{display:none}
  #p-leads,.leadlist{display:none!important}
  .pdf-full #p-leads{display:block!important}.pdf-full .leadlist{display:block!important}
  .pdf-full #p-leads tbody tr{display:table-row!important}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
.frl{display:grid;grid-template-columns:150px 1fr;gap:8px;margin:-2px 0 2px}.lostrow{display:flex;flex-wrap:wrap;gap:4px}
.lost{font-size:11px;color:var(--badtxt);background:color-mix(in srgb,var(--critical) 8%,transparent);border-radius:4px;padding:1px 6px}
.targets .tg{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
@media (max-width:900px){.kpis,.kpis.k8{grid-template-columns:repeat(2,1fr)}.two,.three,section.two{grid-template-columns:1fr}
.funnel .fr{grid-template-columns:110px 1fr 60px}.fc{grid-column:2/4}}
"""
