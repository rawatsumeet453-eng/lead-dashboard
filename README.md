# Lead Quality Dashboard — team website

Team member opens the link → selects project → uploads CRM .xlsx + Meta export → gets the same dashboard as the agent.
Everything runs **inside the browser** (Pyodide). No server, no login, client data never leaves the device.

## Folder
```
site/
├── index.html            ← the website
├── engine/               ← SAME Python engine as the agent (copied by tools/build_site.py)
│   ├── lead_quality.py, purekit.py, campaign_dashboard.py, dashboard_html.py
│   ├── default_config.json   ← 30% / 4% targets, thresholds
│   └── clients.json          ← per-project status words & rules (from clients/*/config.json)
├── pyodide/              ← Python for the browser (self-hosted, no CDN)
└── wheels/               ← openpyxl (reads .xlsx)
```

## Put it online (GitHub Pages, free) — one time, 10 min
1. github.com → **New repository** → name `lead-dashboard` → **Public** → Create.
   (Public is fine: the repo has only code. Client data is never uploaded anywhere.)
2. **Add file → Upload files** → drag the *contents* of the `site/` folder (index.html, engine, pyodide, wheels) → Commit.
3. **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, folder `/ (root)` → Save.
4. 1-2 min baad link milega: `https://<your-github-username>.github.io/lead-dashboard/` — ye team ko bhej do.

## Rule change kiya? (new status word, new client, threshold)
1. Agent folder me change karo (`clients/<client>/config.json` ya `tools/default_config.json`).
2. Chalao: `python tools/build_site.py`
3. GitHub repo me sirf `engine/` folder dobara upload karo (replace). Sab team members ko naya engine mil jaayega.

## Check before release
`python tools/test_classifier.py` → must print **ALL PASS**.
