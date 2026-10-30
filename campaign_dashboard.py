#!/usr/bin/env python3
"""
Campaign Dashboard Engine — Sumeet's Agency Ops Agent  (pandas-free: same code runs on the desktop AND in the website)
======================================================================================================================
CRM sheet + Meta Ads export (+ optional Meta lead download)  ->  per-campaign dashboard.

    python tools/campaign_dashboard.py --client avyukta
    python tools/campaign_dashboard.py --client avyukta --from 2026-10-15 --to 2026-10-29
    python tools/campaign_dashboard.py --client avyukta --insights clients/avyukta/reports/2026-10-30_insights.json

Put files in clients/<client>/data/ (CSV or XLSX — a whole Google Sheet downloaded as .xlsx also works, every tab is scanned).
  * CRM            -> tab with a status column + name/phone/email
  * Meta Ads       -> tab with "Campaign name" + "Amount spent" (best: Ad name + Day + Placement breakdown)
  * Meta lead file -> Meta "Download leads" export (has form_id / platform) — gives FB vs IG per lead

Rules (Sumeet's standard, see CLAUDE.md):
  Quality % = quality leads / TOTAL leads. Quality = Details Shared, Call Back, Interested, Will Visit, Visited,
  Booking done (found anywhere in status / follow-ups / feedback -> stays quality). Visit ratio = (Visited + Booked) / TOTAL.
Requires: Python 3.10+ and openpyxl (only for .xlsx).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lead_quality import (  # noqa: E402
    WA_SHARE_RX, classify_row, detect_crm_columns,
    A_ALL_CLICKS, A_CAMPAIGN, A_IMPR, A_LINK_CLICKS, A_REACH, A_SPEND, CLIENTS_DIR, H_CAMPAIGN, H_CURRENT_STATUS,
    H_DATE, H_FEEDBACK, H_FINAL_STATUS, H_FOLLOWUP, H_PHONE, Classifier, build_campaign_matcher, clean_email,
    clean_phone, find_cols, fmt_inr, is_sci_phone, is_test_lead, load_config, money, norm, pct, verdict,
)
from purekit import day, parse_dates, read_tables, to_num  # noqa: E402

H_AD = [r"^ad\s+name$", r"^ad$", r"^ad\s+creative$", r"^creative$"]
H_NAME = [r"^full\s+name$", r"^name$", r"^lead\s+name$", r"^customer\s+name$", r"^name"]
H_EMAIL = [r"^e\s?mail$", r"email"]
H_PLATFORM = [r"^platform$", r"^source\s+platform$"]
A_AD = [r"^ad\s+name$"]
A_DAY = [r"^day$", r"^date$"]
A_PLACEMENT = [r"^placement$"]
A_PLATFORM = [r"^platform$"]
A_RESULTS = [r"^leads$", r"^leads\s+form$", r"on\s+facebook\s+leads", r"^results$"]
A_RESULT_TYPE = [r"^result\s+type$", r"^result\s+indicator$"]
CAPI_FLAGS = {  # CRM columns written by the CAPI script -> stage word
    "booked": [r"^booking\s+done$", r"^capi\s+booking\s+done$", r"^capi\s+booked$", r"^booked$"],
    "visited": [r"^capi\s+visited$"],
    "will visit": [r"^capi\s+will\s+visit$"],
    "interested": [r"^capi\s+interested$"],
}
STAGE_ORDER = ["interested", "details_callback", "will_visit", "visited", "booked"]
STAGE_LABEL = {"interested": "Interested", "details_callback": "Details Shared / Call Back", "will_visit": "Will Visit",
               "visited": "Visited", "booked": "Booking Done"}
CAMPAIGN_DEAD = {"Budget", "Location", "RTMI / Possession", "CP / Broker", "Wrong / Invalid Number", "Requirement Mismatch",
                 "Community Restriction", "M Client", "Not Enquired / Junk", "Not Looking for Property", "Already Booked Elsewhere"}
SCI_RX = re.compile(r"\d[.,]\d+\s*e\s*\+?\d+", re.I)



# ======================================================================================
# 1. LOAD
# ======================================================================================
def classify_tables(files):
    """files: list of (name, tables) newest first. Returns {"crm": [(name, Table)], "ads": [...], "leads": [...]}."""
    found = {"crm": [], "ads": [], "leads": []}
    for name, tables in files:
        for t in tables:
            cols = list(t.columns)
            ncols = [norm(c) for c in cols]
            if any(n in ("form id", "form name", "is organic") for n in ncols) and find_cols(cols, [r"created\s+time"]):
                found["leads"].append((name, t))
            elif find_cols(cols, A_SPEND) and find_cols(cols, A_CAMPAIGN):
                found["ads"].append((name, t))
            elif (find_cols(cols, H_FINAL_STATUS + H_CURRENT_STATUS) or any(re.search(r"\bstatus\b", n) for n in ncols)) \
                    and (find_cols(cols, H_PHONE) or find_cols(cols, H_EMAIL)):
                found["crm"].append((name, t))
    return found


def scan_data(data_dir: Path):
    """Desktop: read every file in data/, keep only the newest file of each kind."""
    files = sorted([f for f in data_dir.glob("*") if f.is_file() and not f.name.startswith(("~$", "."))],
                   key=lambda p: p.stat().st_mtime, reverse=True)
    loaded = []
    for f in files:
        if f.suffix.lower() not in (".csv", ".tsv", ".txt", ".xlsx", ".xlsm"):
            continue
        try:
            loaded.append((f.name, read_tables(f)))
        except Exception:  # noqa: BLE001
            continue
    found = classify_tables(loaded)
    out = {}
    for k, items in found.items():
        newest = items[0][0] if items else None
        out[k] = [it for it in items if it[0] == newest]
    return out


def _txt(v):
    if v is None:
        return ""
    return str(v).strip()


def load_meta_ads(items, checks):
    rows = []
    for name, t in items:
        cols = list(t.columns)
        g = lambda pats: find_cols(cols, pats)  # noqa: E731
        c_res, c_rtype = g(A_RESULTS), g(A_RESULT_TYPE)
        c_camp, c_ad, c_day, c_pl, c_pf = g(A_CAMPAIGN), g(A_AD), g(A_DAY), g(A_PLACEMENT), g(A_PLATFORM)
        c_sp, c_im, c_re = g(A_SPEND), g(A_IMPR), g(A_REACH)
        c_cl = g(A_LINK_CLICKS) or g(A_ALL_CLICKS)
        days = parse_dates(t.col(c_day)) if c_day else [None] * len(t)
        n = 0
        for r, d in zip(t.rows, days):
            camp = _txt(r.get(c_camp))
            if not camp:
                continue
            res = to_num(r.get(c_res)) if c_res else 0.0
            if c_res and norm(c_res) == "results" and c_rtype:  # only count lead results
                rt = _txt(r.get(c_rtype)).lower()
                if not ("lead" in rt or rt == ""):
                    res = 0.0
            rows.append({"campaign": camp, "ad": _txt(r.get(c_ad)) if c_ad else "", "day": d,
                         "placement": _txt(r.get(c_pl)) if c_pl else "", "platform": _txt(r.get(c_pf)) if c_pf else "",
                         "spend": to_num(r.get(c_sp)), "impr": to_num(r.get(c_im)) if c_im else 0.0,
                         "reach": to_num(r.get(c_re)) if c_re else 0.0, "clicks": to_num(r.get(c_cl)) if c_cl else 0.0,
                         "meta_leads": res})
            n += 1
        checks.append(("info", f"Meta Ads data: '{name}' / tab '{t.name}' — {n} rows. Breakdown: "
                       + ", ".join(x for x, ok in [("Ad", bool(c_ad)), ("Day", bool(c_day)), ("Placement", bool(c_pl)), ("Platform", bool(c_pf))] if ok)))
    return rows


def load_meta_leads(items, checks):
    rows = []
    for name, t in items:
        cols = list(t.columns)
        g = lambda pats: find_cols(cols, pats)  # noqa: E731
        dates = parse_dates(t.col(g([r"^created\s+time$"])))
        before = len(rows)
        for r, d in zip(t.rows, dates):
            get = lambda c: _txt(r.get(c)) if c else ""  # noqa: E731
            nm, em, ph = get(g(H_NAME)), get(g(H_EMAIL)), get(g(H_PHONE))
            if is_test_lead(nm, em, ph):
                continue
            plat = norm(get(g(H_PLATFORM)))
            rows.append({"date": d, "campaign": get(g(A_CAMPAIGN) or g(H_CAMPAIGN)), "ad": get(g(A_AD)),
                         "name": norm(nm), "email": clean_email(em), "phone10": clean_phone(ph),
                         "platform": {"fb": "Facebook", "facebook": "Facebook", "ig": "Instagram", "instagram": "Instagram",
                                      "msg": "Messenger", "an": "Audience Network", "wa": "WhatsApp"}.get(plat, plat.title() or "")})
        checks.append(("info", f"Meta lead download: '{name}' — {len(rows) - before} real leads (test leads removed)."))
    return rows


def load_crm(items, clf: Classifier, checks, cfg=None):
    rows = []
    for name, t in items:
        C = detect_crm_columns(t, cfg)
        cols = list(t.columns)
        dvals = t.col(C["date"]) if C["date"] else [None] * len(t)
        dates = parse_dates(dvals) if C["date"] else [None] * len(t)
        flag_cols = {stage: find_cols(cols, pats) for stage, pats in CAPI_FLAGS.items()}
        c_plat = find_cols(cols, H_PLATFORM)
        n_test = n_blank = n_sci = n_utc = n_dup = n_wa = n_capi_bad = n_capi_nobasis = 0
        for r, d, rawd in zip(t.rows, dates, dvals):
            get = lambda c: _txt(r.get(c)) if c else ""  # noqa: E731
            name_, email, phone = get(C["name"]), get(C["email"]), get(C["phone"])
            if is_test_lead(name_, email, phone):
                n_test += 1
                continue
            if not (name_ or email or phone):
                n_blank += 1
                continue
            flags = " ".join(stage for stage, c in flag_cols.items()
                             if c and get(c) and norm(get(c)) not in ("no", "false", "0", "n", "na"))
            res, fus, fb = classify_row(clf, get, C)
            if "booked" in flags:
                alltxt = " | ".join([get(C["final"]), get(C["current"]), *fus, fb])
                with_us = re.search(r"booking\s+done\s+with\s+us|booked\s+with\s+us", alltxt, re.I)
                if clf.has_reason(alltxt, "Already Booked Elsewhere") and not with_us:
                    flags = flags.replace("booked", "").strip()
                    n_capi_bad += 1
                elif not with_us and not (clf.best_quality(norm(alltxt)) or ("", "", 0))[2] == 5:
                    flags = flags.replace("booked", "").strip()
                    n_capi_nobasis += 1
            if flags and res["bucket"] != "Quality":
                res2 = clf.classify(get(C["final"]), get(C["current"]), fus + [flags], fb)
                res = res2 if res2["bucket"] == "Quality" else res
            elif flags and res["bucket"] == "Quality":
                res2 = clf.classify(get(C["final"]), get(C["current"]), fus + [flags], fb)
                res = res2 if res2["rank"] > res["rank"] else res
            if res["bucket"] == "Test":
                n_test += 1
                continue
            if res["bucket"] == "Duplicate":
                n_dup += 1
                continue
            if is_sci_phone(phone):
                n_sci += 1
            if isinstance(rawd, str) and re.search(r"(\+0000|Z)$", rawd.strip()):
                n_utc += 1
            all_raw = [get(C["final"]), get(C["current"]), *fus, fb]
            if any(x and clf.strip_unanswered(x) != x for x in all_raw):
                n_wa += 1
            final_s, curr_s = get(C["final"]), get(C["current"])
            proof = ""
            if res["bucket"] == "Quality":
                named = [(C["final"], final_s), (C["current"], curr_s)] + list(zip(C["extra_status"] + C["followups"], fus)) + \
                        [("CAPI flag", flags), ("Feedback", fb)]
                for label, val in named:
                    q = clf.best_quality(norm(clf.strip_unanswered(val))) if val else None
                    if q and q[0] == res["key"]:
                        proof = f"{str(label).strip()}: {val[:70]}"
                        break
            latest = final_s or curr_s or next((x for x in reversed(fus) if x), "")
            called = bool(final_s or curr_s or any(fus) or fb)
            connected = res["bucket"] == "Quality" or (res["bucket"] == "Dead" and res["label"] != "Wrong / Invalid Number")
            doubt = None
            if res["bucket"] == "Quality":
                doubt = Classifier.doubt(" | ".join([*fus, fb])) or (f"Later marked: {clf.later_dead(latest)}" if clf.later_dead(latest) else None)
            rows.append({
                "tab": t.name, "date": d, "name": name_, "first_name": (name_.split() or [""])[0][:20],
                "email": clean_email(email), "phone10": clean_phone(phone),
                "campaign_raw": get(C["campaign"]), "ad": get(C["ad"]) or "(no ad name)", "platform": norm(get(c_plat)).title() if c_plat else "",
                "latest": latest, "bucket": res["bucket"], "label": res["label"], "key": res["key"], "rank": res["rank"],
                "called": called, "connected": connected, "doubt": doubt,
                "proof": proof, "feedback": " | ".join(x for x in [*fus, fb] if x)[:200],
            })
        tab = t.name
        used = [x for x in [C["final"], C["current"], *C["extra_status"], *C["followups"], *C["feedback"], *[c for c in flag_cols.values() if c]] if x]
        checks.append(("info", f"CRM: '{name}' / tab '{tab}' — statuses read from {len(used)} columns: " + ", ".join(str(x).strip() or "(blank header)" for x in used)))
        if C["coarse"] and C["current"] not in C["coarse"]:
            checks.append(("fixed", f"Tab '{tab}': '{str(C['coarse'][0]).strip()}' only holds a bucket (Active / In Active / Not Interested) — "
                                    f"the detailed status column '{str(C['current']).strip()}' is used as the latest status."))
        if C["notes"]:
            checks.append(("fixed", f"Tab '{tab}': {len(C['notes'])} column(s) without a header contain notes — read as feedback."))
        if n_test:
            checks.append(("fixed", f"{n_test} test / dummy leads removed (tab '{tab}')."))
        if n_dup:
            checks.append(("fixed", f"{n_dup} leads marked 'Duplicate / Repeat' by sales removed from totals (tab '{tab}')."))
        if n_wa:
            checks.append(("fixed", f"{n_wa} leads have notes like 'Ringing … details sent on WhatsApp' — a WhatsApp message to a lead who never picked up is not treated as 'Details Shared' (tab '{tab}')."))
        if n_capi_bad:
            checks.append(("warn", f"⚠️ CAPI: 'Booking Done' was fired for {n_capi_bad} lead(s) whose notes say they bought ELSEWHERE (e.g. 'Already Purchased', "
                                   f"'booked in <other project>') — tab '{tab}'. Not counted as our booking here. Fix the CAPI script: Meta is being told "
                                   "these people are your buyers and will find more like them."))
        if n_capi_nobasis:
            checks.append(("warn", f"CAPI 'Booking Done' is ticked for {n_capi_nobasis} lead(s) but no status or note mentions a booking (tab '{tab}') — "
                                   "not counted as bookings. Please confirm with sales; if they did book, write 'Booking done with us' in the status."))
        if n_blank:
            checks.append(("fixed", f"{n_blank} empty rows ignored (no name / phone / email) in tab '{tab}'."))
        if n_sci:
            checks.append(("warn", f"{n_sci} phone numbers are in scientific format (e.g. 9.20E+11) in tab '{tab}' — digits are lost, "
                                   "so phone was NOT used for duplicates (email used instead). Fix: download the sheet as .xlsx, "
                                   "and set the phone column to Format → Number → Plain text."))
        if n_utc:
            checks.append(("fixed", f"{n_utc} CRM dates were in UTC (+0000) — converted to IST, so late-evening leads land on the right day."))
        if not C["campaign"]:
            checks.append(("warn", f"Tab '{tab}' has no campaign column — per-campaign numbers not possible for these leads."))
    return rows


def dedupe(crm: list, checks):
    order = {"Quality": 4, "Follow-up": 2, "Unmapped": 1, "Not Called": 1, "Dead": 0}
    FAR = dt.datetime.max
    ranked = sorted(crm, key=lambda r: (-order.get(r["bucket"], 0), -r["rank"], r["date"] or FAR))
    seen_p, seen_e, keep, dup_notes = set(), set(), [], []
    for r in ranked:
        p, e = r["phone10"], r["email"]
        if (p and p in seen_p) or (e and e in seen_e):
            dup_notes.append(f"{r['first_name']} ({r['date']:%d %b}) = same {'phone' if p and p in seen_p else 'email'} as an earlier lead"
                             if r["date"] else f"{r['first_name']} duplicate")
            continue
        keep.append(r)
        if p:
            seen_p.add(p)
        if e:
            seen_e.add(e)
    keep.sort(key=lambda r: r["date"] or FAR)
    n = len(crm) - len(keep)
    checks.append(("fixed" if n else "info", f"Duplicates: {n} removed — matched only on a real 10-digit phone or the same email "
                                             "(never on 9.20E+11-style numbers)." + (" " + "; ".join(dup_notes[:8]) if dup_notes else "")))
    return keep


def attach_platform(crm: list, mleads: list, checks):
    if not mleads or not crm:
        return crm
    by_e = {r["email"]: r["platform"] for r in mleads if r["email"]}
    by_p = {r["phone10"]: r["platform"] for r in mleads if r["phone10"]}
    by_n = defaultdict(list)
    for r in mleads:
        if r["name"]:
            by_n[r["name"]].append((r["date"], r["platform"]))
    matched = 0
    for r in crm:
        p = r["platform"] or by_e.get(r["email"]) or by_p.get(r["phone10"])
        if not p and norm(r["name"]) in by_n:
            cands = [pl for d, pl in by_n[norm(r["name"])] if r["date"] is None or d is None or abs((d - r["date"]).days) <= 2]
            p = cands[0] if cands else ""
        matched += bool(p)
        r["platform"] = p or "Unknown"
    checks.append(("info", f"Platform (FB / IG) found for {matched} of {len(crm)} CRM leads by matching email / phone / name with the Meta lead download."))
    return crm


# ======================================================================================
# 2. METRICS
# ======================================================================================
def lead_stats(df: list) -> dict:
    n = len(df)
    k = Counter(r["key"] for r in df)
    b = Counter(r["bucket"] for r in df)
    quality = b["Quality"]
    called = sum(bool(r["called"]) for r in df)
    connected = sum(bool(r["connected"]) for r in df)
    will_plus = sum(r["rank"] >= 3 for r in df)
    visited_plus = sum(r["rank"] >= 4 for r in df)
    booked = sum(r["rank"] >= 5 for r in df)
    followup = b["Follow-up"]
    doubtful = sum(1 for r in df if r["doubt"])
    return {
        "leads": n, "quality": quality, "quality_pct": pct(quality, n),
        "interested": k["interested"], "details_callback": k["details_callback"], "will_visit": k["will_visit"],
        "visited": k["visited"], "booked": booked, "site_visits": visited_plus, "visit_ratio_pct": pct(visited_plus, n),
        "will_visit_plus": will_plus, "called": called, "connected": connected, "not_called": b["Not Called"],
        "followup": followup, "not_reached_pct": pct(followup, called), "dead": b["Dead"], "unmapped": b["Unmapped"],
        "doubtful_quality": doubtful, "strict_quality": quality - doubtful, "strict_quality_pct": pct(quality - doubtful, n),
        "dead_reasons": dict(Counter(r["label"] for r in df if r["bucket"] == "Dead").most_common()),
        "quality_mix": {STAGE_LABEL[s]: k[s] for s in reversed(STAGE_ORDER)},
    }


def money_stats(m: dict, spend: float) -> dict:
    m = dict(m)
    if not spend:
        m.update({"spend": 0.0, "cpl": None, "cost_per_quality": None, "cost_per_visit": None, "cost_per_booking": None,
                  "visit_to_booking_pct": pct(m["booked"], m["site_visits"])})
        return m
    m.update({"spend": round(float(spend), 2), "cpl": money(spend, m["leads"]), "cost_per_quality": money(spend, m["quality"]),
              "cost_per_visit": money(spend, m["site_visits"]), "cost_per_booking": money(spend, m["booked"]),
              "visit_to_booking_pct": pct(m["booked"], m["site_visits"])})
    return m


def meta_stats(rows) -> dict:
    if not rows:
        return {"impr": 0, "clicks": 0, "meta_leads": 0, "ctr_pct": None, "cpm": None, "cpc": None, "meta_cpl": None, "click_to_lead_pct": None}
    s = sum(r["spend"] for r in rows)
    i = sum(r["impr"] for r in rows)
    c = sum(r["clicks"] for r in rows)
    l = sum(r["meta_leads"] for r in rows)  # noqa: E741
    return {"impr": int(i), "clicks": int(c), "meta_leads": int(l), "ctr_pct": round(c * 100 / i, 2) if i else None,
            "cpm": round(s * 1000 / i) if i else None, "cpc": round(s / c) if c else None, "meta_cpl": money(s, l),
            "click_to_lead_pct": round(l * 100 / c, 1) if c else None}



def journey(m: dict, bench: dict) -> dict:
    steps = [("Leads received", m["leads"], None, None),
             ("Called by sales", m["called"], "called", "Sales"),
             ("Spoke with lead", m["connected"], "connected", "Sales"),
             ("Quality lead", m["quality"], "quality", "Campaign"),
             ("Will Visit (or further)", m["will_visit_plus"], "will_visit", "Sales"),
             ("Site visited", m["site_visits"], "visited", "Sales"),
             ("Booking done", m["booked"], "booked", "Sales")]
    wrong = m["dead_reasons"].get("Wrong / Invalid Number", 0)
    dead_fit = [(k, v) for k, v in m["dead_reasons"].items() if k != "Wrong / Invalid Number"]
    lost = {  # where the leads went between the previous stage and this one
        1: [("Not called yet", m["not_called"])],
        2: [("Ringing / Busy / Not reachable", m["followup"]), ("Wrong / invalid number", wrong), ("Status not clear", m["unmapped"])],
        3: [(f"Dead — {k}", v) for k, v in dead_fit],
        4: [("Stuck at Details Shared / Call Back / Interested", m["details_callback"] + m["interested"])],
        5: [("Planned a visit, not visited yet", m["will_visit"])],
        6: [("Visited, not booked yet", m["site_visits"] - m["booked"])],
    }
    out, worst = [], None
    for i, (label, val, key, owner) in enumerate(steps):
        prev = steps[i - 1][1] if i else None
        conv = pct(val, prev) if prev else None
        row = {"stage": label, "leads": val, "pct_of_leads": pct(val, m["leads"]), "step_conv_pct": conv,
               "benchmark_pct": bench.get(key) if key else None, "owner": owner,
               "lost": [{"reason": r, "leads": int(n), "pct_of_leads": pct(n, m["leads"])} for r, n in lost.get(i, []) if n]}
        if key and prev and prev >= 3 and bench.get(key):
            gap = conv / bench[key]
            row["vs_benchmark"] = round(gap, 2)
            if worst is None or gap < worst[0]:
                worst = (gap, i)
        out.append(row)
    brk = None
    if worst and worst[0] < 1:
        r = out[worst[1]]
        prev = out[worst[1] - 1]
        brk = {"from": prev["stage"], "to": r["stage"], "conv_pct": r["step_conv_pct"], "benchmark_pct": r["benchmark_pct"],
               "lost": prev["leads"] - r["leads"], "owner": r["owner"]}
    return {"steps": out, "break": brk}


def diagnose(m: dict, meta: dict, df: list, today, cfg: dict, acct: dict) -> dict:
    """Campaign vs Sales problem attribution + good / bad / solution in simple English."""
    th = cfg["thresholds"]
    n = m["leads"] or 1
    sales, camp, good = [], [], []
    fresh_cut = today - dt.timedelta(days=cfg.get("fresh_lead_days", 2))
    old = [r for r in df if r["date"] is not None and r["date"] < fresh_cut]

    # ---- sales side
    nc_old = sum(r["bucket"] == "Not Called" for r in old)
    if nc_old and nc_old / n >= 0.05:
        sales.append({"issue": f"{nc_old} leads older than {cfg.get('fresh_lead_days', 2)} days were never called.", "affected": nc_old,
                      "fix": f"Sales: call these {nc_old} leads today. Rule: every new lead called within 5 minutes of the WhatsApp alert."})
    if m["called"] and m["not_reached_pct"] >= 35:
        sales.append({"issue": f"{m['followup']} leads ({m['not_reached_pct']}% of called) are still ringing / busy / not reachable.",
                      "affected": m["followup"],
                      "fix": "Sales: 3 attempts at different times (morning, evening, next day) + WhatsApp message with brochure after the 1st missed call."})
    stuck = [r for r in old if r["bucket"] == "Quality" and r["rank"] <= 2]
    if len(stuck) and m["quality"] and len(stuck) / m["quality"] >= 0.5 and len(stuck) >= 2:
        sales.append({"issue": f"{len(stuck)} of {m['quality']} quality leads are stuck at Details Shared / Call Back — no visit fixed yet.",
                      "affected": len(stuck),
                      "fix": "Sales: offer a fixed slot ('Saturday 11 am or Sunday 4 pm?'), send a walkthrough video, follow up in 24 hours."})
    if m["will_visit_plus"] >= 3 and pct(m["site_visits"], m["will_visit_plus"]) < 40:
        sales.append({"issue": f"{m['will_visit_plus']} leads said they will visit, only {m['site_visits']} actually visited.",
                      "affected": m["will_visit_plus"] - m["site_visits"],
                      "fix": "Sales: confirm the visit one day before + reminder on the morning of the visit; offer a cab / pickup for weekday visits."})

    # ---- campaign side
    dead_c = {k: v for k, v in m["dead_reasons"].items() if k in CAMPAIGN_DEAD and k != "Wrong / Invalid Number"}
    dead_c_n = sum(dead_c.values())
    if m["connected"] and dead_c_n / max(m["connected"], 1) >= 0.4 and dead_c_n >= 2:
        top = ", ".join(f"{k} ({v})" for k, v in list(dead_c.items())[:3])
        camp.append({"issue": f"{dead_c_n} of {m['connected']} leads we spoke to were the wrong fit — {top}.", "affected": dead_c_n,
                     "fix": fix_for_reasons(dead_c)})
    junk = m["dead_reasons"].get("Wrong / Invalid Number", 0) + m["dead_reasons"].get("Not Enquired / Junk", 0)
    if junk and junk / n >= 0.12:
        camp.append({"issue": f"{junk} leads were fake / wrong numbers / 'never enquired'.", "affected": junk,
                     "fix": "Media: switch the form to Higher Intent (review screen) and turn on phone OTP verification."})
    if m["quality"] and m["doubtful_quality"] / m["quality"] >= 0.3:
        camp.append({"issue": f"{m['doubtful_quality']} of {m['quality']} 'quality' leads look doubtful from the sales feedback.",
                     "affected": m["doubtful_quality"],
                     "fix": "Send CAPI signals (Lead_Disqualified for junk, Will Visit / Visited for good leads) so Meta learns who is real."})
    if m["leads"] >= th["low_data_leads"] and m["quality_pct"] < th["review_quality_pct"]:
        camp.append({"issue": f"Quality is only {m['quality_pct']}% (target {th['scale_quality_pct']}%).", "affected": m["leads"] - m["quality"],
                     "fix": "Media: new creatives with price + location in the first line; tighten the form (budget + timeline questions)."})
    if meta.get("ctr_pct") is not None and meta["ctr_pct"] < 0.7 and meta["impr"] >= 5000:
        camp.append({"issue": f"Link CTR is low ({meta['ctr_pct']}%) — the hook is not stopping people.", "affected": 0,
                     "fix": "Media: 3-5 genuinely different creatives (video walkthrough, price-led static, location map) — Andromeda rewards creative variety, not copies."})
    if meta.get("meta_leads") and abs(meta["meta_leads"] - m["leads"]) > max(3, 0.15 * meta["meta_leads"]):
        camp.append({"issue": f"Meta counted {meta['meta_leads']} leads but the CRM has {m['leads']}.", "affected": abs(meta["meta_leads"] - m["leads"]),
                     "fix": "Check the Meta → sheet sync (Leads Center / Pabbly) for missing or extra leads before judging CPL."})
    target_cpl = cfg.get("target_cpl") or (acct.get("cpl") * 1.3 if acct.get("cpl") else None)
    if target_cpl and m.get("cpl") and m["cpl"] > target_cpl and m["leads"] >= 3:
        camp.append({"issue": f"CPL {fmt_inr(m['cpl'])} is above the limit ({fmt_inr(target_cpl)}).", "affected": 0,
                     "fix": "Media: move budget to the best ad / ad set; keep fewer, broader ad sets (Andromeda) and cut ads with 0 quality after 2× CPL spend."})

    # ---- good
    if m["leads"] and m["quality_pct"] >= th["scale_quality_pct"]:
        good.append(f"Quality is strong: {m['quality']} of {m['leads']} leads ({m['quality_pct']}%) are quality.")
    if acct.get("cost_per_quality") and m.get("cost_per_quality") and m["cost_per_quality"] < acct["cost_per_quality"]:
        good.append(f"Cheaper quality leads than the account average ({fmt_inr(m['cost_per_quality'])} vs {fmt_inr(acct['cost_per_quality'])}).")
    if m["site_visits"]:
        good.append(f"{m['site_visits']} site visit(s) done — visit ratio {m['visit_ratio_pct']}%.")
    if meta.get("ctr_pct") and meta["ctr_pct"] >= 1.0:
        good.append(f"Ads get attention: link CTR {meta['ctr_pct']}%.")
    if m["will_visit"]:
        good.append(f"{m['will_visit']} lead(s) have planned a visit.")

    s_aff, c_aff = sum(x["affected"] for x in sales), sum(x["affected"] for x in camp)
    if not sales and not camp:
        owner = "Healthy"
    elif sales and camp:
        owner = "Both — mainly Sales" if s_aff > c_aff else "Both — mainly Campaign"
    else:
        owner = "Sales" if sales else "Campaign"
    return {"owner": owner, "sales_issues": sales, "campaign_issues": camp, "good": good}


def fix_for_reasons(dead: dict) -> str:
    tips = {
        "Budget": "put the starting price in the first line of the ad and use real price ranges in the budget question",
        "Location": "show the exact location / landmark in the creative and headline; keep targeting to ~8-10 km around the project",
        "RTMI / Possession": "show the possession date clearly in the ad so ready-to-move buyers skip it",
        "Not Looking for Property": "use Higher Intent form + a timeline question ('When do you plan to buy?') to filter casual clickers",
        "Plan on Hold / Dropped": "ask 'When do you plan to buy?' in the form and nurture 'later' buyers on WhatsApp instead of calling them as hot leads",
        "CP / Broker": "add 'Are you a channel partner?' to the form and exclude broker-heavy audiences",
        "Requirement Mismatch": "state the configuration and carpet area clearly (e.g. '3 BHK, 1,250 sq ft')",
        "Community Restriction": "mention the community / eligibility rule in the ad so ineligible buyers don't apply",
        "M Client": "ask for budget + timeline in the form to filter low-intent leads",
        "Wrong / Invalid Number": "use Higher Intent form + OTP",
        "Not Enquired / Junk": "use Higher Intent form + OTP",
        "Already Booked Elsewhere": "speed up first call — these buyers were comparing projects",
    }
    parts = [tips[k] for k in list(dead)[:2] if k in tips]
    return "Media: " + "; ".join(parts) + "." if parts else "Media: tighten the form and targeting."


def campaign_verdict(m, meta, th):
    if m["leads"] < th["low_data_leads"]:
        bits = []
        if meta.get("ctr_pct") is not None:
            bits.append(f"CTR {meta['ctr_pct']}%")
        if meta.get("cpm"):
            bits.append(f"CPM {fmt_inr(meta['cpm'])}")
        if m.get("cpl"):
            bits.append(f"CPL {fmt_inr(m['cpl'])}")
        return "LOW DATA", f"Only {m['leads']} leads (need {th['low_data_leads']}) — judge on " + (", ".join(bits) or "CTR / CPM / CPL") + " for now."
    v, _ = verdict(m, th)
    reasons = {
        "SCALE": f"Quality {m['quality_pct']}% and visit ratio {m['visit_ratio_pct']}% are both on target — raise budget 15-20% every 3-4 days.",
        "OPTIMIZE": (f"Quality {m['quality_pct']}% is good but visits are low ({m['visit_ratio_pct']}% vs {th['target_visit_ratio_pct']}%) — push visits before adding budget."
                     if m["quality_pct"] >= th["scale_quality_pct"] else f"Quality {m['quality_pct']}% — tighten form, placements and weak ads."),
        "REVIEW ADS + AUDIENCE": f"Quality {m['quality_pct']}% — review creatives and audience.",
        "PAUSE / FIX": f"Quality {m['quality_pct']}% is below {th['review_quality_pct']}% — pause or fix form / audience / offer.",
    }
    return v, reasons.get(v, "")


# --------------------------------------------------------------------------------------
# ads, angles, fatigue, placements
# --------------------------------------------------------------------------------------
ANGLE_RULES = [
    (r"price|offer|emi|cr\b|lakh|payment|discount|deal", "Price / Offer"),
    (r"location|map|metro|station|connect|landmark|near", "Location / Connectivity"),
    (r"mandir|temple|jain|community|derasar", "Community / Landmark"),
    (r"building|elevation|tower|facade|exterior|night|aerial|drone", "Building / Elevation"),
    (r"jog|garden|park|sitting|lifestyle|kid|family|pool|gym|club|amenit|rooftop|terrace|lounge|hall|deck", "Lifestyle / Amenities"),
    (r"interior|sample|flat|living|bedroom|kitchen|walkthrough|tour", "Inside the Flat"),
]


def ad_angle(ad: str, overrides: dict) -> tuple[str, str]:
    n = norm(ad)
    fmt = "Video" if re.search(r"video|vid|reel|walkthrough|tour", n) else ("Carousel" if "carousel" in n else "Image")
    for k, v in overrides.items():
        if not k.startswith("_") and norm(k) == n:
            return v, fmt
    for rx, label in ANGLE_RULES:
        if re.search(rx, n):
            return label, fmt
    return ("Video (angle not in name)" if fmt == "Video" else "Other / not clear from name"), fmt



def fatigue(rows: list, end_day, fcfg: dict) -> dict:
    """Last N days vs previous N days for one ad (Meta daily rows)."""
    if not rows or all(r["day"] is None for r in rows):
        return {"status": "No daily data", "score": None, "signals": []}
    N = fcfg["window_days"]
    end = day(end_day)
    rec = [r for r in rows if r["day"] is not None and end - dt.timedelta(days=N) < r["day"] <= end]
    prv = [r for r in rows if r["day"] is not None and end - dt.timedelta(days=2 * N) < r["day"] <= end - dt.timedelta(days=N)]

    def agg(x):
        s, i, c, r, l = (sum(z[k] for z in x) for k in ("spend", "impr", "clicks", "reach", "meta_leads"))  # noqa: E741
        return {"spend": s, "impr": i, "ctr": c / i * 100 if i else None, "cpm": s / i * 1000 if i else None,
                "cpl": s / l if l else None, "leads": l, "reach_k": r / i * 1000 if i else None}
    a, b = agg(rec), agg(prv)
    if a["spend"] <= 0:
        return {"status": "Not delivering", "score": None, "signals": [f"No spend in last {N} days"], "recent": a, "previous": b}
    if a["spend"] < fcfg["min_spend"] or a["impr"] < fcfg["min_impr"]:
        return {"status": "Too little data", "score": None,
                "signals": [f"Last {N} days: {fmt_inr(a['spend'])}, {int(a['impr']):,} impressions (needs {fmt_inr(fcfg['min_spend'])} + {fcfg['min_impr']:,})"],
                "recent": a, "previous": b}
    if b["impr"] < fcfg["min_impr"]:
        return {"status": "New (no previous week)", "score": None, "signals": ["Not enough previous-week data to compare"], "recent": a, "previous": b}
    score, sig = 0, []

    def add(weight, change, warn, text):
        nonlocal score
        if change is None:
            return
        if change >= warn:
            score += weight
            sig.append(text)
        elif change >= warn / 2:
            score += weight / 2
            sig.append(text + " (early)")
    ch = lambda new, old, up=True: (((new - old) / old * 100) if up else ((old - new) / old * 100)) if (new is not None and old) else None  # noqa: E731
    d_ctr = ch(a["ctr"], b["ctr"], up=False)
    add(fcfg["w_ctr"], d_ctr, fcfg["ctr_warn"], f"CTR down {d_ctr:.0f}%" if d_ctr else "")
    if a["cpl"] is None and b["cpl"]:
        if a["spend"] >= 1.5 * b["cpl"]:
            score += fcfg["w_cpl"]
            sig.append(f"No leads in last {N} days after {fmt_inr(a['spend'])} spend")
    else:
        d_cpl = ch(a["cpl"], b["cpl"])
        add(fcfg["w_cpl"], d_cpl, fcfg["cpl_warn"], f"CPL up {d_cpl:.0f}%" if d_cpl else "")
    d_cpm = ch(a["cpm"], b["cpm"])
    add(fcfg["w_cpm"], d_cpm, fcfg["cpm_warn"], f"CPM up {d_cpm:.0f}%" if d_cpm else "")
    d_reach = ch(a["reach_k"], b["reach_k"], up=False)
    add(fcfg["w_reach"], d_reach, fcfg["reach_warn"], f"New-people reach down {d_reach:.0f}%" if d_reach else "")
    status = "Fatigued — refresh" if score >= fcfg["fatigue_score"] else ("Watch" if score >= fcfg["watch_score"] else "Healthy")
    return {"status": status, "score": round(score), "signals": [s for s in sig if s], "recent": a, "previous": b}


def platform_of_placement(p: str) -> str:
    n = norm(p)
    for key, label in [("instagram", "Instagram"), ("explore", "Instagram"), ("facebook", "Facebook"), ("marketplace", "Facebook"),
                       ("in stream", "Facebook"), ("video feeds", "Facebook"), ("right column", "Facebook"), ("messenger", "Messenger"),
                       ("audience network", "Audience Network"), ("threads", "Threads"), ("whatsapp", "WhatsApp")]:
        if key in n:
            return label
    return "Mixed (FB + IG)"


# ======================================================================================
# 3. BUILD
# ======================================================================================
def build(client: str, d_from=None, d_to=None, insights_path=None) -> dict:
    """Desktop entry: reads clients/<client>/data/."""
    cdir = CLIENTS_DIR / client
    if not cdir.exists():
        sys.exit(f"Client folder not found: {cdir}")
    cfg = load_config(cdir)
    src = scan_data(cdir / "data")
    if not src["crm"]:
        sys.exit(f"No CRM sheet found in {cdir / 'data'} (needs a status column + name/phone/email).")
    insights = None
    extra = []
    if insights_path:
        ip = Path(insights_path)
        if ip.exists():
            insights = json.loads(ip.read_text(encoding="utf-8"))
        else:
            extra.append(("warn", f"Insights file not found: {ip}"))
    return build_report(src, cfg, client, d_from, d_to, insights, extra)


def build_from_uploads(uploads, cfg: dict, client: str = "Client", d_from=None, d_to=None):
    """Website entry: uploads = list of (filename, bytes). Every uploaded file is used."""
    loaded, checks = [], []
    for fname, data in uploads:
        try:
            loaded.append((fname, read_tables(fname, data)))
        except Exception as e:  # noqa: BLE001
            checks.append(("warn", f"Could not read '{fname}': {e}"))
    src = classify_tables(loaded)
    if not src["crm"]:
        raise ValueError("No CRM sheet found in the uploaded files (it needs a status column + name / phone / email).")
    return build_report(src, cfg, client, d_from, d_to, None, checks)


def build_report(src, cfg, client, d_from=None, d_to=None, insights=None, pre_checks=None) -> dict:
    th, bench, fcfg = cfg["thresholds"], cfg["journey_benchmarks"], cfg["fatigue"]
    checks: list[tuple[str, str]] = list(pre_checks or [])
    clf = Classifier(cfg)
    crm = load_crm(src["crm"], clf, checks, cfg)
    ads = load_meta_ads(src["ads"], checks)
    mleads = load_meta_leads(src["leads"], checks)
    if not ads:
        checks.append(("warn", "No Meta Ads export found — spend, CPL and cost numbers are blank. Add the Ads Manager CSV."))

    # period filter (IST dates)
    if d_from or d_to:
        lo = dt.datetime.combine(d_from, dt.time()) if d_from else dt.datetime.min
        hi = dt.datetime.combine(d_to, dt.time()) + dt.timedelta(days=1) if d_to else dt.datetime.max
        inr = lambda d: d is not None and lo <= d < hi  # noqa: E731
        nod = sum(r["date"] is None for r in crm)
        crm = [r for r in crm if inr(r["date"])]
        if nod:
            checks.append(("warn", f"{nod} CRM leads have no valid date and were left out of the date filter."))
        if ads and any(r["day"] is not None for r in ads):
            ads = [r for r in ads if inr(r["day"])]
        if mleads:
            mleads = [r for r in mleads if inr(r["date"])]
    crm = dedupe(crm, checks)
    crm = attach_platform(crm, mleads, checks)
    if not crm:
        raise ValueError("No leads in the selected period.")

    # campaign matching CRM -> Meta
    ads_camps = list(dict.fromkeys(r["campaign"] for r in ads))
    mapping, fuzzy = build_campaign_matcher(list(dict.fromkeys(r["campaign_raw"] for r in crm)), ads_camps, cfg.get("campaign_aliases"))
    for r in crm:
        r["ckey"] = mapping.get(r["campaign_raw"], "") or ""
    for raw, key in fuzzy:
        checks.append(("fixed", f"Campaign name matched: CRM '{raw}' → Meta '{next(c for c in ads_camps if norm(c) == key)}'."))
    for r in ads:
        r["ckey"] = norm(r["campaign"])
    display = {}
    display.update({norm(c): c for c in ads_camps})
    for raw, key in mapping.items():
        display.setdefault(key, raw or "(no campaign name)")
    display[""] = "(no campaign name)"
    for r in mleads:
        r["ckey"] = mapping.get(r["campaign"]) or norm(r["campaign"])

    has_days = any(r["day"] is not None for r in ads)
    cand = [r["date"] for r in crm if r["date"]] + ([r["day"] for r in ads if r["day"]] if ads else [])
    today = day(max(cand)) if cand else day(dt.datetime.now())
    start = day(min(cand)) if cand else today

    # ---------------- overall
    total_spend = float(sum(r["spend"] for r in ads)) if ads else 0.0
    acct = money_stats(lead_stats(crm), total_spend)
    acct_meta = meta_stats(ads)
    if mleads:
        acct_meta["meta_leads"] = len(mleads)
    acct.update({"meta": acct_meta})
    acct["verdict"], acct["verdict_reason"] = campaign_verdict(acct, acct_meta, th)
    acct["journey"] = journey(acct, bench)
    acct["diagnosis"] = diagnose(acct, acct_meta, crm, today, cfg, {})

    # ---------------- period compare (last N vs previous N)
    N = int(cfg.get("compare_days", 7))
    end = today + dt.timedelta(days=1)
    windows = {"last": (end - dt.timedelta(days=N), end), "prev": (end - dt.timedelta(days=2 * N), end - dt.timedelta(days=N))}
    compare = {}
    for wname, (a, b) in windows.items():
        sub = [r for r in crm if r["date"] is not None and a <= r["date"] < b]
        wads = [r for r in ads if r["day"] is not None and a <= r["day"] < b]
        sp = float(sum(r["spend"] for r in wads)) if (ads and has_days) else None
        ms = money_stats(lead_stats(sub), sp or 0.0)
        ms["spend"] = sp
        ms["meta"] = meta_stats(wads) if (ads and has_days) else meta_stats(None)
        ms["from"], ms["to"] = a.strftime("%d %b"), (b - dt.timedelta(days=1)).strftime("%d %b")
        ms["pending_calls"] = sum((not r["called"]) + (r["bucket"] == "Follow-up") for r in sub)
        compare[wname] = ms

    # ---------------- trend
    daily = []
    span = (today - start).days + 1
    step = 1 if span <= 45 else 7                          # long periods -> weekly bars
    d = start
    while d <= today:
        e = d + dt.timedelta(days=step)
        sub = [r for r in crm if r["date"] is not None and d <= r["date"] < e]
        sp = float(sum(r["spend"] for r in ads if r["day"] is not None and d <= r["day"] < e)) if (ads and has_days) else None
        daily.append({"date": d.strftime("%d %b") if step == 1 else f"wk {d:%d %b}", "leads": len(sub),
                      "quality": sum(r["bucket"] == "Quality" for r in sub), "spend": sp})
        d = e

    # ---------------- campaigns
    campaigns = []
    by_ck = defaultdict(list)
    for r in crm:
        by_ck[r["ckey"]].append(r)
    ads_by_ck = defaultdict(list)
    for r in ads:
        ads_by_ck[r["ckey"]].append(r)
    mleads_ck = Counter(r["ckey"] for r in mleads)
    keys = sorted(set(by_ck) | set(ads_by_ck))
    for key in keys:
        cl, ca = by_ck.get(key, []), ads_by_ck.get(key, [])
        spend = float(sum(r["spend"] for r in ca)) if ca else 0.0
        if not cl and spend <= 0:
            continue
        m = money_stats(lead_stats(cl), spend)
        meta = meta_stats(ca)
        if mleads:
            meta["meta_leads"] = mleads_ck.get(key, 0)
        m["meta"] = meta
        m["campaign"] = display.get(key, key)
        m["key"] = key
        m["verdict"], m["verdict_reason"] = campaign_verdict(m, meta, th)
        m["journey"] = journey(m, bench)
        m["diagnosis"] = diagnose(m, meta, cl, today, cfg, acct)
        m["calls_pending"] = sum((not r["called"]) + (r["bucket"] == "Follow-up") for r in cl)
        m["best_ad"] = best_by(cl, "ad", ca, "ad")
        m["best_platform"] = best_by([r for r in cl if r["platform"] != "Unknown"], "platform", None, None)
        m["best_placement"] = best_placement(ca)
        campaigns.append(m)
    campaigns.sort(key=lambda x: (-x["quality"], -x["spend"]))
    for key in set(ads_by_ck) - set(by_ck):
        sp = sum(r["spend"] for r in ads_by_ck[key])
        if sp > 0:
            checks.append(("warn", f"Meta campaign '{display.get(key, key)}' spent {fmt_inr(sp)} but has 0 leads in the CRM — name mismatch or leads not reaching the sheet."))

    # ---------------- ads + fatigue + angles
    ads_out = []
    overrides = cfg.get("ad_angles", {})
    for r in crm:
        r["adkey"] = norm(r["ad"])
    for r in ads:
        r["adkey"] = norm(r["ad"])
    grp_crm, grp_ads = defaultdict(list), defaultdict(list)
    for r in crm:
        grp_crm[(r["ckey"], r["adkey"])].append(r)
    if any(r["ad"] for r in ads):
        for r in ads:
            grp_ads[(r["ckey"], r["adkey"])].append(r)
    end_day = max((r["day"] for r in ads if r["day"] is not None), default=None)
    for ck, ak in sorted(set(grp_crm) | set(grp_ads)):
        cl = grp_crm.get((ck, ak), [])
        ca = [r for r in ads if r["ckey"] == ck and r["adkey"] == ak] if ads else []
        spend = float(sum(r["spend"] for r in ca)) if ca else 0.0
        if not cl and spend <= 0:
            continue
        name = ca[0]["ad"] if ca else (cl[0]["ad"] if cl else ak)
        m = money_stats(lead_stats(cl), spend)
        meta = meta_stats(ca)
        angle, fmt = ad_angle(name, overrides)
        f = fatigue(ca, end_day, fcfg) if end_day is not None and ca else {"status": "No daily data", "score": None, "signals": []}
        ads_out.append({"ad": name, "campaign": display.get(ck, ck), "angle": angle, "format": fmt, **m, "meta": meta,
                        "fatigue": {k: v for k, v in f.items() if k in ("status", "score", "signals")},
                        "verdict": campaign_verdict(m, meta, th)[0]})
    ads_out.sort(key=lambda x: (-x["quality"], -x["spend"]))
    angles = []
    for label in sorted({a["angle"] for a in ads_out}):
        grp = [a for a in ads_out if a["angle"] == label]
        L, Q, S, V = (sum(a[k] for a in grp) for k in ("leads", "quality", "spend", "site_visits"))
        angles.append({"angle": label, "ads": len(grp), "spend": S, "leads": L, "quality": Q, "quality_pct": pct(Q, L),
                       "cpl": money(S, L), "cost_per_quality": money(S, Q), "site_visits": V})
    angles.sort(key=lambda x: (-x["quality"], x["cost_per_quality"] or 1e12))
    formats = []
    for fm in sorted({a["format"] for a in ads_out}):
        grp = [a for a in ads_out if a["format"] == fm]
        L, Q, S = (sum(a[k] for a in grp) for k in ("leads", "quality", "spend"))
        formats.append({"format": fm, "spend": S, "leads": L, "quality": Q, "quality_pct": pct(Q, L), "cost_per_quality": money(S, Q)})

    # ---------------- placements (Meta) + platform quality (CRM)
    placements, placement_groups = [], []
    if any(r["placement"] for r in ads):
        by_pl = defaultdict(list)
        for r in ads:
            by_pl[r["placement"]].append(r)
        for p in sorted(by_pl):
            if not p:
                continue
            g = by_pl[p]
            ms = meta_stats(g)
            sp = sum(r["spend"] for r in g)
            placements.append({"placement": p, "platform": platform_of_placement(p), "spend": round(float(sp)), **ms,
                               "share_spend_pct": pct(sp, total_spend)})
        placements.sort(key=lambda x: -x["spend"])
        est = estimate_placement_quality(crm, ads)
        for p in placements:
            e = est["by_placement"].get(p["placement"], {"leads": 0.0, "quality": 0.0, "visits": 0.0})
            p.update({"est_leads": round(e["leads"], 1), "est_quality": round(e["quality"], 1),
                      "est_quality_pct": pct(e["quality"], e["leads"]) if e["leads"] else None,
                      "est_cost_per_quality": money(p["spend"], e["quality"]) if e["quality"] >= 0.5 else None,
                      "group": placement_group(p["placement"])})
            p["decision"], p["decision_why"] = placement_decision(p, acct, acct_meta)
        for grp in ["Reels", "Feed", "Stories", "Other"]:
            g = [p for p in placements if p["group"] == grp]
            if not g:
                continue
            S, C, I, ML = (sum(x[k] for x in g) for k in ("spend", "clicks", "impr", "meta_leads"))
            EL, EQ = sum(x["est_leads"] for x in g), sum(x["est_quality"] for x in g)
            placement_groups.append({"group": grp, "placements": len(g), "spend": S, "share_spend_pct": pct(S, total_spend),
                                     "clicks": C, "ctr_pct": round(C * 100 / I, 2) if I else None, "meta_leads": ML,
                                     "click_to_lead_pct": round(ML * 100 / C, 1) if C else None, "meta_cpl": money(S, ML),
                                     "est_leads": round(EL, 1), "est_quality": round(EQ, 1), "est_quality_pct": pct(EQ, EL) if EL else None,
                                     "est_cost_per_quality": money(S, EQ) if EQ >= 0.5 else None})
        if est["unallocated"]:
            checks.append(("info", f"Placement quality estimate: {est['unallocated']} CRM leads could not be linked to a placement "
                                   "(ad name not found in the Meta export) — left out of the placement estimate."))
    platforms = []
    by_pf = defaultdict(list)
    for r in crm:
        by_pf[r["platform"]].append(r)
    for p in sorted(by_pf):
        ls = lead_stats(by_pf[p])
        sp = sum(x["spend"] for x in placements if x["platform"] == p) if placements else None
        platforms.append({"platform": p, "leads": ls["leads"], "quality": ls["quality"], "quality_pct": ls["quality_pct"],
                          "site_visits": ls["site_visits"], "doubtful": ls["doubtful_quality"],
                          "spend": sp or None, "cost_per_quality": money(sp, ls["quality"]) if sp else None})
    platforms.sort(key=lambda x: -x["leads"])
    mixed = sum(x["spend"] for x in placements if x["platform"].startswith("Mixed"))
    if mixed:
        checks.append(("warn", f"{fmt_inr(mixed)} of spend is on a combined 'Feed' placement (FB + IG mixed) — platform cost per quality lead "
                               "is partial. Export with Breakdown → By delivery → Platform for exact FB vs IG spend."))
    checks.append(("info", "Placement-level QUALITY is not possible from Meta data (the lead download has platform, not placement). "
                           "Placement table shows Meta-reported leads; quality is shown by platform. For a true test, run a Reels-only vs Feed-only ad set."))

    # ---------------- visits & bookings + action lists
    def lead_row(r):
        age = (today - day(r["date"])).days if r["date"] else None
        return {"date": r["date"].strftime("%d %b") if r["date"] else "—", "age_days": age,
                "name": r["first_name"] if cfg.get("show_lead_names", True) else "", "campaign": display.get(r["ckey"], r["campaign_raw"]),
                "ad": r["ad"], "platform": r["platform"], "latest": r["latest"], "stage": r["label"], "bucket": r["bucket"],
                "proof": r["proof"], "doubt": r["doubt"] or "", "feedback": r["feedback"]}
    FAR = dt.datetime.max
    lists = {
        "will_visit": [lead_row(r) for r in crm if r["key"] == "will_visit"],
        "visited_booked": [lead_row(r) for r in crm if r["rank"] >= 4],
        "push_for_visit": [lead_row(r) for r in sorted((r for r in crm if r["bucket"] == "Quality" and r["rank"] <= 2), key=lambda r: r["date"] or FAR)],
        "not_called": [lead_row(r) for r in crm if r["bucket"] == "Not Called"],
        "doubtful": [lead_row(r) for r in crm if r["doubt"]],
        "all": [lead_row(r) for r in sorted(crm, key=lambda r: r["date"] or dt.datetime.min, reverse=True)],
    }
    unm = Counter(r["latest"] for r in crm if r["bucket"] == "Unmapped")
    if unm:
        checks.append(("warn", "Statuses not understood (add them in config.json → extra_words): " + ", ".join(f"'{k}' ({v})" for k, v in unm.most_common(10))))
    if acct["doubtful_quality"]:
        checks.append(("warn", f"{acct['doubtful_quality']} leads count as quality by status, but the sales feedback says otherwise "
                               "(e.g. 'not genuine', 'going with another project'). They stay in Quality % per your rule — see Lead List → Doubtful."))

    actions = build_actions(acct, campaigns, ads_out, placements, crm, checks, cfg, today)
    growth = growth_plan(acct, campaigns, ads_out, placements, angles, cfg)

    return {
        "client": client, "generated_at": dt.datetime.now().strftime("%d %b %Y, %H:%M"),
        "period": {"from": start.strftime("%d %b %Y"), "to": today.strftime("%d %b %Y")},
        "files": {k: sorted({n for n, _ in v}) for k, v in src.items()},
        "rules": {"quality_pct": "Quality leads ÷ total leads", "visit_ratio": "(Visited + Booked) ÷ total leads",
                  "quality_stages": [STAGE_LABEL[s] for s in reversed(STAGE_ORDER)], "thresholds": th, "compare_days": N},
        "account": acct, "compare": compare, "daily": daily, "campaigns": campaigns, "ads": ads_out, "angles": angles,
        "formats": formats, "placements": placements, "placement_groups": placement_groups, "platforms": platforms,
        "actions": actions, "growth": growth, "lists": lists,
        "checks": [{"type": t, "text": x} for t, x in checks],
        "insights": insights,
    }


# --------------------------------------------------------------------------------------
# placement quality (estimate) + decisions
# --------------------------------------------------------------------------------------
def placement_group(name: str) -> str:
    n = norm(name)
    if "reel" in n:
        return "Reels"
    if "stor" in n:
        return "Stories"
    if "feed" in n:
        return "Feed"
    return "Other"


def estimate_placement_quality(crm: list, ads: list) -> dict:
    """Meta never says which placement a lead came from. Estimate: split each ad's CRM leads / quality / visits across
    placements in proportion to that ad's Meta-reported leads per placement (spend share if the ad has no Meta leads;
    campaign mix if the ad name is not in the export). Totals always equal the CRM totals that could be linked."""
    by = defaultdict(lambda: {"leads": 0.0, "quality": 0.0, "visits": 0.0})
    unalloc = 0
    A = [r for r in ads if r["placement"]]
    groups = defaultdict(list)
    for r in crm:
        groups[(r["ckey"], r["adkey"])].append(r)
    for (ck, ak) in sorted(groups):
        g = groups[(ck, ak)]
        rows = [r for r in A if r["ckey"] == ck and r["adkey"] == ak] if ak and ak != norm("(no ad name)") else []
        if not rows or (sum(r["meta_leads"] for r in rows) <= 0 and sum(r["spend"] for r in rows) <= 0):
            rows = [r for r in A if r["ckey"] == ck]
        if not rows:
            unalloc += len(g)
            continue
        w = defaultdict(float)
        for r in rows:
            w[r["placement"]] += r["meta_leads"]
        if sum(w.values()) <= 0:
            w = defaultdict(float)
            for r in rows:
                w[r["placement"]] += r["spend"]
        tot = sum(w.values())
        if tot <= 0:
            unalloc += len(g)
            continue
        L, Q, V = len(g), sum(r["bucket"] == "Quality" for r in g), sum(r["rank"] >= 4 for r in g)
        for pl in sorted(w):
            share = w[pl] / tot
            by[pl]["leads"] += L * share
            by[pl]["quality"] += Q * share
            by[pl]["visits"] += V * share
    return {"by_placement": dict(by), "unallocated": unalloc}


def placement_decision(p: dict, acct: dict, acct_meta: dict):
    cpq, cpm = acct.get("cost_per_quality"), acct_meta.get("cpm")
    name = norm(p["placement"])
    if p["share_spend_pct"] < 3:
        return "IGNORE", f"Only {p['share_spend_pct']}% of spend — too small to matter. Leave it to Meta."
    if "multi submit" in name or (cpm and p.get("cpm") and p["cpm"] > 4 * cpm):
        return "REMOVE", (f"CPM {fmt_inr(p['cpm'])} is {p['cpm'] / cpm:.0f}× the account average"
                          + (" — multi-submit shows your ad to people who just filled another advertiser's form (form-fillers, not buyers)." if "multi submit" in name else "."))
    if p["meta_leads"] == 0 and cpq and p["spend"] >= cpq:
        return "REMOVE", f"{fmt_inr(p['spend'])} spent, 0 leads."
    if p["est_leads"] < 3:
        return "LOW DATA", f"Only ~{p['est_leads']} CRM leads linked — keep running, judge after 3+."
    if cpq and p.get("est_cost_per_quality") and p["est_cost_per_quality"] > 1.3 * cpq:
        return "WATCH", f"Est. {fmt_inr(p['est_cost_per_quality'])} per quality lead vs account {fmt_inr(cpq)} — cut if it stays here for 7 more days."
    if cpq and p.get("est_cost_per_quality") and p["est_cost_per_quality"] <= 0.85 * cpq:
        return "KEEP — BEST", f"Est. {fmt_inr(p['est_cost_per_quality'])} per quality lead, better than account {fmt_inr(cpq)}."
    return "KEEP", f"Est. quality {pc_(p.get('est_quality_pct'))}, cost per quality near account average."


def pc_(x):
    return "—" if x is None else f"{x}%"


# --------------------------------------------------------------------------------------
# action plan (rule-based, expert wording; agent can override via insights.actions)
# --------------------------------------------------------------------------------------
def growth_plan(acct, campaigns, ads_out, placements, angles, cfg):
    """Where to put money: scale / hold / fix campaigns, make-more / pause ads & angles, keep / remove placements."""
    th = cfg["thresholds"]
    tq, tv, low = th["scale_quality_pct"], th["target_visit_ratio_pct"], th["low_data_leads"]
    acpq = acct.get("cost_per_quality")
    camp = []
    for c in campaigns:
        if c["leads"] == 0 and c["spend"] > 0:
            move, why = "CUT", f"{fmt_inr(c['spend'])} spent, 0 leads in CRM."
        elif c["leads"] < low:
            move, why = "TEST — LOW DATA", f"{c['leads']} leads so far (need {low}). Keep a fixed budget until it has data."
        elif c["quality_pct"] >= tq and c["visit_ratio_pct"] >= tv:
            move, why = "SCALE ↑", f"Quality {c['quality_pct']}% (≥{tq}%) and visits {c['visit_ratio_pct']}% (≥{tv}%). Raise 15-20% every 3-4 days."
        elif c["quality_pct"] >= tq:
            move, why = "HOLD — PUSH VISITS", f"Quality {c['quality_pct']}% is on target but visits {c['visit_ratio_pct']}% < {tv}%. Fix visits before adding budget."
        elif c["quality_pct"] >= th["optimize_quality_pct"]:
            move, why = "OPTIMIZE", f"Quality {c['quality_pct']}% — just below target. Tighten form, cut weak ads."
        else:
            move, why = "FIX OR CUT ↓", f"Quality {c['quality_pct']}% is far below {tq}%. Move budget to the best campaign."
        camp.append({"campaign": c["campaign"], "move": move, "why": why, "leads": c["leads"], "quality": c["quality"],
                     "quality_pct": c["quality_pct"], "visit_ratio_pct": c["visit_ratio_pct"], "spend": c["spend"],
                     "cost_per_quality": c.get("cost_per_quality"), "meets_quality": c["quality_pct"] >= tq, "meets_visit": c["visit_ratio_pct"] >= tv})
    order = {"SCALE ↑": 0, "HOLD — PUSH VISITS": 1, "OPTIMIZE": 2, "TEST — LOW DATA": 3, "FIX OR CUT ↓": 4, "CUT": 5}
    camp.sort(key=lambda x: (order[x["move"]], -x["quality"]))
    make_more = [a for a in ads_out if a["quality"] >= 3 and not a["fatigue"]["status"].startswith("Fatigued")]
    make_more.sort(key=lambda a: (-(a["quality_pct"]), a.get("cost_per_quality") or 1e12))
    refresh = [a for a in ads_out if a["fatigue"]["status"].startswith("Fatigued")]
    pause = [a for a in ads_out if a["leads"] >= 5 and a["quality_pct"] < th["review_quality_pct"]]
    ads_plan = ([{"ad": a["ad"], "campaign": a["campaign"], "move": "MAKE MORE LIKE THIS", "angle": a["angle"], "format": a["format"],
                  "why": f"{a['quality']} quality of {a['leads']} ({a['quality_pct']}%)" + (f", {fmt_inr(a['cost_per_quality'])} per quality lead" if a.get("cost_per_quality") else "")}
                 for a in make_more[:4]]
                + [{"ad": a["ad"], "campaign": a["campaign"], "move": "REFRESH (fatigued)", "angle": a["angle"], "format": a["format"],
                    "why": ", ".join(a["fatigue"]["signals"])} for a in refresh]
                + [{"ad": a["ad"], "campaign": a["campaign"], "move": "PAUSE", "angle": a["angle"], "format": a["format"],
                    "why": f"{a['quality']} quality of {a['leads']} ({a['quality_pct']}%)"} for a in pause])
    best_angles = [x for x in angles if x["quality"] >= 3][:3]
    pl = [{"placement": p["placement"], "move": p["decision"], "why": p["decision_why"]} for p in placements
          if p.get("decision") in ("KEEP — BEST", "WATCH", "REMOVE")]
    return {"campaigns": camp, "ads": ads_plan, "angles": best_angles, "placements": pl,
            "targets": {"quality_pct": tq, "visit_ratio_pct": tv}}


def build_actions(acct, campaigns, ads_out, placements, crm, checks, cfg, today):
    th = cfg["thresholds"]
    A = []

    def add(pr, owner, when, action, why, impact):
        A.append({"priority": pr, "owner": owner, "when": when, "action": action, "why": why, "impact": impact})

    d = acct["diagnosis"]
    for x in d["sales_issues"]:
        t = x["issue"]
        if "never called" in t:
            add("P1", "Sales", "Today", f"Call the {x['affected']} uncalled leads before 7 pm; WhatsApp brochure right after.",
                t, "Every hour of delay cuts connect rate — Mumbai buyers enquire on 3-4 projects at once.")
        elif "stuck at Details" in t:
            add("P1", "Sales", "This week", "Visit push: call every Details Shared / Call Back lead and offer two fixed slots "
                "('Saturday 11 am or Sunday 4 pm?'). Send a 30-sec walkthrough video before the call.", t,
                f"Moves leads from 'interested' to a site visit — the only number that sells flats. Target {th['target_visit_ratio_pct']}%+ visit ratio.")
        elif "ringing" in t:
            add("P2", "Sales", "This week", "3-attempt rule for ringing leads: morning, evening, next day + WhatsApp after the first missed call.",
                t, "Usually recovers 25-35% of 'ringing' leads.")
        elif "will visit" in t:
            add("P1", "Sales", "This week", "Confirm every planned visit a day before + morning reminder; offer pickup / cab for weekday visits.",
                t, "Converts planned visits into actual visits.")
    for c in campaigns:
        if c["verdict"] == "PAUSE / FIX":
            add("P1", "Media", "Today", f"Pause or fix '{c['campaign']}'.", c["verdict_reason"], "Stops spend on leads that do not convert.")
        elif c["verdict"] == "SCALE":
            add("P2", "Media", "This week", f"Scale '{c['campaign']}' +15-20% every 3-4 days (never double at once).",
                c["verdict_reason"], "More quality leads at a similar cost; avoids resetting learning.")
        elif c["verdict"] == "OPTIMIZE" and c["quality_pct"] >= th["scale_quality_pct"]:
            add("P2", "Media", "This week", f"Hold budget on '{c['campaign']}' — quality is good, fix visits first.",
                c["verdict_reason"], "Adding budget now only piles up leads at Details Shared.")
        elif c["verdict"] == "LOW DATA" and c["leads"] <= 3 and c["spend"] >= 2 * (acct.get("cpl") or 1e12):
            add("P1", "Media", "Today", f"Pause '{c['campaign']}' or move its budget to the best campaign.",
                f"{fmt_inr(c['spend'])} spent for only {c['leads']} lead(s).", "Saves budget for a campaign that is already producing.")
    for a in ads_out:
        if a["fatigue"]["status"].startswith("Fatigued"):
            add("P1", "Media", "This week", f"Replace '{a['ad']}' ({a['campaign']}) with 2 fresh variations of the same angle ({a['angle']}).",
                "Fatigue: " + ", ".join(a["fatigue"]["signals"]), "Restores CTR and CPL; keeps the angle that already brings quality.")
    weak = [a for a in ads_out if a["leads"] >= 5 and a["quality_pct"] < th["review_quality_pct"]]
    for a in weak:
        add("P2", "Media", "This week", f"Pause ad '{a['ad']}' in {a['campaign']}.",
            f"{a['quality']} quality from {a['leads']} leads ({a['quality_pct']}%) at {fmt_inr(a['spend'])} spend.", "Moves budget to ads that bring real buyers.")
    best = [a for a in ads_out if a["quality"] >= 3 and a.get("cost_per_quality") and not a["fatigue"]["status"].startswith("Fatigued")]
    if best:
        b = min(best, key=lambda x: x["cost_per_quality"])
        add("P2", "Creative", "Next 7 days", f"Make 2-3 new variations of '{b['ad']}' ({b['angle']}, {b['format']}) — new hook / first 3 seconds, same message.",
            f"Best cost per quality lead: {fmt_inr(b['cost_per_quality'])} ({b['quality']} quality of {b['leads']}).",
            "Andromeda rewards genuinely different creatives; scaling a winner's angle is the cheapest growth.")
    for p in placements:
        if p.get("decision") == "REMOVE":
            add("P2", "Media", "This week", f"Exclude placement '{p['placement']}' (manual placements).", p["decision_why"], f"Saves ~{p['share_spend_pct']}% of spend.")
    dead = {k: v for k, v in acct["dead_reasons"].items() if k in CAMPAIGN_DEAD and k != "Wrong / Invalid Number"}
    if dead and sum(dead.values()) >= 3:
        top = ", ".join(f"{k} ({v})" for k, v in list(dead.items())[:3])
        add("P2", "Media", "This week", fix_for_reasons(dead).replace("Media: ", "").capitalize(), f"Top wrong-fit reasons: {top}.",
            "Filters wrong buyers before they become paid leads.")
    if acct["doubtful_quality"] or sum(acct["dead_reasons"].values()) >= 5:
        add("P2", "Tracking", "This week", "Send CAPI quality signals: Lead_Disqualified for CP / M Client / RTMI / junk, Will Visit + Visited for good leads.",
            f"{acct['dead']} dead and {acct['doubtful_quality']} doubtful leads — Meta currently learns from all leads equally.",
            "Within 2-3 weeks Meta finds more people like your real buyers.")
    for t, x in checks:
        if t == "warn" and "scientific format" in x:
            add("P3", "Tracking", "This week", "In the CRM sheet set the phone column to Format → Number → Plain text; share exports as .xlsx.",
                "Phones like 9.20E+11 make different people look like duplicates (old sheets undercounted leads).", "Correct lead counts and CPL.")
            break
    order = {"P1": 0, "P2": 1, "P3": 2}
    seen, out = set(), []
    for a in sorted(A, key=lambda x: order[x["priority"]]):
        if a["action"] not in seen:
            seen.add(a["action"])
            out.append(a)
    return out[:12]


def best_by(cl: list, col: str, ca, acol):
    if not cl:
        return None
    best = None
    groups = defaultdict(list)
    for r in cl:
        groups[r[col]].append(r)
    for val in sorted(groups):
        g = groups[val]
        q, n = sum(r["bucket"] == "Quality" for r in g), len(g)
        sp = float(sum(r["spend"] for r in ca if norm(r[acol]) == norm(val))) if (ca and acol) else None
        item = {"name": val, "leads": n, "quality": q, "quality_pct": pct(q, n), "site_visits": sum(r["rank"] >= 4 for r in g),
                "spend": sp, "cost_per_quality": money(sp, q) if sp else None}
        rank = (item["site_visits"], q, -(item["cost_per_quality"] or 1e12))
        if q and (best is None or rank > best[0]):
            best = (rank, item)
    return best[1] if best else None


def best_placement(ca: list):
    if not ca or not any(r["placement"] for r in ca):
        return None
    by = defaultdict(list)
    for r in ca:
        by[r["placement"]].append(r)
    rows = []
    for p in sorted(by):
        if not p:
            continue
        l, s = sum(r["meta_leads"] for r in by[p]), sum(r["spend"] for r in by[p])  # noqa: E741
        if l >= 3:
            rows.append({"name": p, "meta_leads": int(l), "spend": round(float(s)), "meta_cpl": money(s, l)})
    rows.sort(key=lambda x: x["meta_cpl"])
    return rows[0] if rows else None


def _json_default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    return str(o)


def to_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, ensure_ascii=False, default=_json_default)


# ======================================================================================
def main():
    ap = argparse.ArgumentParser(description="Campaign dashboard for one client")
    ap.add_argument("--client", required=True)
    ap.add_argument("--from", dest="d_from", type=dt.date.fromisoformat)
    ap.add_argument("--to", dest="d_to", type=dt.date.fromisoformat)
    ap.add_argument("--insights", help="JSON with expert notes written by the agent")
    ap.add_argument("--out", help="output folder (default clients/<client>/reports)")
    a = ap.parse_args()
    from dashboard_html import render  # local module
    try:
        rep = build(a.client, a.d_from, a.d_to, a.insights)
    except ValueError as e:
        sys.exit(str(e))
    out = Path(a.out) if a.out else CLIENTS_DIR / a.client / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    (out / f"{stamp}_dashboard.json").write_text(to_json(rep), encoding="utf-8")
    (out / f"{stamp}_dashboard.html").write_text(render(rep), encoding="utf-8")
    acc = rep["account"]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print(f"Leads {acc['leads']} | Spend {fmt_inr(acc['spend'])} | CPL {fmt_inr(acc['cpl'])} | Quality {acc['quality']} ({acc['quality_pct']}%) "
          f"| Visits {acc['site_visits']} | Campaigns {len(rep['campaigns'])} | Ads {len(rep['ads'])}")
    for c in rep["campaigns"]:
        print(f"  - {c['campaign']}: {c['leads']} leads, Q {c['quality_pct']}%, CPL {fmt_inr(c['cpl'])} → {c['verdict']} | problem: {c['diagnosis']['owner']}")
    print(f"Saved: {out / (stamp + '_dashboard.html')}")


if __name__ == "__main__":
    main()
