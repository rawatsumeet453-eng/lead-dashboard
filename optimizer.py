"""
Meta Optimisation Agent — rule engine (real estate lead gen, Oct 2026).

Reads the dashboard report dict built by campaign_dashboard.build_report() and returns
verdicts for the account, every campaign, ads and placements, plus a prioritised action list.

Rules (see claude/META_RE_MUMBAI_RESEARCH_OCT2026.md, section 6):
  R1  CAPI health < 80% or false Purchase          -> FIX TRACKING FIRST (only if a CAPI audit is attached)
  R2  No spend data                                 -> INCOMPLETE DATA (cost rules skipped)
  R3  Strict Q >= target AND visits >= target AND cost/quality OK -> SCALE
  R4  Strict Q >= target AND visits < target        -> FIX SALES
  R5  Strict Q < target AND visits >= target        -> CLEAN FUNNEL
  R6  Strict Q < 20%                                -> FIX ADS + FORM
  R7  Ad fatigued                                   -> REFRESH
  R8  Ad spend >= 3x account cost/quality, 0 quality-> PAUSE
  R9  Placement est. cost/quality >= 1.5x and >= 15% spend share -> EXCLUDE (test)
  R10 CP/broker >= 10% or wrong-fit >= 15% of leads -> FILTER (form + copy + CAPI)
  R11 Doubtful >= 20% of quality                    -> SIGNAL RISK
  R12 Not reached >= 25%                            -> SPEED / CALLING FIX
  R13 Last-7d strict Q down >= 10 pts               -> ALERT
  R14 Campaign < 3 days old                         -> LEARNING — HOLD
  R15 >= 2 active campaigns, each < 25 leads/week   -> CONSOLIDATE
  R16 < 7 days of data                              -> no placement cuts
  R17 Status Q - strict Q >= 10 pts                 -> don't switch to qualified-leads goal yet
Strict quality (feedback-verified) is used for every decision, never raw status quality.
"""
from __future__ import annotations

import datetime as dt
import re

RULES = [
    ("R1", "CAPI health < 80% or a false Purchase", "FIX TRACKING FIRST"),
    ("R2", "No Meta spend data in the upload", "INCOMPLETE DATA"),
    ("R3", "Strict quality and visits on target, cost per quality OK", "SCALE"),
    ("R4", "Strict quality on target but visits low", "FIX SALES"),
    ("R5", "Strict quality low but visits on target", "CLEAN FUNNEL"),
    ("R6", "Strict quality under 20%", "FIX ADS + FORM"),
    ("R7", "Ad CTR down / CPL up vs its previous week", "REFRESH"),
    ("R8", "Ad spent 3x account cost per quality with 0 quality", "PAUSE"),
    ("R9", "Placement est. cost per quality 1.5x+ with 15%+ of spend", "EXCLUDE (test)"),
    ("R10", "CP / broker 10%+ or wrong-fit 15%+ of leads", "FILTER"),
    ("R11", "Doubtful leads are 20%+ of quality", "SIGNAL RISK"),
    ("R12", "Not reached 25%+", "CALLING FIX"),
    ("R13", "Last 7 days strict quality down 10+ points", "ALERT"),
    ("R14", "Campaign less than 3 days old", "LEARNING — HOLD"),
    ("R15", "2+ active campaigns, each under 25 leads / week", "CONSOLIDATE"),
    ("R16", "Less than 7 days of data", "NO PLACEMENT CUTS"),
    ("R17", "Status quality vs strict quality gap 10+ points", "NOT READY FOR QUALIFIED-LEADS GOAL"),
]

CP_RX = re.compile(r"\bcp\b|broker|channel", re.I)
FIT_RX = re.compile(r"rtmi|possession|mismatch|requirement|location|budget|config", re.I)
NONAME = "(no campaign name)"


def _pdate(s):
    for f in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(s).strip(), f).date()
        except ValueError:
            pass
    return None


