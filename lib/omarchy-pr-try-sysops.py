#!/usr/bin/env python3
"""omarchy-pr-try privileged file helper.

This is the only part of omarchy-pr-try that runs as root (through sudo or
doas). It never executes downloaded content and never runs a shell. It accepts
a small set of operations, each with validated arguments:

  preflight                      check that the private state folder is safe
  apply PLAN TXID                save originals, journal, then replace files
  restore [--accept-manifest]    put every original back (idempotent)
  verify --expect applied|restored
  show                           print the journal (for a readable copy)
  finish                         delete the state folder after a verified restore
  manual                         print manual recovery instructions

Safety rules enforced here, whatever the caller asks for:
  * destinations must be on a fixed allowlist (/usr/bin/omarchy*,
    /usr/share/omarchy/{bin,themes,shell,icons}/...); no "..", no "//",
    no hidden or option-like path segments
  * every path component is opened with O_NOFOLLOW relative to its parent
    directory fd, so a symlink anywhere in the path is refused, never followed
  * every directory on the way must be owned by root and not group/world
    writable (in test mode: owned by the test user)
  * new content is read from the caller's staging file and must match the
    SHA-256 the plan names, so a swapped staging file is refused
  * files are written to a temporary name in the same directory, fsync'd and
    renamed into place (atomic); the directory is fsync'd afterwards
  * originals are kept content-addressed (orig/<sha256>) and re-verified
    before every restore
  * the journal is written before any file changes and after each change,
    atomically, and carries its own checksum as its last line

Test mode: when --sysroot is given and the helper does not run as root, the
"root-owned" checks accept the invoking user instead, and fault injection
through OMARCHY_PR_TEST_FAULT is honoured. As root, fault injection is ignored.
"""

import errno
import hashlib
import os
import re
import secrets
import signal
import stat
import sys

JOURNAL_MAGIC = "#omarchy-pr-try-journal v2"
STATE_REL = "var/lib/omarchy-pr-try"
MAX_ENTRIES = 500
MAX_FILE_BYTES = 64 * 1024 * 1024

SEGMENT = re.compile(r"^[A-Za-z0-9_@+,=][A-Za-z0-9._@+,=-]*$")
BIN_RE = re.compile(r"^/usr/bin/omarchy(-[A-Za-z0-9._-]+)?$")
SHARE_ROOTS = ("/usr/share/omarchy/bin/", "/usr/share/omarchy/themes/",
               "/usr/share/omarchy/shell/", "/usr/share/omarchy/icons/")
DIR_ROOTS = ("/usr/share/omarchy/themes", "/usr/share/omarchy/shell",
             "/usr/share/omarchy/icons", "/usr/share/omarchy/bin")


class Refuse(Exception):
    pass


