#!/usr/bin/env python3
"""omarchy-pr-try shell AST analyzer (unprivileged, read-only).

Usage: omarchy-pr-try-analyze.py DIR

DIR holds:
  files.tsv     one line per shell file: <relative path><TAB><comma separated added line numbers>
  blobs/N       the new content of the N-th file in files.tsv (0-based)

For every file, `shfmt --tojson` builds the syntax tree (shfmt never executes
anything). The tree is walked for structurally dangerous constructs, and only
constructs that start on an added line are reported, as TSV on stdout:

  SEVERITY<TAB>rule-id<TAB>file<TAB>line<TAB>excerpt

A summary line "#coverage parsed=N failed=M" goes last. Files that shfmt
cannot parse are listed as "#parse-failed<TAB>file".

This covers shell only. QML/JavaScript are not parsed here.
"""

import json
import os
import subprocess
import sys

SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
ROOT_CMDS = {"sudo", "doas", "pkexec", "su", "run0"}
DOWNLOADERS = {"curl", "wget", "aria2c"}
WRITE_REDIRS = {54, 55, 60, 64, 65}          # >  >>  >|  &>  &>>
SYSTEM_PREFIXES = ("/etc/", "/usr/", "/boot/", "/var/lib/", "/opt/")


def word_lit(word):
    """The literal text of a word, or None when any part is dynamic."""
    if not word:
        return None
    out = []
    for p in word.get("Parts", []):
        t = p.get("Type")
        if t == "Lit":
            out.append(p.get("Value", ""))
        elif t == "SglQuoted":
            out.append(p.get("Value", ""))
        elif t == "DblQuoted":
            inner = word_lit({"Parts": p.get("Parts", [])})
            if inner is None:
                return None
            out.append(inner)
        else:
            return None
    return "".join(out)


def word_has(word, kinds):
    stack = list(word.get("Parts", [])) if word else []
    while stack:
        p = stack.pop()
        if p.get("Type") in kinds:
            return True
        stack.extend(p.get("Parts", []) or [])
    return False


def basename(cmd):
    return cmd.rsplit("/", 1)[-1] if cmd else cmd


def call_name(call):
    args = call.get("Args") or []
    if not args:
        return None, []
    name = word_lit(args[0])
    return (basename(name) if name else None), args[1:]


def strip_wrappers(name, rest):
    """sudo / env / command / exec / nice prefixes: return the wrapped command."""
    wrappers = ROOT_CMDS | {"env", "command", "exec", "nice", "nohup", "stdbuf", "timeout"}
    while name in wrappers and rest:
        nxt = []
        i = 0
        while i < len(rest):
            lit = word_lit(rest[i])
            if lit is not None and (lit.startswith("-") or ("=" in lit and name == "env")):
                i += 1
                continue
            if name == "timeout" and lit is not None and lit[:1].isdigit():
                i += 1
                continue
            break
        if i >= len(rest):
            return name, rest
        name = basename(word_lit(rest[i]) or "")
        rest = rest[i + 1:]
        nxt = rest
        rest = nxt
    return name, rest


def calls_in(node):
    """Every CallExpr inside a node (depth first)."""
    found = []
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("Type") == "CallExpr":
                found.append(n)
            for v in n.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(n, list):
            stack.extend(n)
    return found


def line_of(node):
    return (node.get("Pos") or {}).get("Line", 0)