def _n(x):
    return x if isinstance(x, (int, float)) else 0


def _inr(x):
    return "—" if not x else f"₹{round(x):,}"


def _verdict(strict_pct, visit_pct, cpq, base_cpq, leads, th, has_spend):
    """Campaign / account level verdict (R3-R6). Returns (verdict, rule, owner, action, why)."""
    tq, tv, low = th["scale_quality_pct"], th["target_visit_ratio_pct"], th["low_data_leads"]
    if leads < low:
        return ("LOW DATA", "—", "Media",
                "Let it run to 15+ leads before judging quality. Meanwhile watch CTR, CPM and CPL only.",
                f"Only {leads} leads.")
    cost_ok = (not has_spend) or (not cpq) or (not base_cpq) or cpq <= base_cpq * 1.15
    if strict_pct >= tq and visit_pct >= tv and cost_ok:
        return ("SCALE", "R3", "Media",
                "Raise budget 15–20% every 3–4 days and add 1–2 new concepts in the winning angle.",
                f"Strict quality {strict_pct}% (target {tq}%) and visits {visit_pct}% (target {tv}%)."
                + ("" if has_spend else " Cost not checked — add the Meta export first."))
    if strict_pct >= tq and visit_pct >= tv:
        return ("OPTIMISE", "R3", "Media",
                "Quality and visits are on target but cost per quality lead is high — cut the weakest ads / placements before scaling.",
                f"Cost per quality {_inr(cpq)} vs account {_inr(base_cpq)}.")
    if strict_pct >= tq:
        return ("FIX SALES", "R4", "Sales",
                "Hold budget. Call every Details / Call back / Interested lead with two fixed visit slots; confirm Will Visit leads the day before.",
                f"Strict quality {strict_pct}% is fine but visits are {visit_pct}% (target {tv}%).")
    if visit_pct >= tv:
        return ("CLEAN FUNNEL", "R5", "Media",
                "Keep the campaign (it brings visits). Tighten the form (budget / timeline / broker question) and judge on cost per visit, not quality %.",
                f"Visits {visit_pct}% are on target, strict quality only {strict_pct}%.")
    if strict_pct < 20:
        return ("FIX ADS + FORM", "R6", "Media",
                "Higher Intent form + 2–3 qualifier questions, pause the bottom 30% ads by cost per quality, launch new concepts with price / possession in the first frame.",
                f"Strict quality {strict_pct}% (under 20%).")
    return ("OPTIMISE", "R4/R6", "Both",
            "Sales: visit push on quality leads. Media: replace weakest ads and add qualifier questions.",
            f"Strict quality {strict_pct}% (target {tq}%), visits {visit_pct}% (target {tv}%).")


