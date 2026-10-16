"""End-to-end check of the installed `gitviz` command, following the Lab 1 steps.

Starts gitviz on a folder that does not exist yet, then runs the lab's Git commands
(mkdir, init, add, commit, branch, merge, conflict, push) and checks /api/state after each.
Run after `pip install`:  python tests/smoke_test.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

WORK = tempfile.mkdtemp(prefix="gitviz-test-")
LAB = os.path.join(WORK, "Local_Git_Repository", "lab1")
REMOTE = os.path.join(WORK, "github-lab1.git")
ENV = dict(os.environ, PYTHONUNBUFFERED="1")
procs = []


def run(*cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd or LAB, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and p.returncode != 0:
        sys.exit("FAILED: %s\n%s" % (" ".join(cmd), p.stdout.decode(errors="replace")))
    return p


def start_gitviz(path):
    exe = shutil.which("gitviz")
    if not exe:
        sys.exit("FAILED: 'gitviz' is not on PATH after pip install")
    p = subprocess.Popen([exe, path, "--no-browser"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=ENV)
    procs.append(p)
    lines = []
    deadline = time.time() + 30
    while time.time() < deadline:
        line = p.stdout.readline().decode(errors="replace")
        if not line:
            break
        lines.append(line)
        m = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        if m:
            return int(m.group(1))
    sys.exit("FAILED: gitviz did not start:\n" + "".join(lines))


def get(port, path="/api/state"):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=10) as r:
        body = r.read().decode("utf-8")
    return json.loads(body) if path.startswith("/api") else body


def check(name, cond, state=None):
    if not cond:
        detail = json.dumps(state, indent=1)[:3000] if state is not None else ""
        sys.exit("FAILED: %s\n%s" % (name, detail))
    print("ok  ", name)


def names(files):
    return {f["path"] for f in files}


def branch_at(s, name):
    return next((r["commit"] for r in s["refs"] if r["kind"] == "branch" and r["name"] == name), None)


def main():
    port = start_gitviz(LAB)
    check("page is served", "<html" in get(port, "/").lower())

    s = get(port)
    check("missing folder reported", not s["repo"] and "does not exist" in s.get("error", ""), s)

    os.makedirs(LAB)
    s = get(port)
    check("empty folder is not a repo yet", not s["repo"] and "error" not in s, s)

    run("git", "init")
    s = get(port)
    check("git init detected", s["repo"] and s["head"] is None and s["head_branch"] == "master", s)

    with open(os.path.join(LAB, "HelloWorld.py"), "w") as f:
        f.write('print("Hello World")\n')
    s = get(port)
    check("new file in working directory", names(s["working"]) == {"HelloWorld.py"}, s)

    run("git", "add", "HelloWorld.py")
    s = get(port)
    check("git add moves file to staging", names(s["staged"]) == {"HelloWorld.py"} and not s["working"], s)

    run("git", "commit", "-m", "Initial version")
    s = get(port)
    check("first commit", len(s["commits"]) == 1 and branch_at(s, "master") == s["head"]
          and not s["staged"] and s["tracked"] == ["HelloWorld.py"], s)

    with open(os.path.join(LAB, "HelloWorld.py"), "a") as f:
        f.write('print("Second line")\n')
    s = get(port)
    check("modified file shown", s["working"] == [{"path": "HelloWorld.py", "code": "M"}], s)
    run("git", "commit", "-am", "Second line")

    run("git", "checkout", "-b", "feature")
    with open(os.path.join(LAB, "feature.py"), "w") as f:
        f.write("x = 1\n")
    run("git", "add", ".")
    run("git", "commit", "-m", "Add feature")
    s = get(port)
    check("branch and checkout", s["head_branch"] == "feature" and len(s["commits"]) == 3
          and branch_at(s, "master") != branch_at(s, "feature"), s)

    run("git", "checkout", "master")
    run("git", "merge", "--no-ff", "feature", "-m", "Merge feature")
    s = get(port)
    merge = next(c for c in s["commits"] if c["id"] == s["head"])
    check("merge commit has two parents", len(merge["parents"]) == 2, s)

    # conflict: both branches change the same line
    run("git", "checkout", "-b", "other")
    with open(os.path.join(LAB, "HelloWorld.py"), "w") as f:
        f.write('print("from other")\n')
    run("git", "commit", "-am", "Other change")
    run("git", "checkout", "master")
    with open(os.path.join(LAB, "HelloWorld.py"), "w") as f:
        f.write('print("from master")\n')
    run("git", "commit", "-am", "Master change")
    run("git", "merge", "other", check=False)
    s = get(port)
    check("merge conflict detected", s["merging"] and names(s["conflicts"]) == {"HelloWorld.py"}, s)
    run("git", "merge", "--abort")
    s = get(port)
    check("merge abort clears conflict", not s["merging"] and not s["conflicts"], s)

    run("git", "init", "--bare", REMOTE, cwd=WORK)
    run("git", "remote", "add", "origin", REMOTE)
    run("git", "push", "-u", "origin", "master")
    s = get(port)
    check("push sets upstream in sync", s["upstream"] == {"name": "origin/master", "ahead": 0, "behind": 0}
          and any(r["kind"] == "remote" and r["name"] == "origin/master" for r in s["refs"]), s)

    with open(os.path.join(LAB, "HelloWorld.py"), "a") as f:
        f.write("# more\n")
    run("git", "commit", "-am", "Local only")
    s = get(port)
    check("ahead of origin after new commit", s["upstream"]["ahead"] == 1, s)

    run("git", "commit", "--amend", "-m", "Local only (amended)")
    s = get(port + 0, "/api/state?orphans=1")
    check("amended commit shown as orphan", any(c["orphan"] for c in s["commits"]), s)

    port2 = start_gitviz(LAB)
    check("second gitviz gets its own port (%d vs %d)" % (port2, port), port2 != port)

    print("\nAll checks passed.")


try:
    main()
finally:
    for p in procs:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            p.terminate()
    shutil.rmtree(WORK, ignore_errors=True)
