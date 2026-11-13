"""A single self-contained HTML receipt: no scripts, no network, works offline and prints well."""

from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional

from .util import human_duration, local_clock, local_stamp, squash

LIMIT = 200

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#16181d;--mute:#5b6270;--line:#e3e6ec;--high:#c62828;--highbg:#fdeaea;
--med:#a15c00;--medbg:#fff3dc;--ok:#1b7a3d;--okbg:#e6f6ec;--note:#3b5bdb;--notebg:#eaeefd;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e8eaf0;--mute:#98a0b0;--line:#2a2f3a;--high:#ff8a80;
--highbg:#3a1c1c;--med:#ffcc80;--medbg:#3a2d14;--ok:#7be0a0;--okbg:#17301f;--note:#91a7ff;--notebg:#1d2440}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 48px}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;justify-content:space-between;margin-bottom:4px}
h1{font-size:1.35rem;margin:0}
.brand{color:var(--mute);font-size:.85rem}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;margin:12px 0 20px;font-size:.92rem}
.meta dt{color:var(--mute)} .meta dd{margin:0;overflow-wrap:anywhere}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}
h2{font-size:1rem;margin:0 0 10px;letter-spacing:.01em}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px}
.stat{border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.stat b{display:block;font-size:1.5rem;line-height:1.1}
.stat span{color:var(--mute);font-size:.8rem}
.pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.75rem;font-weight:600;margin-right:8px;white-space:nowrap}
.high{background:var(--highbg);color:var(--high)} .medium{background:var(--medbg);color:var(--med)}
.info{background:var(--notebg);color:var(--note)} .good{background:var(--okbg);color:var(--ok)}
ul{list-style:none;margin:0;padding:0} li{padding:7px 0;border-top:1px solid var(--line)} li:first-child{border-top:0}
code,.mono{font-family:var(--mono);font-size:.84rem;overflow-wrap:anywhere}
.sub{color:var(--mute);font-size:.85rem;display:block;overflow-wrap:anywhere}
.t{color:var(--mute);font-family:var(--mono);font-size:.8rem;margin-right:8px}
.bad{color:var(--high);font-weight:600}.ok{color:var(--ok)}
.quote{border-left:3px solid var(--line);padding:2px 12px;color:var(--mute);white-space:pre-wrap;overflow-wrap:anywhere}
.callout{border-radius:10px;padding:10px 14px}
footer{color:var(--mute);font-size:.8rem;margin-top:18px}
@media print{body{background:#fff}.card{break-inside:avoid;border-color:#ccc}}
"""


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _meta(r: Dict[str, Any]) -> str:
    s = r["session"]
    when = local_stamp(s["start"]) if s.get("start") else "unknown"
    if s.get("duration_seconds") is not None:
        when += f" &middot; {_e(human_duration(s['duration_seconds']))}"
    else:
        when = _e(when)
    rows = [("Project", _e(s.get("cwd") or "unknown")), ("When", when), ("Showing", _e(r["window"])), ("Session", _e(s["id"][:36]))]
    if s.get("title"):
        rows.insert(1, ("Task", _e(squash(s["title"], 200))))
    return "<dl class='meta'>" + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows) + "</dl>"


def render_html(r: Dict[str, Any], limit: Optional[int] = None) -> str:
    cap = LIMIT if not limit else limit
    sm, v = r["summary"], r["verification"]
    body: List[str] = []

    stat = lambda n, label: f"<div class='stat'><b>{_e(n)}</b><span>{_e(label)}</span></div>"
    body.append(
        "<section class='card'><h2>What happened</h2><div class='stats'>"
        + stat(sm["commands"], "commands run")
        + stat(sm["commands_failed"], "commands failed")
        + stat(sm["files_edited"] + sm["files_written"], "files changed")
        + stat(sm["files_deleted"], "files deleted")
        + stat(sm["files_read"], "files read")
        + stat(sm["web"], "web lookups")
        + "</div></section>"
    )

    items = r["attention"]
    body.append(f"<section class='card'><h2>Needs your attention ({len(items)})</h2>")
    if not items:
        body.append("<div class='callout good'>Nothing risky was detected.</div>")
    else:
        body.append("<ul>")
        for it in items:
            count = f" <span class='sub'>happened {it['count']} times</span>" if it["count"] > 1 else ""
            ex = ""
            if it["level"] != "info" and it.get("example"):
                ex = f"<code class='sub'>{_e(squash(it['example'], 200))}</code>"
            body.append(f"<li><span class='pill {_e(it['level'])}'>{_e(it['level'].upper())}</span>{_e(it['message'])}{count}{ex}</li>")
        body.append("</ul>")
    body.append("</section>")

    cls = {"pass": "good", "pass-but-stale": "medium", "fail": "high", "unknown": "medium", "none": "medium"}[v["verdict"]]
    label = {"pass": "PASS", "pass-but-stale": "STALE", "fail": "FAIL", "unknown": "UNKNOWN", "none": "NO TESTS"}[v["verdict"]]
    extra = ""
    if v.get("ran"):
        extra = f"<span class='sub'>{v['runs']} test run(s). Last: <code>{_e(squash(v['last_command'], 160))}</code></span>"
        for path in v.get("edited_after", [])[:6]:
            extra += f"<span class='sub'>changed after: <code>{_e(path)}</code></span>"
    body.append(f"<section class='card'><h2>Tests</h2><span class='pill {cls}'>{label}</span>{_e(v['message'])}{extra}</section>")

    files = r["files"]
    changed = [("edited", x) for x in files["edited"]] + [("written", x) for x in files["written"]] + [("deleted", x) for x in files["deleted"]]
    body.append(f"<section class='card'><h2>Files changed ({len(changed)})</h2>")
    if not changed:
        body.append("<p class='sub'>None through the agent's file tools. Changes made by shell commands are not listed here.</p>")
    else:
        body.append("<ul>")
        for lab, item in changed[:cap]:
            times = f" <span class='sub'>x{item['count']}</span>" if item["count"] > 1 else ""
            body.append(f"<li><span class='pill info'>{lab}</span><code>{_e(item['path'])}</code>{times}</li>")
        if len(changed) > cap:
            body.append(f"<li class='sub'>... and {len(changed) - cap} more</li>")
        body.append("</ul>")
    body.append("</section>")

    commands = r["commands"]
    body.append(f"<section class='card'><h2>Commands ({len(commands)})</h2>")
    if not commands:
        body.append("<p class='sub'>None.</p>")
    else:
        body.append("<ul>")
        for c in commands[:cap]:
            mark = {True: "<span class='ok'>ok</span>", False: "<span class='bad'>FAILED</span>", None: "<span class='sub'>?</span>"}[c["ok"]]
            flag = ""
            if c["risk"] in ("high", "medium"):
                flag = f" <span class='pill {c['risk']}'>review</span>"
            body.append(f"<li><span class='t'>{_e(local_clock(c['time']))}</span>{mark} <code>{_e(squash(c['command'], 400))}</code>{flag}</li>")
        if len(commands) > cap:
            body.append(f"<li class='sub'>... and {len(commands) - cap} more (use --format json for everything)</li>")
        body.append("</ul>")
    body.append("</section>")

    if r["failures"]:
        body.append(f"<section class='card'><h2>Things that failed ({len(r['failures'])})</h2><ul>")
        for f in r["failures"][:25]:
            detail = f"<span class='sub'>{_e(squash(f['detail'], 240))}</span>" if f.get("detail") else ""
            body.append(f"<li><span class='t'>{_e(local_clock(f['time']))}</span><code>{_e(squash(f['target'], 160))}</code>{detail}</li>")
        body.append("</ul></section>")

    if r.get("last_message"):
        body.append(f"<section class='card'><h2>What the agent said last</h2><div class='quote'>{_e(squash(r['last_message'], 1200))}</div></section>")
    if r["warnings"]:
        body.append("<section class='card'><h2>Notes</h2><ul>" + "".join(f"<li>{_e(w)}</li>" for w in r["warnings"]) + "</ul></section>")

    tokens = sm.get("tokens")
    token_text = ""
    if tokens:
        token_text = f" &middot; tokens in {tokens.get('input', 0):,} / out {tokens.get('output', 0):,}"
    privacy = "Secrets are hidden." if r["redacted"] else "<b>Secret hiding was OFF. Do not share this file.</b>"
    title = f"Agent Receipt - {r['agent']['label']}"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='light dark'>"
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
        f"<header><h1>{_e(title)}</h1><span class='brand'>Agent Receipts {_e(r['tool_version'])}</span></header>"
        + _meta(r)
        + "".join(body)
        + f"<footer>Warnings are hints for a quick review, not a security guarantee. {privacy}{token_text}</footer>"
        "</div></body></html>\n"
    )