def optimise(r: dict) -> dict:
    acc, th = r["account"], r["rules"]["thresholds"]
    tq = th["scale_quality_pct"]
    has_spend = bool(acc.get("spend"))
    base_cpq = acc.get("cost_per_quality") if has_spend else None
    p_from, p_to = _pdate(r["period"]["from"]), _pdate(r["period"]["to"])
    days = (p_to - p_from).days + 1 if p_from and p_to else None
    leads_all = r.get("lists", {}).get("all", [])

    gates, actions = [], []

    def gate(rid, status, detail):
        gates.append({"rule": rid, "name": next(x[1] for x in RULES if x[0] == rid), "status": status, "detail": detail})

    def act(pr, owner, text, why, rule):
        actions.append({"priority": pr, "owner": owner, "action": text, "why": why, "rule": rule})

    # ---- R1 CAPI
    capi = r.get("capi") or {}
    if capi:
        h, fp = capi.get("health_pct"), capi.get("false_purchase", 0)
        bad = (h is not None and h < 80) or fp
        gate("R1", "FAIL" if bad else "PASS", f"CAPI health {h}% · false Purchase {fp}")
        if bad:
            act("P1", "Tracking", "Fix the CAPI script before any scaling (install v5, stop Purchase for non-bookings).",
                f"CAPI health {h}%, {fp} false Purchase — Meta is learning the wrong buyer.", "R1")
    else:
        gate("R1", "N/A", "Not checked on the website — run the CAPI audit in Claude and fix it before scaling.")

    # ---- R2 spend
    gate("R2", "PASS" if has_spend else "FAIL",
         f"Spend {_inr(acc.get('spend'))}" if has_spend else "No Meta Ads export — CPL, cost per quality, pause and placement rules are skipped.")
    if not has_spend:
        act("P1", "Media", "Upload the Ads Manager export (Ad name + Day + Placement) with the CRM sheet.",
            "Without spend the agent cannot judge cost per quality, pause losers or check placements.", "R2")

    # ---- R11 / R17 signal
    q, sq = _n(acc.get("quality_pct")), _n(acc.get("strict_quality_pct"))
    gap = round(q - sq, 1)
    doubt_share = round(100 * _n(acc.get("doubtful_quality")) / acc["quality"], 1) if acc.get("quality") else 0
    sig_bad = gap >= 10 or doubt_share >= 20
    gate("R11", "FAIL" if doubt_share >= 20 else "PASS", f"{acc.get('doubtful_quality', 0)} doubtful = {doubt_share}% of quality leads")
    gate("R17", "FAIL" if gap >= 10 else "PASS", f"Status quality {q}% vs strict {sq}% (gap {gap} pts)")
    if sig_bad:
        act("P1", "Sales", "Fix status discipline: a lead marked quality must not have 'not genuine / going elsewhere / not interested' in feedback. Use a dropdown for status.",
            f"Status says {q}% quality, feedback says {sq}%. Sending this to Meta teaches it the wrong buyer — don't switch to the qualified-leads goal yet.", "R11/R17")

    # ---- R12 not reached
    nr = _n(acc.get("not_reached_pct"))
    gate("R12", "FAIL" if nr >= 25 else "PASS", f"Not reached {nr}%")
    if nr >= 25:
        act("P2", "Sales", "Call within 5 minutes of the lead, 3 attempts at different times, WhatsApp the brochure after the 1st missed call.",
            f"{nr}% of called leads were never reached.", "R12")

    # ---- R13 last 7 days
    L, P = r["compare"]["last"], r["compare"]["prev"]
    if _n(L.get("leads")) >= 10 and _n(P.get("leads")) >= 10:
        drop = round(_n(P.get("strict_quality_pct")) - _n(L.get("strict_quality_pct")), 1)
        gate("R13", "FAIL" if drop >= 10 else "PASS",
             f"Strict quality last 7 days {L.get('strict_quality_pct')}% vs previous {P.get('strict_quality_pct')}%")
        if drop >= 10:
            act("P1", "Media", "Check what changed this week: new ads, new campaign, placements (Threads / Audience Network), audience expansion.",
                f"Strict quality fell {drop} points in the last 7 days.", "R13")
    else:
        gate("R13", "N/A", "Under 10 leads in one of the two weeks")

    # ---- R10 dead reasons
    dr = acc.get("dead_reasons") or {}
    leads = max(_n(acc.get("leads")), 1)
    cp = sum(v for k, v in dr.items() if CP_RX.search(k))
    fit = sum(v for k, v in dr.items() if FIT_RX.search(k))
    cp_pct, fit_pct = round(100 * cp / leads, 1), round(100 * fit / leads, 1)
    gate("R10", "FAIL" if cp_pct >= 10 or fit_pct >= 15 else "PASS", f"CP / broker {cp_pct}% · wrong fit (possession, location, budget, requirement) {fit_pct}% of leads")
    if cp_pct >= 10:
        act("P2", "Media", "Add 'Are you a channel partner / broker?' to the form, exclude the broker list, send Lead_Disqualified for CP leads.",
            f"{cp} CP / broker leads = {cp_pct}% of all leads.", "R10")
    if fit_pct >= 15:
        top = ", ".join(f"{k} {v}" for k, v in dr.items() if FIT_RX.search(k))
        act("P2", "Media", "Put possession date, carpet area, price and location in the first frame / first line, and add a budget + 'when do you plan to move' question.",
            f"Wrong-fit dead leads {fit_pct}% ({top}).", "R10")

    # ---- R16 data length
    gate("R16", "FAIL" if days is not None and days < 7 else "PASS", f"{days} days of data" if days else "Period unknown")

    # ---- campaigns
    camp_recent, camp_age = {}, {}
    for x in leads_all:
        c, age = x.get("campaign"), x.get("age_days")
        if age is None:
            continue
        camp_age[c] = max(camp_age.get(c, 0), age)
        if age <= 13:
            camp_recent[c] = camp_recent.get(c, 0) + 1

    camps = []
    for c in r["campaigns"]:
        name = c["campaign"]
        v = _verdict(_n(c.get("strict_quality_pct")), _n(c.get("visit_ratio_pct")), c.get("cost_per_quality"), base_cpq,
                     c["leads"], th, has_spend)
        verdict, rule, owner, action, why = v
        age = camp_age.get(name)
        if name != NONAME and age is not None and age <= 2:
            verdict, rule, owner = "LEARNING — HOLD", "R14", "Media"
            action, why = "Don't edit or judge yet (only clear waste can be cut).", f"First lead {age} day(s) ago — still learning."
        camps.append({"campaign": name, "leads": c["leads"], "leads_14d": camp_recent.get(name, 0), "spend": c.get("spend"),
                      "strict_quality_pct": c.get("strict_quality_pct"), "quality_pct": c.get("quality_pct"),
                      "visit_ratio_pct": c.get("visit_ratio_pct"), "cost_per_quality": c.get("cost_per_quality"),
                      "cost_per_visit": c.get("cost_per_visit"), "verdict": verdict, "rule": rule, "owner": owner,
                      "action": action, "why": why, "active": camp_recent.get(name, 0) > 0 and name != NONAME})

    # ---- R15 consolidate
    active = [c for c in camps if c["active"]]
    small = [c for c in active if c["leads_14d"] / 2 < 25]
    if len(active) >= 2 and len(small) == len(active):
        gate("R15", "FAIL", f"{len(active)} campaigns got leads in the last 14 days, each under 25 leads / week")
        act("P2", "Media", f"Merge the {len(active)} active campaigns into 1 campaign + 1 broad ad set per project; move the best concepts in.",
            "Meta's ad model (GEM / Andromeda) learns from big, steady pools. Small split campaigns stay stuck in learning.", "R15")
        for c in camps:
            if c in small:
                c["consolidate"] = True
    else:
        gate("R15", "PASS", f"{len(active)} active campaign(s)")

    # ---- account verdict
    av = _verdict(sq, _n(acc.get("visit_ratio_pct")), base_cpq, base_cpq, acc["leads"], th, has_spend)
    acc_verdict, acc_why = av[0], av[4]
    if capi and any(g["rule"] == "R1" and g["status"] == "FAIL" for g in gates):
        acc_verdict, acc_why = "FIX TRACKING FIRST", "CAPI problems found — fix before scaling. " + acc_why
    elif acc_verdict == "SCALE" and sig_bad:
        acc_verdict, acc_why = "OPTIMISE", "Numbers look like SCALE, but status quality is inflated (R11/R17). " + acc_why
    if av[0] == "FIX SALES":
        act("P1", "Sales", av[3], av[4], "R4")
    elif av[0] in ("FIX ADS + FORM", "CLEAN FUNNEL", "OPTIMISE"):
        act("P1" if av[0] == "FIX ADS + FORM" else "P2", av[2] if av[2] != "Both" else "Media", av[3], av[4], av[1])
    elif av[0] == "SCALE" and not sig_bad:
        act("P2", "Media", av[3], av[4], "R3")

    # ---- ads
    ads = []
    for a in r["ads"]:
        if a["ad"] == "(no ad name)":
            continue
        st = (a.get("fatigue") or {}).get("status", "")
        row = {"ad": a["ad"], "campaign": a["campaign"], "angle": a.get("angle"), "leads": a["leads"], "spend": a.get("spend"),
               "strict_quality": a.get("strict_quality"), "strict_quality_pct": a.get("strict_quality_pct"),
               "cost_per_quality": a.get("cost_per_quality"), "verdict": None}
        if has_spend and base_cpq and _n(a.get("spend")) >= 3 * base_cpq and not a.get("quality"):
            row.update(verdict="PAUSE", rule="R8", why=f"{_inr(a.get('spend'))} spent, 0 quality leads (3x account cost per quality {_inr(base_cpq)}).",
                       action="Pause and move budget to the best ad.")
        elif st.startswith("Fatigued"):
            row.update(verdict="REFRESH", rule="R7", why=", ".join((a.get("fatigue") or {}).get("signals", [])) or "Fatigued vs previous week.",
                       action="Make a new version in the same angle (new first 3 seconds / new visual); keep the old one until the new one has data.")
        elif a["leads"] >= 5 and _n(a.get("strict_quality_pct")) >= tq and (not base_cpq or not a.get("cost_per_quality") or a["cost_per_quality"] <= base_cpq):
            row.update(verdict="MAKE MORE", rule="R3", why=f"{a.get('strict_quality')} strict quality of {a['leads']} leads ({a.get('strict_quality_pct')}%).",
                       action="Brief 2 new concepts in this angle; give it more budget share.")
        if row["verdict"]:
            ads.append(row)
    for a in ads:
        if a["verdict"] == "PAUSE":
            act("P1", "Media", f"Pause ad '{a['ad']}'.", a["why"], "R8")
        elif a["verdict"] == "REFRESH":
            act("P2", "Creative", f"Refresh ad '{a['ad']}' ({a['angle']}).", a["why"], "R7")
    winners = [a for a in ads if a["verdict"] == "MAKE MORE"][:2]
    if winners:
        act("P2", "Creative", "Make 2 new concepts like: " + ", ".join(f"'{w['ad']}'" for w in winners) + ".",
            "These ads bring strict quality at or below account cost.", "R3")

    # ---- placements
    pls = []
    short = days is not None and days < 7
    for p in r.get("placements", []):
        cpq = p.get("est_cost_per_quality")
        if has_spend and base_cpq and cpq and cpq >= 1.5 * base_cpq and _n(p.get("share_spend_pct")) >= 15:
            pls.append({"placement": p["placement"], "share_spend_pct": p.get("share_spend_pct"), "est_cost_per_quality": cpq,
                        "verdict": "RE-CHECK" if short else "EXCLUDE (test)", "rule": "R16" if short else "R9",
                        "why": f"Est. cost per quality {_inr(cpq)} = {round(cpq / base_cpq, 1)}x account, {p.get('share_spend_pct')}% of spend."
                               + (" Under 7 days of data — check again before cutting." if short else "")})
    for p in pls:
        if p["verdict"] == "EXCLUDE (test)":
            act("P2", "Media", f"Exclude {p['placement']} (account-level placement exclusion) and watch cost per quality for 7 days.", p["why"], "R9")

    order = {"P1": 0, "P2": 1, "P3": 2}
    actions.sort(key=lambda a: order.get(a["priority"], 9))
    return {"verdict": acc_verdict, "verdict_why": acc_why, "days": days, "gates": gates, "campaigns": camps,
            "ads": ads, "placements": pls, "actions": actions, "rules": [{"rule": a, "when": b, "verdict": c} for a, b, c in RULES]}