def out(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def err(msg):
    sys.stderr.write("! " + msg + "\n")
    sys.stderr.flush()


def safe_text(s):
    return "".join(ch if (ch.isprintable() and ch not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069") else "?" for ch in s)


# ------------------------------------------------------------------ context

class Ctx:
    def __init__(self, sysroot):
        self.euid = os.geteuid()
        self.sysroot = sysroot or ""
        self.test = bool(self.sysroot) and self.euid != 0
        if self.sysroot:
            if not os.path.isabs(self.sysroot) or os.path.islink(self.sysroot) \
                    or not os.path.isdir(self.sysroot) or os.path.realpath(self.sysroot) == "/":
                raise Refuse("the test system root must be an existing absolute directory, not a symlink")
        self.state_dir = os.path.join(self.sysroot or "/", STATE_REL)
        self.faults = {}
        if self.test:
            for item in os.environ.get("OMARCHY_PR_TEST_FAULT", "").split(","):
                if ":" in item:
                    k, v = item.split(":", 1)
                    self.faults[k.strip()] = v.strip()

    def fault(self, kind, value):
        return self.test and self.faults.get(kind) == value

    def owner_ok(self, st):
        want = self.euid if self.test else 0
        return st.st_uid == want and not (st.st_mode & 0o022)

    def root_fd(self):
        return os.open(self.sysroot or "/", os.O_RDONLY | os.O_DIRECTORY)


# ------------------------------------------------------------------ paths

def dest_ok(dest):
    if not isinstance(dest, str) or not dest.startswith("/") or "\x00" in dest:
        return False
    if "//" in dest or dest.endswith("/"):
        return False
    parts = dest.split("/")[1:]
    for p in parts:
        if p in ("", ".", ".."):
            return False
    if BIN_RE.match(dest):
        return True
    for root in SHARE_ROOTS:
        if dest.startswith(root):
            rest = dest[len(root):].split("/")
            return all(SEGMENT.match(p) for p in rest)
    return False


def dir_ok(d):
    if not isinstance(d, str) or "//" in d or d.endswith("/") or "\x00" in d:
        return False
    for root in DIR_ROOTS:
        if d.startswith(root + "/"):
            rest = d[len(root) + 1:].split("/")
            return all(SEGMENT.match(p) for p in rest)
    return False


def walk_parent(ctx, dest, create=False, created=None):
    """Open the parent directory of dest without following any symlink.
    Returns (dirfd, leafname). Optionally creates missing directories
    (recorded in created, as absolute paths without sysroot)."""
    parts = dest.strip("/").split("/")
    fd = ctx.root_fd()
    cur = ""
    try:
        for comp in parts[:-1]:
            cur += "/" + comp
            try:
                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                if not dir_ok(cur):
                    raise Refuse("will not create the folder %s" % cur)
                os.mkdir(comp, 0o755, dir_fd=fd)
                if created is not None:
                    created.append(cur)
                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                if not ctx.test:
                    os.fchown(nfd, 0, 0)
                os.fchmod(nfd, 0o755)
            except OSError as e:
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise Refuse("%s is a symlink or not a folder; refusing to follow it" % cur)
                raise
            st = os.fstat(nfd)
            if not ctx.owner_ok(st):
                os.close(nfd)
                raise Refuse("%s is not owned by root or is writable by others; refusing" % cur)
            os.close(fd)
            fd = nfd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def leaf_state(ctx, dest):
    """('-', None) when absent, ('?', st) when not a plain file or a symlink,
    (sha, st) for a regular file. Never follows a symlink."""
    try:
        fd, name = walk_parent(ctx, dest)
    except FileNotFoundError:
        return "-", None
    except Refuse:
        return "?", None
    try:
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return "-", None
        if not stat.S_ISREG(st.st_mode):
            return "?", st
        try:
            ffd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError:
            return "?", st
        try:
            return sha_fd(ffd), os.fstat(ffd)
        finally:
            os.close(ffd)
    finally:
        os.close(fd)


def sha_fd(fd):
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def read_regular(path_or_fd, dir_fd=None, limit=MAX_FILE_BYTES):
    fd = os.open(path_or_fd, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Refuse("not a plain file")
        if st.st_size > limit:
            raise Refuse("file too large")
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks), st
    finally:
        os.close(fd)


def atomic_write_at(ctx, dirfd, name, data, mode, uid, gid):
    tmp = ".%s.omarchy-pr-try.%s" % (name[:80], secrets.token_hex(6))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        if not ctx.test:
            os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    os.close(fd)
    try:
        os.rename(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dirfd)
        except OSError:
            pass
        raise
    os.fsync(dirfd)


def write_file_plain(path, data, mode=0o600):
    """Write a file inside the private state folder (already validated)."""
    d, name = os.path.split(path)
    dfd = os.open(d, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        tmp = ".%s.%s" % (name, secrets.token_hex(6))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        os.fsync(dfd)
    finally:
        os.close(dfd)


# ------------------------------------------------------------------ state folder

def ensure_state(ctx, create):
    sd = ctx.state_dir
    parent = os.path.dirname(sd)
    if create:
        os.makedirs(parent, exist_ok=True)
    try:
        st = os.lstat(sd)
    except FileNotFoundError:
        if not create:
            return False
        os.mkdir(sd, 0o700)
        st = os.lstat(sd)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise Refuse("%s is not a plain folder; refusing to use it" % sd)
    want = ctx.euid if ctx.test else 0
    if st.st_uid != want:
        raise Refuse("%s is not owned by root; refusing to use it" % sd)
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(sd, 0o700)
    orig = os.path.join(sd, "orig")
    if create and not os.path.lexists(orig):
        os.mkdir(orig, 0o700)
    if os.path.lexists(orig) and (os.path.islink(orig) or not os.path.isdir(orig)):
        raise Refuse("%s is not a plain folder" % orig)
    return True


def journal_path(ctx):
    return os.path.join(ctx.state_dir, "manifest.tsv")


FIELDS = ("kind", "dest", "orig", "new", "omode", "ouid", "ogid", "nmode", "state")


def write_journal(ctx, header, entries):
    lines = [JOURNAL_MAGIC + " " + header]
    for e in entries:
        lines.append("\t".join(str(e.get(f, "-")) for f in FIELDS))
    body = "\n".join(lines) + "\n"
    digest = hashlib.sha256(body.encode()).hexdigest()
    write_file_plain(journal_path(ctx), (body + "#sha256=" + digest + "\n").encode())


def read_journal(ctx):
    """Returns (format, header, entries, problems, intact)."""
    data, _ = read_regular(journal_path(ctx))
    text = data.decode("utf-8", "replace")
    lines = text.split("\n")
    if not lines or not lines[0].startswith(JOURNAL_MAGIC):
        return "v1", "", [], [], True
    header = lines[0][len(JOURNAL_MAGIC):].strip()
    body_lines = [lines[0]]
    entries, problems = [], []
    intact = False
    seen_sum = False
    for ln in lines[1:]:
        if ln.startswith("#sha256="):
            body = "\n".join(body_lines) + "\n"
            intact = (not seen_sum) and hashlib.sha256(body.encode()).hexdigest() == ln[len("#sha256="):].strip()
            seen_sum = True
            continue
        if ln == "":
            continue
        if seen_sum:
            intact = False   # anything after the checksum line is not ours
        else:
            body_lines.append(ln)
        cols = ln.split("\t")
        e = dict(zip(FIELDS, cols + ["-"] * (len(FIELDS) - len(cols))))
        ok = True
        if e["kind"] == "D":
            ok = dir_ok(e["dest"])
        elif e["kind"] in ("A", "M", "R"):
            ok = dest_ok(e["dest"])
        else:
            ok = False
        if not ok:
            problems.append("the manifest has a path I will not touch (%s); skipping it" % safe_text(e["dest"])[:200])
            continue
        entries.append(e)
    return "v2", header, entries, problems, intact


def header_get(header, key):
    for tok in header.split():
        if tok.startswith(key + "="):
            return tok[len(key) + 1:]
    return ""


def header_set(header, key, value):
    toks = [t for t in header.split() if not t.startswith(key + "=")]
    toks.append("%s=%s" % (key, value))
    return " ".join(toks)


# ------------------------------------------------------------------ commands

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def cmd_preflight(ctx, args):
    existed = os.path.lexists(ctx.state_dir)
    ensure_state(ctx, create=False) if existed else None
    if existed and os.path.lexists(journal_path(ctx)):
        out("ACTIVE journal present")
        return 3
    # Can we create and write it?
    ensure_state(ctx, create=True)
    probe = os.path.join(ctx.state_dir, ".probe")
    write_file_plain(probe, b"ok")
    os.unlink(probe)
    if not existed:
        os.rmdir(os.path.join(ctx.state_dir, "orig"))
        os.rmdir(ctx.state_dir)
    out("OK state folder usable")
    return 0


def parse_plan(path):
    data, _ = read_regular(path, limit=4 * 1024 * 1024)
    plan = []
    for ln in data.decode().splitlines():
        if not ln.strip():
            continue
        cols = ln.split("\t")
        if len(cols) != 5:
            raise Refuse("malformed plan line")
        kind, dest, src, new, mode = cols
        if kind not in ("A", "M", "R"):
            raise Refuse("unknown plan entry kind")
        if not dest_ok(dest):
            raise Refuse("refusing to touch %s" % safe_text(dest)[:200])
        if kind != "R":
            if not HEX64.match(new):
                raise Refuse("bad checksum in plan")
            if mode not in ("644", "755"):
                raise Refuse("bad file mode in plan")
            if not os.path.isabs(src):
                raise Refuse("bad source in plan")
        plan.append({"kind": kind, "dest": dest, "src": src, "new": new, "mode": mode})
    if len(plan) > MAX_ENTRIES:
        raise Refuse("plan too large")
    dests = [p["dest"] for p in plan]
    if len(set(dests)) != len(dests):
        raise Refuse("plan names a destination twice")
    return plan


def store_original(ctx, sha, data):
    path = os.path.join(ctx.state_dir, "orig", sha)
    if os.path.lexists(path):
        have, _ = read_regular(path)
        if hashlib.sha256(have).hexdigest() == sha:
            return
        os.unlink(path)
    write_file_plain(path, data, 0o600)
    have, _ = read_regular(path)
    if hashlib.sha256(have).hexdigest() != sha:
        raise Refuse("the saved copy of an original file does not verify")


def cmd_apply(ctx, args):
    if len(args) != 2:
        raise Refuse("usage: apply PLAN TXID")
    plan_path, txid = args
    if not re.match(r"^[A-Za-z0-9._-]{1,80}$", txid):
        raise Refuse("bad transaction id")
    plan = parse_plan(plan_path)
    if os.path.lexists(journal_path(ctx)):
        raise Refuse("a PR transaction is already recorded in %s; restore it first" % ctx.state_dir)
    ensure_state(ctx, create=True)

    # 1. Inspect every destination and save every original before anything changes.
    entries = []
    for p in plan:
        cur, st = leaf_state(ctx, p["dest"])
        if cur == "?":
            raise Refuse("%s is a symlink, not a plain file, or sits under a folder I will not follow" % p["dest"])
        e = {"kind": p["kind"], "dest": p["dest"], "orig": cur, "new": "-", "omode": "-",
             "ouid": "-", "ogid": "-", "nmode": p["mode"] if p["kind"] != "R" else "-", "state": "planned"}
        if p["kind"] == "A" and cur != "-":
            raise Refuse("%s appeared since the plan was made" % p["dest"])
        if p["kind"] in ("M", "R"):
            if cur == "-":
                raise Refuse("%s disappeared since the plan was made" % p["dest"])
            fd, name = walk_parent(ctx, p["dest"])
            try:
                data, fst = read_regular(name, dir_fd=fd)
            finally:
                os.close(fd)
            if hashlib.sha256(data).hexdigest() != cur:
                raise Refuse("%s changed while it was being saved" % p["dest"])
            store_original(ctx, cur, data)
            e.update(omode="%o" % stat.S_IMODE(fst.st_mode), ouid=str(fst.st_uid), ogid=str(fst.st_gid))
        if p["kind"] != "R":
            e["new"] = p["new"]
        entries.append(e)

    header = "tx=%s state=applying" % txid
    write_journal(ctx, header, entries)
    out("SAVED %d originals" % sum(1 for e in entries if e["kind"] in ("M", "R")))

    # 2. Apply, journalling after every step.
    created = []
    applied = 0
    try:
        for i, p in enumerate(plan):
            e = entries[i]
            if ctx.fault("sys-write", p["dest"]):
                raise Refuse("could not write %s (injected test fault)" % p["dest"])
            if p["kind"] == "R":
                fd, name = walk_parent(ctx, p["dest"])
                try:
                    os.unlink(name, dir_fd=fd)
                    os.fsync(fd)
                finally:
                    os.close(fd)
            else:
                data, _ = read_regular(p["src"])
                if hashlib.sha256(data).hexdigest() != p["new"]:
                    raise Refuse("the staged copy of %s does not match the plan; refusing" % p["dest"])
                before = len(created)
                fd, name = walk_parent(ctx, p["dest"], create=True, created=created)
                try:
                    if len(created) > before:
                        for d in created[before:]:
                            entries.append({"kind": "D", "dest": d, "orig": "-", "new": "-", "omode": "-",
                                            "ouid": "-", "ogid": "-", "nmode": "755", "state": "created"})
                    st = None
                    try:
                        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    if st is not None and not stat.S_ISREG(st.st_mode):
                        raise Refuse("%s is a symlink or not a plain file; refusing" % p["dest"])
                    uid = int(e["ouid"]) if e["ouid"] not in ("-", "") else 0
                    gid = int(e["ogid"]) if e["ogid"] not in ("-", "") else 0
                    atomic_write_at(ctx, fd, name, data, int(p["mode"], 8), uid, gid)
                finally:
                    os.close(fd)
                cur, _ = leaf_state(ctx, p["dest"])
                if cur != p["new"]:
                    raise Refuse("could not verify %s after writing it" % p["dest"])
            e["state"] = "applied"
            applied += 1
            write_journal(ctx, header, entries)
            if ctx.test and ctx.faults.get("sys-kill-after") == str(applied):
                # Simulate the whole tool dying (power loss, kill -9) mid-transaction.
                try:
                    os.kill(os.getppid(), signal.SIGKILL)
                finally:
                    os.kill(os.getpid(), signal.SIGKILL)
    except (Refuse, OSError) as ex:
        write_journal(ctx, header_set(header, "state", "failed"), entries)
        err(str(ex) if isinstance(ex, Refuse) else "could not write %s (%s)" % (plan[applied]["dest"], ex.strerror))
        out("FAILED after %d of %d" % (applied, len(plan)))
        return 1
    write_journal(ctx, header_set(header, "state", "applied"), entries)
    out("APPLIED %d" % applied)
    return 0


def cleanup_tmp_near(ctx, dest):
    try:
        fd, name = walk_parent(ctx, dest)
    except (OSError, Refuse):
        return
    try:
        for f in os.listdir(fd):
            if f.startswith("." + name[:80] + ".omarchy-pr-try.") or f == name + ".omarchy-pr-try.new":
                try:
                    st = os.stat(f, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISREG(st.st_mode):
                        os.unlink(f, dir_fd=fd)
                except OSError:
                    pass
    finally:
        os.close(fd)


def original_bytes(ctx, sha):
    path = os.path.join(ctx.state_dir, "orig", sha)
    data, _ = read_regular(path)
    if hashlib.sha256(data).hexdigest() != sha:
        raise Refuse("the saved system files do not match their checksum")
    return data


def cmd_restore(ctx, args):
    accept = "--accept-manifest" in args
    if not os.path.lexists(journal_path(ctx)):
        out("NOTHING the system files were already put back")
        return 0
    ensure_state(ctx, create=False)
    fmt, header, entries, problems, intact = read_journal(ctx)
    if fmt != "v2":
        out("LEGACY")
        return 4
    for p in problems:
        err(p)
    if not intact and not accept:
        err("the transaction manifest does not match its checksum (corrupted or edited); nothing was restored automatically")
        out("CORRUPT")
        return 1

    files = [e for e in entries if e["kind"] in ("A", "M", "R")]
    dirs = [e for e in entries if e["kind"] == "D"]

    # Decide and verify everything before changing anything.
    todo = []
    kept = []
    for e in files:
        cur, st = leaf_state(ctx, e["dest"])
        k = e["kind"]
        if k == "M":
            if cur == e["orig"]:
                if st is not None and e["omode"] not in ("-", "") and stat.S_IMODE(st.st_mode) != int(e["omode"], 8):
                    todo.append(("write", e))   # only the mode changed
                else:
                    todo.append(("check", e))
            elif cur == e["new"]:
                todo.append(("write", e))
            else:
                kept.append(e)
        elif k == "R":
            if cur == "-":
                todo.append(("write", e))
            elif cur == e["orig"]:
                todo.append(("check", e))
            else:
                kept.append(e)
        elif k == "A":
            if cur == e["new"]:
                todo.append(("remove", e))
            elif cur == "-":
                pass
            else:
                kept.append(e)
    try:
        for action, e in todo:
            if action == "write":
                original_bytes(ctx, e["orig"])
    except (Refuse, OSError) as ex:
        err(str(ex) if isinstance(ex, Refuse) else "a saved original is missing (%s)" % ex)
        err("nothing was put back")
        out("BADBACKUP")
        return 1

    failed = 0
    restored = 0
    header = header_set(header, "state", "restoring")
    for action, e in reversed(todo):
        try:
            if ctx.fault("restore-fail", e["dest"]):
                raise Refuse("could not restore %s (injected test fault)" % e["dest"])
            if action == "write":
                data = original_bytes(ctx, e["orig"])
                fd, name = walk_parent(ctx, e["dest"], create=False)
                try:
                    mode = int(e["omode"], 8) if e["omode"] not in ("-", "") else 0o644
                    uid = int(e["ouid"]) if e["ouid"] not in ("-", "") else 0
                    gid = int(e["ogid"]) if e["ogid"] not in ("-", "") else 0
                    atomic_write_at(ctx, fd, name, data, mode, uid, gid)
                finally:
                    os.close(fd)
                restored += 1
            elif action == "remove":
                fd, name = walk_parent(ctx, e["dest"])
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISREG(st.st_mode):
                        os.unlink(name, dir_fd=fd)
                        os.fsync(fd)
                finally:
                    os.close(fd)
                restored += 1
            e["state"] = "restored"
        except FileNotFoundError:
            # A parent folder is gone (a package removed it): nothing of ours remains there.
            e["state"] = "restored"
        except (Refuse, OSError) as ex:
            failed += 1
            err(str(ex) if isinstance(ex, Refuse) else "could not restore %s (%s)" % (e["dest"], ex.strerror))
        cleanup_tmp_near(ctx, e["dest"])
        write_journal(ctx, header, entries) if intact else None
    for e in kept:
        err("%s changed since the PR was installed (a package update?); I left it alone" % e["dest"])
        e["state"] = "kept"
    for e in reversed(dirs):
        try:
            fd, name = walk_parent(ctx, e["dest"])
            try:
                os.rmdir(name, dir_fd=fd)
            finally:
                os.close(fd)
            e["state"] = "removed"
        except FileNotFoundError:
            e["state"] = "removed"
        except OSError as ex:
            if ex.errno == errno.ENOTEMPTY:
                e["state"] = "kept"
            else:
                err("could not remove the folder %s (%s)" % (e["dest"], ex.strerror))
        except Refuse as ex:
            err(str(ex))

    # Verify.
    bad = verify_entries(ctx, entries, "restored", quiet=False)
    final = "restored" if (failed == 0 and bad == 0 and not problems) else "restore-incomplete"
    if intact:
        write_journal(ctx, header_set(header, "state", final), entries)
    out("RESULT restored=%d kept=%d failed=%d unverified=%d" % (restored, len(kept), failed, bad))
    return 0 if final == "restored" else 1


def verify_entries(ctx, entries, expect, quiet):
    bad = 0
    for e in entries:
        k = e["kind"]
        if k == "D":
            continue
        if e.get("state") == "kept":
            continue
        cur, st = leaf_state(ctx, e["dest"])
        if expect == "applied":
            want = "-" if k == "R" else e["new"]
            okay = cur == want
        else:
            want = "-" if k == "A" else e["orig"]
            okay = cur == want
            if okay and k in ("M", "R") and st is not None and e["omode"] not in ("-", ""):
                if stat.S_IMODE(st.st_mode) != int(e["omode"], 8):
                    okay = False
                    if not quiet:
                        err("%s has mode %o, expected %s" % (e["dest"], stat.S_IMODE(st.st_mode), e["omode"]))
                elif not ctx.test and (str(st.st_uid) != e["ouid"] or str(st.st_gid) != e["ogid"]):
                    okay = False
                    if not quiet:
                        err("%s has the wrong owner" % e["dest"])
        if not okay:
            bad += 1
            if not quiet:
                err("%s is not as expected after %s (have %s, want %s)" % (
                    e["dest"], "applying" if expect == "applied" else "restoring", cur[:12], want[:12]))
        if expect == "restored":
            try:
                fd, name = walk_parent(ctx, e["dest"])
                try:
                    for f in os.listdir(fd):
                        if f.startswith("." + name[:80] + ".omarchy-pr-try."):
                            bad += 1
                            if not quiet:
                                err("a temporary file from this tool is still next to %s" % e["dest"])
                finally:
                    os.close(fd)
            except (OSError, Refuse):
                pass
    return bad


def cmd_verify(ctx, args):
    expect = "applied"
    if len(args) == 2 and args[0] == "--expect" and args[1] in ("applied", "restored"):
        expect = args[1]
    if not os.path.lexists(journal_path(ctx)):
        out("NOJOURNAL")
        return 0 if expect == "restored" else 1
    fmt, header, entries, problems, intact = read_journal(ctx)
    if fmt != "v2":
        out("LEGACY")
        return 4
    for p in problems:
        err(p)
    if not intact:
        err("the transaction manifest does not match its checksum")
    bad = verify_entries(ctx, entries, expect, quiet=False)
    out("VERIFY expect=%s bad=%d intact=%s" % (expect, bad, "yes" if intact else "no"))
    return 0 if (bad == 0 and intact and not problems) else 1


def cmd_show(ctx, args):
    if not os.path.lexists(journal_path(ctx)):
        return 1
    data, _ = read_regular(journal_path(ctx))
    sys.stdout.write(data.decode("utf-8", "replace"))
    return 0


def cmd_finish(ctx, args):
    if not os.path.lexists(ctx.state_dir):
        return 0
    ensure_state(ctx, create=False)
    if os.path.lexists(journal_path(ctx)):
        fmt, header, entries, problems, intact = read_journal(ctx)
        if fmt == "v2" and header_get(header, "state") != "restored" and "--force" not in args:
            err("the transaction is not marked restored; keeping %s" % ctx.state_dir)
            return 1
    orig = os.path.join(ctx.state_dir, "orig")
    if os.path.isdir(orig) and not os.path.islink(orig):
        for f in os.listdir(orig):
            os.unlink(os.path.join(orig, f))
        os.rmdir(orig)
    for f in os.listdir(ctx.state_dir):
        p = os.path.join(ctx.state_dir, f)
        if os.path.isfile(p) and not os.path.islink(p):
            os.unlink(p)
    try:
        os.rmdir(ctx.state_dir)
    except OSError as ex:
        err("could not remove %s (%s)" % (ctx.state_dir, ex.strerror))
        return 1
    return 0


def cmd_manual(ctx, args):
    if not os.path.lexists(journal_path(ctx)):
        out("No transaction is recorded in %s." % ctx.state_dir)
        return 0
    fmt, header, entries, problems, intact = read_journal(ctx)
    out("Manual recovery (run as root, check each file first):")
    for e in entries:
        k, d = e["kind"], ctx.sysroot + e["dest"]
        src = os.path.join(ctx.state_dir, "orig", e["orig"])
        if k == "M":
            out("  install -m %s -o %s -g %s '%s' '%s'   # original sha256 %s" % (e["omode"], e["ouid"], e["ogid"], src, d, e["orig"]))
        elif k == "R":
            out("  install -m %s -o %s -g %s '%s' '%s'   # the PR removed it" % (e["omode"], e["ouid"], e["ogid"], src, d))
        elif k == "A":
            out("  rm -f -- '%s'   # added by the PR (sha256 %s)" % (d, e["new"]))
        elif k == "D":
            out("  rmdir -- '%s'   # created for the PR, if empty" % d)
    out("Then check: sha256sum each file against the value shown, and run 'omarchy-pr-try verify-recovery'.")
    return 0


COMMANDS = {"preflight": cmd_preflight, "apply": cmd_apply, "restore": cmd_restore,
            "verify": cmd_verify, "show": cmd_show, "finish": cmd_finish, "manual": cmd_manual}


def main(argv):
    sysroot = ""
    if len(argv) >= 2 and argv[0] == "--sysroot":
        sysroot = argv[1]
        argv = argv[2:]
    if not argv or argv[0] not in COMMANDS:
        err("usage: omarchy-pr-try-sysops [--sysroot DIR] {%s}" % "|".join(COMMANDS))
        return 2
    os.umask(0o077)
    try:
        ctx = Ctx(sysroot)
        return COMMANDS[argv[0]](ctx, argv[1:])
    except Refuse as ex:
        err(str(ex))
        return 1
    except OSError as ex:
        err("system error: %s" % ex)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