class Walker:
    def __init__(self, rel, added, lines):
        self.rel = rel
        self.added = added
        self.lines = lines
        self.findings = []

    def report(self, sev, rule, node):
        ln = line_of(node)
        if ln not in self.added:
            return
        text = self.lines[ln - 1].strip() if 0 < ln <= len(self.lines) else ""
        self.findings.append((sev, rule, self.rel, ln, text[:90]))

    def walk(self, n):
        if isinstance(n, list):
            for x in n:
                self.walk(x)
            return
        if not isinstance(n, dict):
            return
        t = n.get("Type")
        if t == "BinaryCmd" and n.get("Op") in (12, 13):
            self.pipeline(n)
        if "Cmd" in n and isinstance(n.get("Redirs"), list):
            for r in n["Redirs"]:
                if r.get("Op") in WRITE_REDIRS:
                    target = word_lit(r.get("Word"))
                    if target and target.startswith(SYSTEM_PREFIXES):
                        self.report("MEDIUM", "ast-system-write", n)
        if t == "CallExpr":
            self.call(n)
        for v in n.values():
            if isinstance(v, (dict, list)):
                self.walk(v)

    def pipeline(self, n):
        left = n.get("X") or {}
        right = n.get("Y") or {}
        downloads = any(call_name(c)[0] in DOWNLOADERS for c in calls_in(left))
        if not downloads:
            return
        rc = calls_in(right)
        if not rc:
            return
        name, rest = strip_wrappers(*call_name(rc[-1]))
        if name in SHELLS or name in ("python", "python3", "perl", "ruby", "node"):
            self.report("CRITICAL", "ast-download-exec", n)

    def call(self, c):
        name, rest = call_name(c)
        if name is None:
            return
        if name in ROOT_CMDS:
            self.report("HIGH", "ast-root", c)
        name2, rest2 = strip_wrappers(name, rest)
        if name2 == "eval":
            self.report("HIGH", "ast-eval", c)
        if name2 in ("source", "."):
            if rest2 and word_has(rest2[0], {"ProcSubst"}):
                for inner in calls_in(rest2[0]):
                    if call_name(inner)[0] in DOWNLOADERS:
                        self.report("CRITICAL", "ast-download-exec", c)
                        return
                self.report("HIGH", "ast-source-dynamic", c)
            elif rest2 and word_lit(rest2[0]) is None:
                self.report("HIGH", "ast-source-dynamic", c)
        if name2 in SHELLS:
            for i, w in enumerate(rest2):
                lit = word_lit(w)
                if lit is not None and lit.startswith("-") and "c" in lit[1:] and not lit.startswith("--"):
                    if i + 1 < len(rest2) and word_lit(rest2[i + 1]) is None:
                        self.report("MEDIUM", "ast-shell-string", c)
                    break
        if name2 == "rm":
            flags = "".join(word_lit(w) or "" for w in rest2 if (word_lit(w) or "").startswith("-") and not (word_lit(w) or "").startswith("--"))
            longs = [word_lit(w) for w in rest2 if (word_lit(w) or "").startswith("--")]
            if "r" in flags.lower() or "--recursive" in longs:
                for w in rest2:
                    lit = word_lit(w)
                    if lit is not None and lit.startswith("-"):
                        continue
                    if word_has(w, {"ParamExp", "CmdSubst"}):
                        self.report("HIGH", "ast-rm-dynamic", c)
                        break
                    if lit in ("/", "/*", "~", "~/", "$HOME"):
                        self.report("CRITICAL", "ast-rm-root", c)
                        break
        if name2 == "chmod":
            for w in rest2:
                lit = word_lit(w) or ""
                if lit in ("777", "0777", "666", "a+w", "o+w") or "+s" in lit or lit.startswith(("4", "2", "6")) and len(lit) == 4 and lit.isdigit():
                    self.report("HIGH", "ast-chmod-unsafe", c)
                    break


def main():
    if len(sys.argv) != 2:
        print("usage: omarchy-pr-try-analyze.py DIR", file=sys.stderr)
        return 2
    d = sys.argv[1]
    parsed = failed = 0
    with open(os.path.join(d, "files.tsv"), encoding="utf-8", errors="replace") as fh:
        entries = [ln.rstrip("\n").split("\t") for ln in fh if ln.strip()]
    for i, cols in enumerate(entries):
        rel = cols[0]
        added = set()
        if len(cols) > 1 and cols[1]:
            for x in cols[1].split(","):
                if x.isdigit():
                    added.add(int(x))
        blob = os.path.join(d, "blobs", str(i))
        try:
            with open(blob, "rb") as fh:
                src = fh.read()
        except OSError:
            failed += 1
            print("#parse-failed\t%s" % rel)
            continue
        try:
            res = subprocess.run(["shfmt", "--tojson", "-ln", "bash"], input=src, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            failed += 1
            print("#parse-failed\t%s" % rel)
            continue
        if res.returncode != 0:
            failed += 1
            print("#parse-failed\t%s" % rel)
            continue
        try:
            tree = json.loads(res.stdout)
        except ValueError:
            failed += 1
            print("#parse-failed\t%s" % rel)
            continue
        parsed += 1
        w = Walker(rel, added, src.decode("utf-8", "replace").split("\n"))
        w.walk(tree)
        seen = set()
        for f in w.findings:
            key = (f[1], f[3])
            if key in seen:
                continue
            seen.add(key)
            print("\t".join(str(x).replace("\t", " ") for x in f))
    print("#coverage parsed=%d failed=%d" % (parsed, failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
