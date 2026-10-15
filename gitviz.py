#!/usr/bin/env python3
r"""
gitviz - live picture of YOUR Git repository (ET0735 Lab 1)

Install once:   pip install git+https://github.com/StanleySP-eng/gitviz
Then, in a SECOND Command Prompt, go to your repo folder and run:

    cd C:\Local_Git_Repository\lab1
    gitviz              (or: python -m gitviz)

A browser tab opens and redraws itself every second while you type git
commands in your other Command Prompt. Press Ctrl+C here to stop.

No other packages needed - only Python 3.8+ and Git for Windows.
It only READS your repository; it never changes anything.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MAX_COMMITS = 200
MAX_FILES = 300
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def git(cwd, *args):
    p = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.quotepath=off", *args],
        cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return p.returncode, p.stdout.decode("utf-8", "replace")


def read_status(top):
    working, staged, conflicts = [], [], []
    _, out = git(top, "status", "--porcelain=v1", "-z")
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path = e[:2], e[3:]
        if xy[0] in "RC":
            i += 1  # -z puts the original path of a rename in the next field
        if xy == "??":
            working.append({"path": path, "code": "U"})
        elif xy == "!!":
            continue
        elif xy in CONFLICT_CODES:
            conflicts.append({"path": path, "code": xy})
        else:
            if xy[0] != " ":
                staged.append({"path": path, "code": xy[0]})
            if xy[1] != " ":
                working.append({"path": path, "code": xy[1]})
    return working[:MAX_FILES], staged[:MAX_FILES], conflicts


def read_state(path, show_orphans):
    s = {"path": path, "repo": False}
    if not os.path.isdir(path):
        s["error"] = "This folder does not exist yet."
        return s
    rc, top = git(path, "rev-parse", "--show-toplevel")
    if rc != 0:
        return s
    top = top.strip()
    _, gitdir = git(top, "rev-parse", "--absolute-git-dir")
    gitdir = gitdir.strip()
    s.update(repo=True, top=top)

    rc, sym = git(top, "symbolic-ref", "-q", "HEAD")
    s["head_branch"] = sym.strip()[len("refs/heads/"):] if rc == 0 else None
    rc, head = git(top, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    s["head"] = head.strip() if rc == 0 else None

    refs = []
    _, out = git(top, "for-each-ref",
                 "--format=%(refname)%09%(objectname)%09%(*objectname)",
                 "refs/heads", "refs/remotes", "refs/tags")
    kinds = {"heads": "branch", "remotes": "remote", "tags": "tag"}
    for line in out.splitlines():
        name, obj, peeled = (line.split("\t") + ["", ""])[:3]
        if name.startswith("refs/remotes/") and name.endswith("/HEAD"):
            continue
        _, kind, short = name.split("/", 2)
        refs.append({"name": short, "kind": kinds[kind], "commit": peeled or obj})
    s["refs"] = refs

    commits = []
    if s["head"] or refs:
        fmt = "--format=%H%x09%P%x09%ct%x09%an%x09%s"
        args = ["log", "--all", "--topo-order", "-n", str(MAX_COMMITS), fmt]
        reachable = None
        if show_orphans:
            args.insert(2, "--reflog")
            _, r = git(top, "log", "--all", "--format=%H", "-n", "5000")
            reachable = set(r.split())
        _, out = git(top, *args)
        for line in out.splitlines():
            parts = line.split("\t", 4)
            if len(parts) < 5:
                continue
            cid, parents, ts, author, subject = parts
            commits.append({
                "id": cid, "parents": parents.split(), "time": int(ts or 0),
                "author": author, "subject": subject,
                "orphan": reachable is not None and cid not in reachable,
            })
    s["commits"] = commits

    s["working"], s["staged"], s["conflicts"] = read_status(top)

    tracked = []
    if s["head"]:
        _, out = git(top, "ls-tree", "-r", "-z", "--name-only", "HEAD")
        tracked = [f for f in out.split("\0") if f]
    s["tracked_count"] = len(tracked)
    s["tracked"] = tracked[:MAX_FILES]

    s["merging"] = os.path.exists(os.path.join(gitdir, "MERGE_HEAD"))

    _, out = git(top, "reflog", "-n", "8", "--format=%h%x09%gs")
    s["reflog"] = [dict(zip(("id", "msg"), l.split("\t", 1))) for l in out.splitlines() if "\t" in l]

    remotes = {}
    _, out = git(top, "remote", "-v")
    for line in out.splitlines():
        bits = line.split()
        if len(bits) >= 2:
            remotes.setdefault(bits[0], bits[1])
    s["remotes"] = [{"name": k, "url": v} for k, v in remotes.items()]

    s["upstream"] = None
    if s["head_branch"] and s["head"]:
        rc, up = git(top, "rev-parse", "--abbrev-ref", "@{u}")
        if rc == 0:
            _, counts = git(top, "rev-list", "--left-right", "--count", "HEAD...@{u}")
            ahead, behind = (counts.split() + ["0", "0"])[:2]
            s["upstream"] = {"name": up.strip(), "ahead": int(ahead), "behind": int(behind)}
    return s


def make_handler(repo_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                self.reply(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            elif url.path == "/api/state":
                orphans = parse_qs(url.query).get("orphans") == ["1"]
                try:
                    body = json.dumps(read_state(repo_path, orphans))
                    self.reply(200, "application/json", body.encode("utf-8"))
                except Exception as e:  # keep the page alive if git hiccups mid-command
                    self.reply(500, "text/plain", str(e).encode("utf-8"))
            else:
                self.reply(404, "text/plain", b"not found")

        def reply(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(prog="gitviz", description="Live visualizer for a local Git repository.")
    ap.add_argument("repo", nargs="?", default=".", help="folder to watch (default: current folder)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = ap.parse_args()

    if not shutil.which("git"):
        sys.exit("Git was not found. Install Git for Windows (git-scm.com) and open a NEW Command Prompt.")

    repo = os.path.abspath(args.repo)
    handler = make_handler(repo)
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            continue
    if server is None:
        sys.exit("Could not find a free port near %d." % args.port)

    url = "http://127.0.0.1:%d/" % server.server_address[1]
    print("GitViz is watching: %s" % repo)
    print("Open %s in your browser (Ctrl+C to stop)." % url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGitViz stopped.")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GitViz – live repo</title>
<style>
:root{
  --bg:#f4f6f9; --panel:#fff; --ink:#1d2330; --muted:#677084; --line:#dce0e7;
  --commit:#3b7ddd; --commit-ink:#fff; --edge:#9aa3b2;
  --branch:#f5b83d; --branch-ink:#3a2600; --head:#e5484d; --head-ink:#fff;
  --remote:#8e6be8; --remote-ink:#fff; --tag:#2fa774; --tag-ink:#fff;
  --A:#1f9d55; --M:#c97a0a; --D:#d63b3b; --U:#677084; --R:#3b7ddd; --C:#c0237a;
  --flash:#fff1b8; --ok:#1f9d55; --warn-bg:#fff4e0; --warn-ink:#7a4a00; --bad-bg:#fde8e8; --bad-ink:#8a1c1c;
  --mono:ui-monospace,SFMono-Regular,Consolas,"Cascadia Mono",Menlo,monospace;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#11141a; --panel:#1a1e26; --ink:#e5e8ee; --muted:#98a1b2; --line:#2b313c;
    --commit:#4a8cf0; --edge:#5d6677; --branch:#e9a92c; --flash:#5a4612;
    --warn-bg:#3a2a0c; --warn-ink:#ffd58a; --bad-bg:#3d1717; --bad-ink:#ffb3b3;
  }
}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
code{font-family:var(--mono);font-size:.92em}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;padding:12px 20px;background:var(--panel);border-bottom:1px solid var(--line)}
header h1{font-size:17px;margin:0}
#path{color:var(--muted);font-family:var(--mono);font-size:12.5px;word-break:break-all}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:600;background:var(--bg);border:1px solid var(--line)}
.spacer{flex:1}
#conn{font-size:12.5px;color:var(--ok)}
#conn.off{color:var(--D)}
label.toggle{font-size:12.5px;color:var(--muted);display:flex;gap:6px;align-items:center;cursor:pointer}
main{padding:16px 20px 28px;display:grid;gap:16px;max-width:1500px;margin:0 auto}
.banner{padding:10px 14px;border-radius:8px;background:var(--warn-bg);color:var(--warn-ink);font-weight:500}
.banner.bad{background:var(--bad-bg);color:var(--bad-ink)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.card h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 8px}
.areas{display:grid;grid-template-columns:1fr auto 1fr auto 1fr;gap:10px;align-items:stretch}
.arrow{display:flex;flex-direction:column;justify-content:center;align-items:center;color:var(--muted);font-size:12px;gap:2px}
.arrow b{font-size:22px;line-height:1}
.arrow code{white-space:nowrap}
.area .hint{font-size:12px;color:var(--muted);margin:-4px 0 8px}
.files{display:flex;flex-wrap:wrap;gap:6px;max-height:180px;overflow:auto}
.file{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;border-radius:6px;border:1px solid var(--line);font-family:var(--mono);font-size:12.5px;background:var(--bg);max-width:100%}
.file span.p{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.file i{font-style:normal;font-weight:700;font-size:11px;padding:0 5px;border-radius:4px;color:#fff}
.c-A{background:var(--A)}.c-M{background:var(--M)}.c-D{background:var(--D)}.c-U{background:var(--U)}.c-R{background:var(--R)}.c-C{background:var(--R)}.c-X{background:var(--C)}
.file.new{animation:flash 2.4s ease-out}
@keyframes flash{0%{background:var(--flash);transform:scale(1.06)}100%{background:var(--bg);transform:none}}
.empty{color:var(--muted);font-size:13px;font-style:italic}
#graphWrap{overflow:auto;border-radius:8px;background:var(--bg);min-height:140px}
#graph{display:block}
#graph .edge{fill:none;stroke:var(--edge);stroke-width:2.5}
#graph .edge.orphan{stroke-dasharray:5 4;opacity:.45}
#graph circle.c{fill:var(--commit);stroke:var(--panel);stroke-width:3}
#graph g.orphan{opacity:.4}
#graph g.orphan circle.c{stroke-dasharray:4 3;stroke:var(--edge)}
#graph circle.ring{fill:none;stroke:var(--head);stroke-width:3}
#graph text.id{fill:var(--commit-ink);font:600 9px var(--mono);text-anchor:middle;dominant-baseline:central;pointer-events:none}
#graph text.msg{fill:var(--muted);font-size:11px;text-anchor:middle}
#graph g.new circle.c{animation:pop 1.6s ease-out}
@keyframes pop{0%{r:6}40%{r:30}100%{r:24}}
#graph .lbl rect{rx:4;ry:4}
#graph .lbl text{font:600 11px var(--mono);text-anchor:middle;dominant-baseline:central}
#graph .lbl.branch rect{fill:var(--branch)} #graph .lbl.branch text{fill:var(--branch-ink)}
#graph .lbl.current rect{stroke:var(--head);stroke-width:2.5}
#graph .lbl.head rect{fill:var(--head)} #graph .lbl.head text{fill:var(--head-ink)}
#graph .lbl.remote rect{fill:var(--remote)} #graph .lbl.remote text{fill:var(--remote-ink)}
#graph .lbl.tag rect{fill:var(--tag)} #graph .lbl.tag text{fill:var(--tag-ink)}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12px;color:var(--muted);margin-top:8px}
.legend span::before{content:"";display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px;vertical-align:-1px;background:var(--k)}
.bottom{display:grid;grid-template-columns:2fr 1fr;gap:16px}
#log{list-style:none;margin:0;padding:0;max-height:300px;overflow:auto}
#log li{display:flex;gap:10px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--line)}
#log li:last-child{border-bottom:0}
#log time{color:var(--muted);font-family:var(--mono);font-size:11.5px;flex:none}
#log .cmd{margin-left:auto;flex:none;font-family:var(--mono);font-size:11.5px;color:var(--muted);background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:0 6px}
#log li.fresh{animation:flashrow 2.4s ease-out}
@keyframes flashrow{0%{background:var(--flash)}100%{background:transparent}}
.side h3{font-size:12px;margin:12px 0 4px;color:var(--muted)}
.side h3:first-child{margin-top:0}
.side ol{margin:0;padding-left:0;list-style:none;font-size:12.5px}
.side li{padding:2px 0}
.side li code{color:var(--muted);margin-right:6px}
@media (max-width:900px){
  .areas{grid-template-columns:1fr}
  .arrow{flex-direction:row;gap:8px}
  .arrow b{transform:rotate(90deg)}
  .bottom{grid-template-columns:1fr}
  header,main{padding-left:16px;padding-right:16px}
}
</style>
</head>
<body>
<header>
  <h1>GitViz</h1>
  <span id="branchPill" class="pill">…</span>
  <span id="upPill" class="pill" hidden></span>
  <span id="path"></span>
  <span class="spacer"></span>
  <label class="toggle"><input type="checkbox" id="orphans"> show orphaned commits</label>
  <span id="conn">● live</span>
</header>
<main>
  <div id="banners"></div>

  <section class="areas">
    <div class="card area">
      <h2>Working Directory</h2>
      <div class="hint">Files on your disk that Git sees as new or changed</div>
      <div class="files" id="wd"></div>
    </div>
    <div class="arrow"><b>→</b><code>git add</code></div>
    <div class="card area">
      <h2>Staging Area</h2>
      <div class="hint">Changes that will go into the next commit</div>
      <div class="files" id="stage"></div>
    </div>
    <div class="arrow"><b>→</b><code>git commit</code></div>
    <div class="card area">
      <h2>Repository <span id="repoCount"></span></h2>
      <div class="hint">Files saved in the latest commit (HEAD)</div>
      <div class="files" id="repo"></div>
    </div>
  </section>

  <section class="card">
    <h2>Commit graph</h2>
    <div id="graphWrap"><svg id="graph" width="0" height="0"></svg></div>
    <div class="legend">
      <span style="--k:var(--commit)">commit</span>
      <span style="--k:var(--branch)">local branch</span>
      <span style="--k:var(--head)">HEAD (where you are)</span>
      <span style="--k:var(--remote)">remote-tracking branch (e.g. origin/master)</span>
      <span style="--k:var(--tag)">tag</span>
    </div>
  </section>

  <section class="bottom">
    <div class="card">
      <h2>What just happened</h2>
      <ul id="log"></ul>
    </div>
    <div class="card side">
      <h2>Details</h2>
      <h3>Recent HEAD moves (git reflog)</h3>
      <ol id="reflog"></ol>
      <h3>Remotes</h3>
      <ol id="remotes"></ol>
    </div>
  </section>
</main>

<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const short = id => id ? id.slice(0, 7) : "";
const SVGNS = "http://www.w3.org/2000/svg";
const FILE_WORDS = {U:"untracked", M:"modified", D:"deleted", A:"new file", R:"renamed", C:"copied"};

let prev = null, prevText = "", skipDiff = false;
const freshCommits = new Set();

$("orphans").addEventListener("change", () => { skipDiff = prev !== null; prevText = ""; });

async function poll() {
  let text = null;
  try {
    const r = await fetch("/api/state?orphans=" + ($("orphans").checked ? 1 : 0), {cache: "no-store"});
    if (r.ok) text = await r.text();
    $("conn").textContent = "● live"; $("conn").className = "";
  } catch (e) {
    $("conn").textContent = "● disconnected – is gitviz.py still running?"; $("conn").className = "off";
  }
  if (text !== null && text !== prevText) {
    const s = JSON.parse(text);
    try {
      if (!skipDiff) describeChanges(prev, s);
    } catch (e) { console.error(e); }
    skipDiff = false;
    try { render(s, prev); } catch (e) { console.error(e); }
    prev = s; prevText = text;
  }
  setTimeout(poll, 1000);
}

/* ---------- activity log ---------- */
function log(html, cmd) {
  const li = document.createElement("li");
  li.className = "fresh";
  const t = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  li.innerHTML = `<time>${t}</time><span>${html}</span>` + (cmd ? `<span class="cmd">${esc(cmd)}</span>` : "");
  $("log").prepend(li);
  while ($("log").children.length > 60) $("log").lastChild.remove();
}

const fileKey = f => f.code + ":" + f.path;

function describeChanges(a, b) {
  if (!a) {
    log(`Watching <code>${esc(b.path)}</code>` + (b.repo ? "" : " – not a Git repository yet"));
    return;
  }
  if (!a.repo && b.repo) { log("Repository created – a hidden <code>.git</code> folder now exists", "git init"); return; }
  if (a.repo && !b.repo) { log("The <code>.git</code> folder is gone – this is no longer a repository"); return; }
  if (!b.repo) return;

  const reflogTop = b.reflog.length ? b.reflog[0].msg : "";
  const reflogChanged = reflogTop && (!a.reflog.length || a.reflog[0].id !== b.reflog[0].id || a.reflog[0].msg !== reflogTop);

  // commits
  const had = new Set(a.commits.map(c => c.id));
  const fresh = b.commits.filter(c => !had.has(c.id) && !c.orphan);
  const freshIds = new Set(fresh.map(c => c.id));
  fresh.slice(0, 5).reverse().forEach(c => {
    freshCommits.add(c.id);
    setTimeout(() => freshCommits.delete(c.id), 2500);
    const merge = c.parents.length > 1;
    log(`New ${merge ? "<b>merge</b> " : ""}commit <b>${short(c.id)}</b> “${esc(c.subject)}”` +
        (merge ? " – it has two parents" : ""), merge ? "git merge" : "git commit");
  });

  // refs
  const map = refs => new Map(refs.map(r => [r.kind + ":" + r.name, r]));
  const am = map(a.refs), bm = map(b.refs);
  for (const [k, r] of bm) {
    const old = am.get(k);
    const name = `<b>${esc(r.name)}</b>`;
    if (!old) {
      if (r.kind === "branch" && freshIds.has(r.commit) && b.head_branch === r.name) { /* first commit on an unborn branch */ }
      else if (r.kind === "branch") log(`Branch ${name} created at ${short(r.commit)}`, "git branch");
      else if (r.kind === "remote") log(`Remote-tracking branch ${name} appeared at ${short(r.commit)}`, "git push / fetch");
      else log(`Tag ${name} created at ${short(r.commit)}`, "git tag");
    } else if (old.commit !== r.commit) {
      if (r.kind === "remote") log(`${name} moved to ${short(r.commit)} – GitHub's copy is now known to be here`, "git push / fetch / pull");
      else if (r.kind === "branch" && freshIds.has(r.commit) && b.head_branch === r.name) { /* already logged as a commit */ }
      else {
        const ff = /fast-forward/i.test(reflogTop) ? " (fast-forward: the label just slid forward)" : "";
        log(`Branch ${name} moved ${short(old.commit)} → ${short(r.commit)}${ff}`, ff ? "git merge" : "");
      }
    }
  }
  for (const [k, r] of am) {
    if (!bm.has(k)) log(`${r.kind === "tag" ? "Tag" : "Branch"} <b>${esc(r.name)}</b> deleted`, r.kind === "tag" ? "git tag -d" : "git branch -d");
  }

  // HEAD
  if (a.head_branch !== b.head_branch) {
    if (b.head_branch) log(`HEAD now points to branch <b>${esc(b.head_branch)}</b> – your files now match that branch`, "git checkout / switch");
    else log(`<b>Detached HEAD</b> at ${short(b.head)} – you are not on any branch`, "git checkout");
  } else if (!b.head_branch && a.head !== b.head) {
    log(`Detached HEAD moved to ${short(b.head)}`, "git checkout");
  }
  if (reflogChanged && a.reflog.length) log(`<span style="color:var(--muted)">reflog: “${esc(reflogTop)}”</span>`);

  // merge state
  if (!a.merging && b.merging) log("<b>Merge in progress</b> – Git needs you to finish it", "git merge");
  if (a.merging && !b.merging) log("Merge finished (or aborted)");
  const aCon = new Set(a.conflicts.map(f => f.path));
  b.conflicts.filter(f => !aCon.has(f.path)).forEach(f => log(`<b style="color:var(--D)">Conflict</b> in <code>${esc(f.path)}</code> – edit the file, then <code>git add</code> it`));

  // files
  let n = 0;
  const say = (html, cmd) => { if (n++ < 6) log(html, cmd); };
  const aStaged = new Set(a.staged.map(fileKey)), bStaged = new Set(b.staged.map(fileKey));
  const aWork = new Set(a.working.map(fileKey));
  b.staged.filter(f => !aStaged.has(fileKey(f))).forEach(f =>
    say(`Staged <code>${esc(f.path)}</code> (${FILE_WORDS[f.code] || f.code}) – it will be in the next commit`, f.code === "D" ? "git rm / git add" : "git add"));
  if (!fresh.length) {
    a.staged.filter(f => !bStaged.has(fileKey(f)) && !b.staged.some(g => g.path === f.path)).forEach(f =>
      say(`Unstaged <code>${esc(f.path)}</code>`, "git restore --staged"));
  }
  b.working.filter(f => !aWork.has(fileKey(f))).forEach(f => {
    if (f.code === "U") say(`New file <code>${esc(f.path)}</code> – Git sees it but is not tracking it yet`);
    else if (f.code === "M") say(`<code>${esc(f.path)}</code> changed on disk – not staged yet`);
    else if (f.code === "D") say(`<code>${esc(f.path)}</code> deleted on disk – not staged yet`);
  });
  if (n > 6) log(`…and ${n - 6} more file changes`);
}

/* ---------- top panels ---------- */
function fileChips(el, files, oldFiles, emptyText, codeOf) {
  const old = new Set((oldFiles || []).map(f => typeof f === "string" ? f : fileKey(f)));
  if (!files.length) { el.innerHTML = `<span class="empty">${emptyText}</span>`; return; }
  el.innerHTML = files.map(f => {
    const key = typeof f === "string" ? f : fileKey(f);
    const path = typeof f === "string" ? f : f.path;
    const code = codeOf(f);
    const badge = code ? `<i class="c-${code.cls}" title="${code.word}">${code.letter}</i>` : "";
    return `<span class="file${oldFiles && !old.has(key) ? " new" : ""}" title="${esc(path)}">${badge}<span class="p">${esc(path)}</span></span>`;
  }).join("");
}

function render(s, p) {
  const pr = p && p.repo ? p : null;
  $("path").textContent = s.top || s.path;

  // header + banners
  const bp = $("branchPill"), up = $("upPill");
  if (!s.repo) bp.textContent = "not a repository";
  else if (s.head_branch) bp.innerHTML = `On branch <code>${esc(s.head_branch)}</code>` + (s.head ? "" : " · no commits yet");
  else bp.innerHTML = `Detached HEAD at <code>${short(s.head)}</code>`;
  if (s.upstream) {
    const u = s.upstream;
    up.hidden = false;
    up.innerHTML = u.ahead || u.behind
      ? `<code>${esc(u.name)}</code>: ${u.ahead ? `${u.ahead} to push ↑` : ""}${u.ahead && u.behind ? " · " : ""}${u.behind ? `${u.behind} to pull ↓` : ""}`
      : `up to date with <code>${esc(u.name)}</code>`;
  } else up.hidden = true;

  const banners = [];
  if (!s.repo) banners.push(["", s.error ? esc(s.error) :
    "This folder is not a Git repository yet. In your Command Prompt run <code>git init</code> here – this page will update by itself."]);
  if (s.repo && s.merging) banners.push(["bad", "Merge in progress. Fix the conflicted files, <code>git add</code> them, then <code>git commit</code> (or <code>git merge --abort</code>)."]);
  if (s.repo && s.conflicts.length) banners.push(["bad", "Conflicts in: " + s.conflicts.map(f => `<code>${esc(f.path)}</code>`).join(", ")]);
  if (s.repo && !s.head_branch && s.head) banners.push(["", "Detached HEAD: you are looking at an old commit, not a branch. New commits here are easy to lose – use <code>git switch master</code> to go back."]);
  $("banners").innerHTML = banners.map(([cls, html]) => `<div class="banner ${cls}">${html}</div>`).join("");

  if (!s.repo) {
    ["wd", "stage", "repo"].forEach(id => $(id).innerHTML = '<span class="empty">—</span>');
    $("repoCount").textContent = "";
    drawGraph(s);
    $("reflog").innerHTML = ""; $("remotes").innerHTML = "";
    return;
  }

  const wcode = f => f.code === "U" ? {cls: "U", letter: "?", word: "untracked"} : {cls: f.code, letter: f.code, word: FILE_WORDS[f.code] || f.code};
  const scode = f => ({cls: f.code, letter: f.code, word: FILE_WORDS[f.code] || f.code});
  const workAll = s.working.concat(s.conflicts.map(f => ({path: f.path, code: "X"})));
  fileChips($("wd"), workAll, pr && pr.working.concat(pr.conflicts.map(f => ({path: f.path, code: "X"}))),
    "Nothing new or changed – working tree clean",
    f => f.code === "X" ? {cls: "X", letter: "!", word: "conflict"} : wcode(f));
  fileChips($("stage"), s.staged, pr && pr.staged, "Nothing staged. Use <code>git add &lt;file&gt;</code>", scode);
  fileChips($("repo"), s.tracked, pr && pr.tracked, s.head ? "No files in this commit" : "No commits yet", () => null);
  $("repoCount").textContent = s.tracked_count ? `(${s.tracked_count})` : "";

  drawGraph(s, pr);

  $("reflog").innerHTML = s.reflog.length
    ? s.reflog.map(r => `<li><code>${esc(r.id)}</code>${esc(r.msg)}</li>`).join("")
    : '<li class="empty">nothing yet</li>';
  $("remotes").innerHTML = s.remotes.length
    ? s.remotes.map(r => `<li><b>${esc(r.name)}</b> <code>${esc(r.url)}</code></li>`).join("")
    : '<li class="empty">none – add one with <code>git remote add origin &lt;url&gt;</code></li>';
}

/* ---------- commit graph ---------- */
const COL = 112, R = 24, LBL_H = 18, LBL_GAP = 4, PAD_X = 70;

function el(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

function drawGraph(s, p) {
  const svg = $("graph"), wrap = $("graphWrap");
  svg.innerHTML = "";
  const commits = (s.commits || []).slice().reverse();  // oldest on the left
  if (!commits.length) {
    svg.setAttribute("width", 520); svg.setAttribute("height", 120);
    const t = el("text", {x: 24, y: 64, class: "msg", style: "text-anchor:start;font-size:13px"}, svg);
    t.textContent = s.repo ? "No commits yet – your first git commit will appear here as a circle." : "Run git init to create a repository.";
    return;
  }
  const byId = new Map(commits.map((c, i) => [c.id, Object.assign({col: i}, c)]));

  // labels attached to each commit, bottom-to-top: tags, remotes, branches, current branch, HEAD
  const labels = new Map();
  const add = (id, l) => { if (!byId.has(id)) return; if (!labels.has(id)) labels.set(id, []); labels.get(id).push(l); };
  const rank = {tag: 0, remote: 1, branch: 2};
  s.refs.slice().sort((x, y) => rank[x.kind] - rank[y.kind] || x.name.localeCompare(y.name)).forEach(r => {
    if (r.kind === "branch" && r.name === s.head_branch) return;
    add(r.commit, {text: r.name, cls: r.kind});
  });
  if (s.head_branch && s.head) add(s.head, {text: s.head_branch, cls: "branch current"});
  if (s.head) add(s.head, {text: "HEAD", cls: "head"});

  // lanes: follow first parents from each tip; master/main gets the bottom lane
  const lane = new Map(); let lanes = 0;
  const branches = s.refs.filter(r => r.kind === "branch");
  const mainB = branches.find(b => b.name === "main") || branches.find(b => b.name === "master");
  const tips = [];
  if (mainB) tips.push(mainB.commit);
  if (s.head) tips.push(s.head);
  branches.slice().sort((x, y) => x.name.localeCompare(y.name)).forEach(b => tips.push(b.commit));
  s.refs.filter(r => r.kind !== "branch").forEach(r => tips.push(r.commit));
  s.commits.forEach(c => tips.push(c.id));
  for (const tip of tips) {
    let id = tip, used = false;
    while (id && byId.has(id) && !lane.has(id)) { lane.set(id, lanes); used = true; id = byId.get(id).parents[0]; }
    if (used) lanes++;
  }

  // lane heights depend on how many labels are stacked in them
  const maxLbl = new Array(lanes).fill(0);
  for (const [id, ls] of labels) maxLbl[lane.get(id)] = Math.max(maxLbl[lane.get(id)], ls.length);
  const laneY = []; let y = 14;
  // draw lane 0 at the bottom, like branches growing upwards off master
  for (let l = lanes - 1; l >= 0; l--) {
    const top = y + maxLbl[l] * (LBL_H + LBL_GAP) + 6;
    laneY[l] = top + R;
    y = laneY[l] + R + 30;
  }
  const W = PAD_X * 2 + (commits.length - 1) * COL, H = y;
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pos = id => { const c = byId.get(id); return [PAD_X + c.col * COL, laneY[lane.get(id)]]; };

  // edges
  const gEdges = el("g", {}, svg);
  for (const c of commits) {
    const [cx, cy] = pos(c.id);
    c.parents.forEach((pid, idx) => {
      if (!byId.has(pid)) return;
      const [px, py] = pos(pid);
      let d;
      if (py === cy) d = `M${px},${py} L${cx},${cy}`;
      else if (idx === 0) d = `M${px},${py} C${px + COL / 2},${py} ${px + COL / 2},${cy} ${px + COL},${cy} L${cx},${cy}`;
      else d = `M${px},${py} L${cx - COL},${py} C${cx - COL / 2},${py} ${cx - COL / 2},${cy} ${cx},${cy}`;
      el("path", {d, class: "edge" + (c.orphan || byId.get(pid).orphan ? " orphan" : "")}, gEdges);
    });
  }

  // commits
  for (const c of commits) {
    const [cx, cy] = pos(c.id);
    const g = el("g", {class: (c.orphan ? "orphan " : "") + (freshCommits.has(c.id) ? "new" : "")}, svg);
    const tip = el("title", {}, g);
    tip.textContent = `${c.id}\n${c.subject}\n${c.author} · ${new Date(c.time * 1000).toLocaleString()}` +
      (c.parents.length > 1 ? "\nmerge commit (2 parents)" : "") + (c.orphan ? "\nnot on any branch (only in reflog)" : "");
    if (c.id === s.head) el("circle", {cx, cy, r: R + 5, class: "ring"}, g);
    el("circle", {cx, cy, r: R, class: "c"}, g);
    el("text", {x: cx, y: cy, class: "id"}, g).textContent = short(c.id);
    const m = c.subject.length > 16 ? c.subject.slice(0, 15) + "…" : c.subject;
    el("text", {x: cx, y: cy + R + 15, class: "msg"}, g).textContent = m;

    (labels.get(c.id) || []).forEach((l, i) => {
      const txt = l.text.length > 15 ? l.text.slice(0, 14) + "…" : l.text;
      const w = txt.length * 6.9 + 14;
      const ly = cy - R - 8 - LBL_H / 2 - i * (LBL_H + LBL_GAP);
      const lg = el("g", {class: "lbl " + l.cls}, svg);
      el("title", {}, lg).textContent = l.text;
      el("rect", {x: cx - w / 2, y: ly - LBL_H / 2, width: w, height: LBL_H}, lg);
      el("text", {x: cx, y: ly}, lg).textContent = txt;
    });
  }

  // keep HEAD in view when it moves
  if (s.head && (!p || p.head !== s.head || p.head_branch !== s.head_branch) && byId.has(s.head)) {
    const [hx] = pos(s.head);
    wrap.scrollLeft = Math.max(0, hx - wrap.clientWidth / 2);
  }
}

poll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
