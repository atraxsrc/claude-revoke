#!/usr/bin/env python3
"""
claude-revoke - audit and revoke Claude Code access on Linux.

An installer-style terminal UI in reverse: it scans everything Claude Code
has left behind (trusted folders, auto-approve rules, extra directories,
session transcripts, hooks, MCP servers), lets you tick what to remove,
and QUARANTINES it instead of deleting, so every run can be undone.

Usage:
  ./claude_revoke.py                  interactive TUI
  ./claude_revoke.py --report         print the audit and exit (no changes)
  ./claude_revoke.py --report --only stale   list only stale items
  ./claude_revoke.py --dry-run        TUI, but "apply" changes nothing
  ./claude_revoke.py --restore DIR    undo a previous run from its quarantine folder
  ./claude_revoke.py --roots ~ /srv   folders to search for per-project settings
  ./claude_revoke.py --notify         scan and send a desktop notification if anything needs a look
  ./claude_revoke.py --schedule weekly   run --notify on a systemd user timer (weekly | daily | off)

Needs only the Python 3 standard library. Close all Claude Code sessions
before applying changes (it rewrites ~/.claude.json while running).
"""
import argparse
import curses
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

__version__ = "0.3.0"

# CLAUDE_REVOKE_HOME lets you point the tool at a test home folder.
HOME = Path(os.environ.get("CLAUDE_REVOKE_HOME", str(Path.home()))).expanduser()
CLAUDE_JSON = HOME / ".claude.json"
CLAUDE_DIR = HOME / ".claude"
SESSIONS_DIR = CLAUDE_DIR / "projects"
GLOBAL_SETTINGS = CLAUDE_DIR / "settings.json"
QUARANTINE_ROOT = HOME / ".claude-revoke-quarantine"

# Bar/accent color = an index into YOUR terminal theme's palette (0-15), so the
# UI always matches the theme. Names: black red green yellow blue magenta cyan
# white, prefix "bright-" for 8-15. Set with --accent or CLAUDE_REVOKE_ACCENT.
PALETTE = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]


def parse_accent(v):
    v = str(v).strip().lower()
    if v.isdigit() and 0 <= int(v) <= 15:
        return int(v)
    bright = v.startswith("bright-")
    name = v[7:] if bright else v
    if name in PALETTE:
        return PALETTE.index(name) + (8 if bright else 0)
    raise ValueError(f"unknown color '{v}' (use 0-15 or e.g. blue, bright-cyan)")


ACCENT = 4

SKIP_DIRS = {
    "node_modules", ".git", ".cache", ".npm", ".cargo", ".rustup", ".venv", "venv",
    "__pycache__", ".claude-revoke-quarantine", "Trash",
    ".local", ".mozilla", ".steam", "snap",
}
# Only skipped directly under / ("dev" or "run" deeper down can be real projects).
SKIP_ROOT_DIRS = {"proc", "sys", "dev", "run"}

DENY_RULES = [
    "Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.gnupg/**)", "Read(~/.kube/**)",
    "Read(~/.config/gh/**)", "Read(**/.env)", "Read(**/.env.*)", "Read(**/*.pem)",
    "Read(**/*.key)", "Read(**/id_rsa*)", "Read(**/id_ed25519*)",
]

# Group "v" is the secret value itself (what gets redacted and entropy-checked).
SECRET_RX = {
    "AWS key": re.compile(r"(?P<v>AKIA[0-9A-Z]{16})"),
    "private key": re.compile(r"(?P<v>-----BEGIN [A-Z ]*PRIVATE KEY-----)"),
    "GitHub token": re.compile(r"(?P<v>gh[pousr]_[A-Za-z0-9]{36,})"),
    "API key (sk-)": re.compile(r"(?P<v>sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,})"),
    "Slack token": re.compile(r"(?P<v>xox[baprs]-[A-Za-z0-9-]{10,})"),
    "secret assignment": re.compile(
        r"(?i)(api[_-]?key|secret|token|passw(?:or)?d)[\\\"']*\s*[:=]\s*[\\\"']*(?P<v>[A-Za-z0-9_\-/+]{12,})"),
}
# Fixed-format matches that need no "does this look random" check.
NO_ENTROPY_CHECK = {"AWS key", "private key"}
PLACEHOLDER_WORDS = ("example", "placeholder", "your_", "your-", "xxxx", "changeme", "redacted", "dummy")


# --------------------------------------------------------------------------- model
@dataclass
class Item:
    kind: str          # trust | session | settings | gallow | gdir | ghooks | gmode | gmcp | harden
    key: str           # what the action acts on (path, rule, server name)
    title: str
    badge: str = ""
    detail: list = field(default_factory=list)
    risky: bool = False
    stale: bool = False
    selected: bool = False
    size: int = 0
    name: str = ""     # short display name (folder name)
    where: str = ""    # parent location, shown dimmer

    def __post_init__(self):
        if not self.name:
            self.name, self.where = split_name(self.title)


@dataclass
class Category:
    name: str
    help: str
    items: list


@dataclass
class Secret:
    kind: str
    file: str          # transcript file, relative to the project's transcripts folder
    line: int
    redacted: str      # e.g. "password=p4ss...(19 chars)"


# --------------------------------------------------------------------------- helpers
def load_json(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# Claude Code's files are hand-editable, so any key can hold the wrong type.
def as_dict(v):
    return v if isinstance(v, dict) else {}


def as_list(v):
    return v if isinstance(v, list) else []


def str_list(v):
    return [x for x in as_list(v) if isinstance(x, str)]


def write_json_atomic(p, data):
    # Keep the original file's permissions (~/.claude.json is usually 0600);
    # files that don't exist yet are created private.
    try:
        mode = os.stat(p).st_mode & 0o777
    except OSError:
        mode = 0o600
    tmp = Path(str(p) + ".claude-revoke.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def encode_path(p):
    # Claude Code names each transcript folder after the project path with
    # every non-alphanumeric character turned into "-".
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def short_home(p):
    p = str(p)
    for home in {str(HOME), str(Path.home())}:
        if p == home:
            return "~"
        if p.startswith(home + "/"):
            return "~" + p[len(home):]
    return p


def split_name(title):
    """'/home/me/code/app  [settings.json]' -> ('app [settings.json]', '~/code')"""
    m = re.match(r"^(/[^\s]*)(.*)$", title)
    if not m:
        return title, ""
    path, rest = m.group(1).rstrip("/"), m.group(2).strip()
    name = (os.path.basename(path) or path) + (" " + rest if rest else "")
    return name, short_home(os.path.dirname(path))


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def fmt_date(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "?"


def is_broad(rule):
    r = rule.replace(" ", "")
    if r in ("Bash", "Bash(*)", "Bash(:*)", "Edit", "Write", "Read", "WebFetch", "MultiEdit"):
        return True
    if r.endswith("(*)") or r.endswith("(**)") or "(/**)" in r or "(~/**)" in r:
        return True
    if r.startswith("mcp__") and r.count("__") == 1:   # whole MCP server allowed
        return True
    return False


def analyze_settings(data):
    """Return (list of risk strings, number of allow rules)."""
    if not isinstance(data, dict):
        return ["unreadable or invalid JSON"], 0
    risks = []
    perms = as_dict(data.get("permissions"))
    allow = str_list(perms.get("allow"))
    if perms.get("defaultMode") == "bypassPermissions":
        risks.append("bypassPermissions mode: no prompts at all")
    for r in allow:
        if is_broad(r):
            risks.append(f"broad allow rule: {r}")
    for d in as_list(perms.get("additionalDirectories")):
        risks.append(f"extra directory granted: {d}")
    hooks = data.get("hooks")
    if hooks:
        events = ", ".join(hooks.keys()) if isinstance(hooks, dict) else "?"
        risks.append(f"hooks that run commands automatically: {events}")
    mcp = data.get("mcpServers")
    if mcp:
        risks.append("defines MCP servers: " + (", ".join(mcp.keys()) if isinstance(mcp, dict) else "?"))
    if data.get("enableAllProjectMcpServers"):
        risks.append("auto-enables all project MCP servers")
    return risks, len(allow)


def looks_random(v):
    """Real credentials mix letters and digits and have high entropy;
    identifiers like generateTokenForUser or your_password_here do not."""
    if not (re.search(r"[0-9]", v) and re.search(r"[A-Za-z]", v)):
        return False
    counts = {}
    for ch in v:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(v)
    entropy = -sum(c / n * math.log2(c / n) for c in counts.values())
    return entropy >= 3.0


def plausible_secret(kind, v):
    low = v.lower()
    if any(w in low for w in PLACEHOLDER_WORDS):
        return False
    return kind in NO_ENTROPY_CHECK or looks_random(v)


def redact(kind, m):
    v = m.group("v")
    if kind == "private key":       # the header line is not the secret
        return v
    # keep what led up to the value (e.g. "password=") and its first 4 chars
    prefix = m.group(0)[: m.start("v") - m.start(0)]
    return f"{prefix}{v[:4]}...({len(v)} chars)"


def scan_secrets(d, max_bytes=300 * 1024 * 1024, max_hits=500):
    """One Secret per distinct credential-looking value in d's transcripts, with where it was first seen."""
    hits, seen, read = [], set(), 0
    for f in sorted(d.rglob("*.jsonl")):
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                for n, line in enumerate(fh, 1):
                    read += len(line)
                    for kind, rx in SECRET_RX.items():
                        for m in rx.finditer(line):
                            v = m.group("v")
                            if (kind, v) in seen or not plausible_secret(kind, v):
                                continue
                            seen.add((kind, v))
                            hits.append(Secret(kind, f.relative_to(d).as_posix(), n, redact(kind, m)))
                            if len(hits) >= max_hits:
                                return hits
                    if read > max_bytes:
                        return hits
        except OSError:
            continue
    return hits


def running_claude():
    pids = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    for p in proc.iterdir():
        if not p.name.isdigit() or p.name == str(os.getpid()):
            continue
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except OSError:
            continue
        if re.search(r"(^|/)claude(\s|$)", cmd):
            pids.append(p.name)
    return pids


# --------------------------------------------------------------------------- scanning
def scan_trust(projects, session_names):
    items = []
    for path, cfg in sorted(projects.items()):
        cfg = as_dict(cfg)
        exists = Path(path).is_dir()
        allowed = str_list(cfg.get("allowedTools"))
        mcp = as_dict(cfg.get("mcpServers"))
        broad = [r for r in allowed if is_broad(r)]
        badge = []
        if not exists:
            badge.append("MISSING")
        if allowed:
            badge.append(f"allow:{len(allowed)}")
        if mcp:
            badge.append(f"mcp:{len(mcp)}")
        detail = [
            f"Folder: {path}" + ("   [no longer exists]" if not exists else ""),
            f"Trust accepted: {cfg.get('hasTrustDialogAccepted', 'unknown')}",
            f"Session transcripts saved: {'yes' if encode_path(path) in session_names else 'no'}",
        ]
        if allowed:
            detail.append("Always-allowed tools: " + ", ".join(allowed[:8]) + (" ..." if len(allowed) > 8 else ""))
        if broad:
            detail.append("BROAD rules: " + ", ".join(broad))
        if mcp:
            detail.append("MCP servers: " + ", ".join(mcp.keys()))
        items.append(Item("trust", path, path, "  ".join(badge), detail,
                          risky=bool(broad or mcp), stale=not exists, selected=not exists))
    return items


def scan_sessions(projects, secret_scan):
    items = []
    if not SESSIONS_DIR.is_dir():
        return items
    enc = {encode_path(p): p for p in projects}
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        orig = enc.get(d.name)
        if orig is None:
            guess = d.name.replace("-", "/")
            orig = guess if Path(guess).is_dir() else None
        files, size, mtime = 0, 0, 0
        for f in d.rglob("*"):
            try:
                st = f.stat()
            except OSError:
                continue
            if f.is_file():
                files += 1
                size += st.st_size
                mtime = max(mtime, st.st_mtime)
        hits = scan_secrets(d) if secret_scan else []
        stale = orig is not None and not Path(orig).exists()
        badge = f"{human(size):>7}  {fmt_date(mtime)}"
        if hits:
            badge += f"  SECRETS:{len(hits)}"
        detail = [
            f"Transcripts: {d}",
            f"Project: {orig or 'unknown (not in trusted list)'}" + ("   [no longer exists]" if stale else ""),
            f"{files} files, {human(size)}, last used {fmt_date(mtime)}",
        ]
        if hits:
            kinds = {}
            for h in hits:
                kinds[h.kind] = kinds.get(h.kind, 0) + 1
            detail.append("Possible secrets: " + ", ".join(f"{k} x{v}" for k, v in kinds.items())
                          + "  -> rotate them, then remove")
            detail += [f"  {h.kind}  {h.file}:{h.line}  {h.redacted}" for h in hits[:3]]
            if len(hits) > 3:
                detail.append(f"  ... and {len(hits) - 3} more")
        else:
            detail.append("Transcripts hold copies of every file Claude read in that project.")
        items.append(Item("session", str(d), orig or f"(unknown) {d.name}", badge, detail,
                          risky=bool(hits), stale=stale, selected=stale, size=size))
    return items


def scan_settings(roots):
    items, seen = [], set()
    claude_dir = CLAUDE_DIR.resolve() if CLAUDE_DIR.exists() else CLAUDE_DIR
    for root in roots:
        root = Path(root).expanduser()
        for dirpath, dirnames, _ in os.walk(root, onerror=lambda e: None):
            skip = SKIP_DIRS | SKIP_ROOT_DIRS if dirpath == "/" else SKIP_DIRS
            dirnames[:] = [d for d in dirnames if d not in skip]
            if ".claude" in dirnames:
                dirnames.remove(".claude")
                cdir = Path(dirpath) / ".claude"
                try:
                    if cdir.resolve() == claude_dir:
                        continue
                except OSError:
                    continue
                for name in ("settings.json", "settings.local.json"):
                    f = cdir / name
                    if f.is_file() and f not in seen:
                        seen.add(f)
                        items.append(settings_item(f))
    items.sort(key=lambda i: i.key)
    return items


def settings_item(f):
    data = load_json(f)
    risks, n_allow = analyze_settings(data)
    proj = f.parent.parent
    local = f.name == "settings.local.json"
    detail = [
        f"File: {f}",
        "Personal file: your clicked 'always allow' choices" if local
        else "Shared project file: may be committed to git (removing affects teammates)",
    ]
    if (proj / ".git").exists() and not local:
        detail.append("Project is a git repo - check `git status` after removing.")
    detail += risks or ["No risky entries found."]
    badge = f"allow:{n_allow}" + ("  local" if local else "  shared")
    return Item("settings", str(f), f"{proj}  [{f.name}]", badge, detail, risky=bool(risks))


def scan_global(cj):
    items = []
    g = as_dict(load_json(GLOBAL_SETTINGS))
    perms = as_dict(g.get("permissions"))
    for r in str_list(perms.get("allow")):
        items.append(Item("gallow", r, f"global allow rule: {r}", "BROAD" if is_broad(r) else "",
                          [f"In {GLOBAL_SETTINGS}", "Applies in every project."], risky=is_broad(r)))
    for d in str_list(perms.get("additionalDirectories")):
        exists = Path(os.path.expanduser(d)).exists()
        items.append(Item("gdir", d, f"global extra directory: {d}", "" if exists else "MISSING",
                          [f"In {GLOBAL_SETTINGS}", "Claude can reach this folder from any project."],
                          risky=True, stale=not exists, selected=not exists))
    hooks = g.get("hooks")
    if hooks:
        events = ", ".join(hooks.keys()) if isinstance(hooks, dict) else "?"
        items.append(Item("ghooks", "hooks", "global hooks: " + events, "",
                          ["Hooks run shell commands automatically on Claude events.",
                           json.dumps(hooks)[:300]], risky=True))
    if perms.get("defaultMode") == "bypassPermissions":
        items.append(Item("gmode", "bypassPermissions", "global default mode: bypassPermissions", "",
                          ["Claude never asks before acting. Removing restores normal prompts."], risky=True))
    for name, cfg in as_dict(cj.get("mcpServers")).items():
        cfg = as_dict(cfg)
        cmd = cfg.get("command") or cfg.get("url") or "?"
        args = " ".join(str(a) for a in as_list(cfg.get("args")))
        items.append(Item("gmcp", name, f"MCP server (all projects): {name}", "",
                          [f"In {CLAUDE_JSON}", f"Runs: {cmd} {args}"]))
    missing = [r for r in DENY_RULES if r not in as_list(perms.get("deny"))]
    if missing:
        items.append(Item("harden", "deny", "HARDEN: add deny rules for keys and .env files",
                          f"+{len(missing)} rules",
                          ["Adds to global settings (does not remove anything):"] + missing))
    return items


def scan_all(roots, secret_scan=True):
    cj = as_dict(load_json(CLAUDE_JSON))
    projects = as_dict(cj.get("projects"))
    session_names = {d.name for d in SESSIONS_DIR.iterdir()} if SESSIONS_DIR.is_dir() else set()
    return [
        Category("Trusted projects", "Folders you trusted, with their auto-approve rules (~/.claude.json)",
                 scan_trust(projects, session_names)),
        Category("Session transcripts", "Saved conversations, incl. file contents (~/.claude/projects)",
                 scan_sessions(projects, secret_scan)),
        Category("Project settings files", "Per-project .claude/settings*.json found on disk",
                 scan_settings(roots)),
        Category("Global settings", "Rules, extra dirs, hooks and MCP servers for every project",
                 scan_global(cj)),
    ]


# --------------------------------------------------------------------------- actions
def describe(it):
    return {
        "trust": f"Forget trusted project and its allow rules: {it.key}",
        "session": f"Quarantine transcripts ({human(it.size)}): {it.title}",
        "settings": f"Quarantine settings file: {it.key}",
        "gallow": f"Remove global allow rule: {it.key}",
        "gdir": f"Remove global extra directory: {it.key}",
        "ghooks": "Remove all global hooks",
        "gmode": "Remove bypassPermissions default mode",
        "gmcp": f"Remove user-wide MCP server: {it.key}",
        "harden": "Add deny rules for SSH/AWS/GPG keys and .env/.pem files",
    }[it.kind]


def apply_plan(plan):
    ts = time.strftime("%Y%m%d-%H%M%S")
    qdir = QUARANTINE_ROOT / ts
    # The quarantine holds transcripts and config backups: owner-only.
    QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(QUARANTINE_ROOT, 0o700)
    qdir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(qdir, 0o700)
    # "edits" records each JSON entry removed (or, for harden, added) so that
    # --restore can put exactly those back without touching anything newer.
    manifest = {"created": ts, "moves": [], "backups": [], "edits": []}
    log = []
    by = {}
    for it in plan:
        by.setdefault(it.kind, []).append(it)

    def backup(p):
        if p.exists():
            dest = qdir / "backup" / p.name
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            manifest["backups"].append({"original": str(p), "copy": str(dest)})

    def edit(file, path, **undo):
        manifest["edits"].append({"file": str(file), "path": path, **undo})

    def pop_entry(d, key):
        """Remove d[key], returning (found, value); value may legitimately be None."""
        if isinstance(d, dict) and key in d:
            return True, d.pop(key)
        return False, None

    # ~/.claude.json : trusted projects + user-wide MCP servers
    if by.get("trust") or by.get("gmcp"):
        backup(CLAUDE_JSON)
        data = as_dict(load_json(CLAUDE_JSON))
        for it in by.get("trust", []):
            ok, old = pop_entry(data.get("projects"), it.key)
            if ok:
                edit(CLAUDE_JSON, ["projects", it.key], set=old)
            log.append(("OK  " if ok else "SKIP") + " forgot project " + it.key)
        for it in by.get("gmcp", []):
            ok, old = pop_entry(data.get("mcpServers"), it.key)
            if ok:
                edit(CLAUDE_JSON, ["mcpServers", it.key], set=old)
            log.append(("OK  " if ok else "SKIP") + " removed MCP server " + it.key)
        write_json_atomic(CLAUDE_JSON, data)

    # ~/.claude/settings.json
    gkinds = ("gallow", "gdir", "ghooks", "gmode", "harden")
    if any(by.get(k) for k in gkinds):
        backup(GLOBAL_SETTINGS)
        data = as_dict(load_json(GLOBAL_SETTINGS))
        perms = data.get("permissions")
        if not isinstance(perms, dict):
            perms = data["permissions"] = {}
        for kind, field_, what in (("gallow", "allow", "allow rule"),
                                   ("gdir", "additionalDirectories", "extra directory")):
            rules = as_list(perms.get(field_))
            for it in by.get(kind, []):
                if it.key in rules:
                    rules.remove(it.key)
                    edit(GLOBAL_SETTINGS, ["permissions", field_], append=it.key)
                    log.append(f"OK   removed {what} {it.key}")
        if by.get("ghooks") and "hooks" in data:
            edit(GLOBAL_SETTINGS, ["hooks"], set=data.pop("hooks"))
            log.append("OK   removed global hooks")
        if by.get("gmode") and perms.get("defaultMode") == "bypassPermissions":
            edit(GLOBAL_SETTINGS, ["permissions", "defaultMode"], set=perms.pop("defaultMode"))
            log.append("OK   removed bypassPermissions mode")
        if by.get("harden"):
            deny = perms.get("deny")
            if not isinstance(deny, list):
                deny = perms["deny"] = []
            added = [r for r in DENY_RULES if r not in deny]
            deny.extend(added)
            edit(GLOBAL_SETTINGS, ["permissions", "deny"], remove=added)
            log.append(f"OK   added {len(added)} deny rules")
        GLOBAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(GLOBAL_SETTINGS, data)

    # moves into quarantine
    for kind, sub in (("session", "sessions"), ("settings", "settings")):
        for it in by.get(kind, []):
            src = Path(it.key)
            dest = qdir / sub / encode_path(it.key)
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dest))
                manifest["moves"].append({"original": str(src), "quarantined": str(dest)})
                log.append(f"OK   quarantined {src}")
            except OSError as e:
                log.append(f"FAIL {src}: {e}")

    write_json_atomic(qdir / "manifest.json", manifest)
    log.append("")
    log.append(f"Everything removed is in {qdir}")
    log.append(f"Undo:  {sys.argv[0]} --restore {qdir}")
    log.append(f"Once you're sure, delete it for good:  rm -rf {qdir}")
    return log


def put_back(data, e):
    """Undo one manifest edit inside the file's current contents. Returns True if it changed anything."""
    node = data
    for k in e["path"][:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    last = e["path"][-1]
    if "set" in e:                      # a removed entry: put it back unless something newer is there
        if last in node:
            return False
        node[last] = e["set"]
        return True
    if not isinstance(node.get(last), list):
        node[last] = []
    lst = node[last]
    if "append" in e:                   # a removed list rule
        if e["append"] in lst:
            return False
        lst.append(e["append"])
        return True
    changed = False
    for r in e.get("remove", []):       # rules that harden added
        if r in lst:
            lst.remove(r)
            changed = True
    return changed


def restore(qdir):
    qdir = Path(qdir)
    manifest = load_json(qdir / "manifest.json")
    if not isinstance(manifest, dict):
        sys.exit(f"No manifest.json in {qdir}")
    for m in reversed(as_list(manifest.get("moves"))):
        src, dest = Path(m["quarantined"]), Path(m["original"])
        if dest.exists():
            print(f"SKIP {dest} already exists")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        print(f"OK   restored {dest}")
    # Removed JSON entries go back into the file as it is NOW, so whatever Claude
    # Code wrote since the run is kept. The full backup is only used when the
    # file is gone or unreadable, or for 0.1.0 manifests that have no "edits".
    edits = {}
    for e in as_list(manifest.get("edits")):
        edits.setdefault(e["file"], []).append(e)
    backups = {b["original"]: b["copy"] for b in as_list(manifest.get("backups"))}
    for file in sorted(set(edits) | set(backups)):
        if "edits" in manifest:
            data = load_json(file)
            if isinstance(data, dict):
                n = sum(put_back(data, e) for e in edits.get(file, []))
                write_json_atomic(Path(file), data)
                print(f"OK   put {n} entr{'y' if n == 1 else 'ies'} back into {file}")
                continue
        if file in backups:
            shutil.copy2(backups[file], file)
            print(f"OK   restored {file} from backup")
        else:
            print(f"SKIP {file}: unreadable and no backup in {qdir}")


def show_in(it, mode):
    return (mode == "all" or (mode == "stale" and it.stale) or (mode == "risky" and it.risky)
            or (mode == "selected" and it.selected))


def print_report(cats, only="all"):
    for c in cats:
        risky = sum(i.risky for i in c.items)
        stale = sum(i.stale for i in c.items)
        print(f"\n== {c.name}: {len(c.items)} found, {stale} stale, {risky} risky ==")
        for it in c.items:
            if not show_in(it, only):
                continue
            flag = "!" if it.risky else ("~" if it.stale else " ")
            print(f" {flag} {it.title}   {it.badge}")
            for d in it.detail[1:]:
                if it.risky or it.stale:
                    print(f"      {d}")
    print("\nLegend: ! risky   ~ stale (folder gone).  Run without --report to revoke.")


# --------------------------------------------------------------------------- scheduled audit
TIMER = "claude-revoke-audit.timer"


def summarize(cats):
    """One line naming what needs attention per category, or None when nothing does."""
    bits = []
    for c in cats:
        stale = sum(1 for i in c.items if i.stale)
        risky = sum(1 for i in c.items if i.risky and not i.stale)   # like the TUI groups: stale first
        what = []
        if stale:
            what.append(f"{stale} stale")
        if risky:
            # a transcript is risky exactly when secrets were found in it
            what.append(f"{risky} with secrets" if all(i.kind == "session" for i in c.items)
                        else f"{risky} risky")
        if what:
            bits.append(f"{c.name}: {', '.join(what)}")
    return "; ".join(bits) or None


def config_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def state_dir():
    return Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local" / "state") / "claude-revoke"


def send_notification(summary):
    """Desktop notification through notify-send. Returns False when it is not installed."""
    if not shutil.which("notify-send"):
        return False
    subprocess.run(["notify-send", "--app-name=claude-revoke", "--icon=claude-revoke",
                    "Claude Code access needs a look", summary + "\nRun claude-revoke to review."],
                   check=False)
    return True


def schedule(choice, dry_run=False):
    """Turn the systemd user timer on (weekly or daily) or off. Returns an exit status."""
    dropin = config_dir() / "systemd" / "user" / (TIMER + ".d") / "schedule.conf"
    if dry_run:
        if choice == "off":
            print(f"Dry run: would run  systemctl --user disable --now {TIMER}")
        else:
            print(f"Dry run: would write {dropin} with OnCalendar={choice} "
                  f"and run  systemctl --user enable --now {TIMER}")
        return 0
    if not shutil.which("systemctl"):
        print("systemctl not found: scheduled audits need a systemd user session.", file=sys.stderr)
        return 1

    def ctl(*args):
        return subprocess.run(["systemctl", "--user", *args]).returncode

    if choice == "off":
        code = ctl("disable", "--now", TIMER)
        if code == 0:
            print("Scheduled audit: off")
        else:
            print(f"Could not disable {TIMER}: it may never have been installed, or there is no "
                  "systemd user session (is this a desktop login?).", file=sys.stderr)
        return code
    # A drop-in overrides the OnCalendar= shipped in the timer unit.
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text(f"[Timer]\nOnCalendar=\nOnCalendar={choice}\n")
    code = ctl("daemon-reload") or ctl("enable", "--now", TIMER)
    if code == 0:
        print(f"Scheduled audit: {choice}  (check with: systemctl --user status {TIMER})")
    else:
        dropin.unlink()
        print(f"Could not enable {TIMER}: is there a systemd user session (desktop login)? "
              "From a source checkout, run ./install.sh first.", file=sys.stderr)
    return code


# --------------------------------------------------------------------------- TUI
class App:
    def __init__(self, scr, cats, dry_run):
        self.scr, self.cats, self.dry_run = scr, cats, dry_run
        self.final = []
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.colors = curses.has_colors() and not os.environ.get("NO_COLOR")
        if self.colors:
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_RED, bg)
            curses.init_pair(2, curses.COLOR_YELLOW, bg)
            curses.init_pair(3, curses.COLOR_GREEN, bg)
            curses.init_pair(4, curses.COLOR_CYAN, bg)
            acc = ACCENT % max(8, getattr(curses, "COLORS", 8))
            curses.init_pair(5, curses.COLOR_BLACK, acc)   # bars: theme black on theme accent
            curses.init_pair(7, acc, bg)                   # accent text on theme background
        scr.keypad(True)

    def bar(self):
        # Header/footer/cursor bar. Uses the theme palette instead of reverse
        # video, which would paint the theme's foreground (often cream/white).
        return curses.color_pair(5) if self.colors else curses.A_REVERSE

    def hl(self, attr):
        # Cursor highlight: colored rows reverse into their own palette color,
        # plain rows get the accent bar.
        return attr | curses.A_REVERSE if attr else self.bar()

    def cp(self, n):
        return curses.color_pair(n) if self.colors else (curses.A_BOLD if n in (1, 7) else 0)

    def put(self, y, x, s, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w - 1:
            return
        try:
            self.scr.addstr(y, x, s[: w - x - 1], attr)
        except curses.error:
            pass

    def header(self, title, sub=""):
        h, w = self.scr.getmaxyx()
        self.put(0, 0, " " * (w - 1), self.bar())
        self.put(0, 1, "claude-revoke" + ("  [DRY RUN]" if self.dry_run else "") + "  |  " + title,
                 self.bar())
        if sub:
            self.put(1, 1, sub, self.cp(4))

    def footer(self, text):
        h, w = self.scr.getmaxyx()
        self.put(h - 1, 0, (" " + text).ljust(w - 1), self.bar())

    def prompt(self, msg):
        h, w = self.scr.getmaxyx()
        self.put(h - 1, 0, " " * (w - 1))
        self.put(h - 1, 0, msg, self.cp(7))
        curses.echo()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            s = self.scr.getstr(h - 1, min(len(msg), w - 2), 200).decode(errors="ignore")
        finally:
            curses.noecho()
            try:
                curses.curs_set(0)
            except curses.error:
                pass
        return s.strip()

    def item_attr(self, it):
        if it.risky:
            return self.cp(1)
        if it.stale:
            return self.cp(2)
        return 0

    # ---- screens
    def run(self):
        cur = 0
        while True:
            entries = [c.name for c in self.cats] + ["Review & apply", "Quit"]
            self.scr.erase()
            self.header("Main menu", "Pick a category, tick what to revoke, then Review & apply.")
            for i, c in enumerate(self.cats):
                sel = sum(it.selected for it in c.items)
                risky = sum(it.risky for it in c.items)
                stale = sum(it.stale for it in c.items)
                line = f"{c.name:<24}{len(c.items):>5} found  {stale:>4} stale  {risky:>4} risky   [{sel} selected]"
                attr = self.bar() if i == cur else 0
                self.put(3 + i, 2, ("> " if i == cur else "  ") + line, attr)
                if i == cur:
                    self.put(4 + len(entries), 4, c.help, self.cp(4))
            for j, name in enumerate(entries[len(self.cats):], start=len(self.cats)):
                attr = self.bar() if j == cur else self.cp(7)
                self.put(4 + j, 2, ("> " if j == cur else "  ") + name, attr)
            total = sum(it.selected for c in self.cats for it in c.items)
            self.put(7 + len(entries), 4, f"{total} item(s) selected in total.", self.cp(3))
            self.put(8 + len(entries), 4, "Red = risky (broad rules, hooks, secrets)   Yellow = stale (folder gone)")
            self.footer("Up/Down: move   Enter: open   q: quit")
            k = self.scr.getch()
            if k in (curses.KEY_UP, ord("k")):
                cur = (cur - 1) % len(entries)
            elif k in (curses.KEY_DOWN, ord("j")):
                cur = (cur + 1) % len(entries)
            elif k in (ord("q"), 27):
                return
            elif k in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT, ord("l")):
                if cur < len(self.cats):
                    self.checklist(self.cats[cur])
                elif entries[cur] == "Quit":
                    return
                elif self.review() == "exit":
                    return

    def build_rows(self, cat, filt, mode):
        groups = [
            ("STALE", "folder deleted - safe to remove", self.cp(2), lambda it: it.stale),
            ("RISKY", "broad rules, hooks, secrets - review", self.cp(1), lambda it: it.risky and not it.stale),
            ("OK", "no problems found", self.cp(3), lambda it: not it.stale and not it.risky),
        ]
        rows = []
        f = filt.lower()
        for name, desc, color, test in groups:
            members = [it for it in cat.items if test(it) and show_in(it, mode)
                       and (f in it.name.lower() or f in it.where.lower() or f in it.title.lower())]
            if members:
                rows.append(("head", name, desc, color, members))
                rows += [("item", it) for it in members]
        return rows

    def checklist(self, cat):
        cur, top, filt = 0, 0, ""
        modes = ["all", "stale", "risky", "selected"]
        mode = "all"
        detail_h = 7
        while True:
            rows = self.build_rows(cat, filt, mode)
            h, w = self.scr.getmaxyx()
            list_h = max(1, h - 4 - detail_h)
            cur = max(0, min(cur, len(rows) - 1))
            if cur < top:
                top = cur
            elif cur >= top + list_h:
                top = cur - list_h + 1
            self.scr.erase()
            self.header(cat.name, f"Space: tick folder or whole group   a all  n none   v view: {mode.upper()}   / search   Esc back")
            if not rows:
                self.put(3, 2, f"Nothing to show (view: {mode}" + (f", search: {filt}" if filt else "") + ").")
            name_w = 30
            for i in range(list_h):
                j = top + i
                if j >= len(rows):
                    break
                row = rows[j]
                if row[0] == "head":
                    _, gname, desc, color, members = row
                    n_sel = sum(it.selected for it in members)
                    mark = "[x]" if n_sel == len(members) else ("[-]" if n_sel else "[ ]")
                    line = f"{mark} {gname} - {desc} ({n_sel}/{len(members)} selected)"
                    attr = color | curses.A_BOLD
                else:
                    it = row[1]
                    mark = "[x]" if it.selected else "[ ]"
                    badge = ("RISKY  " if it.stale and it.risky else "") + it.badge
                    nw = name_w if it.where else max(name_w, w - 12 - len(badge))
                    nm = it.name if len(it.name) <= nw else it.name[: nw - 1] + "~"
                    room = w - 10 - nw - len(badge) - 2
                    where = it.where if len(it.where) <= room else "..." + it.where[-max(0, room - 3):]
                    line = f"    {mark} {nm.ljust(nw)} {where.ljust(max(0, room))} {badge}"
                    attr = self.item_attr(it)
                if j == cur:
                    attr = self.hl(attr)
                self.put(2 + i, 1, line, attr)
            y0 = h - 1 - detail_h
            self.put(y0, 0, "-" * (w - 1), self.cp(4))
            if rows:
                row = rows[cur]
                if row[0] == "head":
                    info = [f"Group {row[1]}: {row[2]}", f"{len(row[4])} item(s). Space ticks/unticks the whole group."]
                else:
                    info = row[1].detail
                for k, d in enumerate(info[: detail_h - 1]):
                    self.put(y0 + 1 + k, 2, d.replace(str(HOME), "~"))
            sel = sum(it.selected for it in cat.items)
            self.footer(f"{sel}/{len(cat.items)} selected" + (f"   search: {filt}" if filt else "")
                        + "   Up/Down move   Enter/Esc: back to menu")
            visible = [r[1] for r in rows if r[0] == "item"]
            k = self.scr.getch()
            if k in (curses.KEY_UP, ord("k")):
                cur -= 1
            elif k in (curses.KEY_DOWN, ord("j")):
                cur += 1
            elif k == curses.KEY_PPAGE:
                cur -= list_h
            elif k == curses.KEY_NPAGE:
                cur += list_h
            elif k == curses.KEY_HOME:
                cur = 0
            elif k == curses.KEY_END:
                cur = len(rows) - 1
            elif k == ord(" ") and rows:
                row = rows[cur]
                if row[0] == "head":
                    members = row[4]
                    new = not all(it.selected for it in members)
                    for it in members:
                        it.selected = new
                else:
                    row[1].selected = not row[1].selected
                    cur += 1
            elif k == ord("a"):
                for it in visible:
                    it.selected = True
            elif k == ord("n"):
                for it in visible:
                    it.selected = False
            elif k == ord("s"):
                for it in visible:
                    if it.stale:
                        it.selected = True
            elif k == ord("r"):
                for it in visible:
                    if it.risky:
                        it.selected = True
            elif k == ord("v"):
                mode = modes[(modes.index(mode) + 1) % len(modes)]
                cur = top = 0
            elif k == ord("/"):
                filt = self.prompt("Search folder name (empty = clear): ")
                cur = top = 0
            elif k in (27, ord("q"), 10, 13, curses.KEY_ENTER, curses.KEY_LEFT, ord("h")):
                return

    def pager(self, title, lines, footer):
        top = 0
        while True:
            h, w = self.scr.getmaxyx()
            body = h - 3
            self.scr.erase()
            self.header(title)
            for i in range(body):
                j = top + i
                if j >= len(lines):
                    break
                text, attr = lines[j] if isinstance(lines[j], tuple) else (lines[j], 0)
                self.put(2 + i, 1, text, attr)
            self.footer(footer)
            k = self.scr.getch()
            if k in (curses.KEY_UP, ord("k")):
                top = max(0, top - 1)
            elif k in (curses.KEY_DOWN, ord("j")):
                top = min(max(0, len(lines) - body), top + 1)
            elif k == curses.KEY_PPAGE:
                top = max(0, top - body)
            elif k == curses.KEY_NPAGE:
                top = min(max(0, len(lines) - body), top + body)
            elif 0 <= k < 256:
                return chr(k)

    def review(self):
        plan = [it for c in self.cats for it in c.items if it.selected]
        if not plan:
            self.pager("Review", ["Nothing selected yet. Open a category and tick items with Space."],
                       "any key: back")
            return None
        lines = []
        pids = running_claude()
        if pids:
            lines += [(f"WARNING: Claude Code seems to be running (pid {', '.join(pids)}).", self.cp(1)),
                      ("Close it first, or it may overwrite ~/.claude.json after this tool edits it.", self.cp(1)), ""]
        for c in self.cats:
            sel = [it for it in c.items if it.selected]
            if sel:
                lines.append((f"{c.name} ({len(sel)})", self.cp(4)))
                lines += [("  - " + describe(it), self.item_attr(it)) for it in sel]
                lines.append("")
        lines += ["Files are MOVED to a quarantine folder and JSON files are backed up first,",
                  "so you can undo with --restore. Nothing is permanently deleted."]
        k = self.pager(f"Review: {len(plan)} change(s)", lines,
                       "y: apply   any other key: back" + ("   (dry run: nothing will change)" if self.dry_run else ""))
        if k != "y":
            return None
        if self.dry_run:
            self.pager("Dry run", ["Dry run: nothing was changed. Run without --dry-run to apply."], "any key: back")
            return None
        if self.prompt("Type YES to apply these changes: ") != "YES":
            return None
        self.final = apply_plan(plan)
        self.pager("Done", self.final, "any key: exit")
        return "exit"


def main():
    ap = argparse.ArgumentParser(description="Audit and revoke Claude Code access (installer-style TUI).")
    ap.add_argument("--report", action="store_true", help="print the audit and exit")
    ap.add_argument("--only", choices=["all", "stale", "risky"], default="all",
                    help="with --report: list only stale or risky items")
    ap.add_argument("--dry-run", action="store_true", help="never change anything")
    ap.add_argument("--restore", metavar="DIR", help="undo a run from its quarantine folder")
    ap.add_argument("--roots", nargs="+", default=[str(HOME)],
                    help="where to look for project .claude/ folders (default: your home)")
    ap.add_argument("--accent", default=os.environ.get("CLAUDE_REVOKE_ACCENT", "blue"),
                    help="bar color from your terminal theme palette: 0-15 or a name like blue, bright-magenta")
    ap.add_argument("--no-secret-scan", action="store_true", help="skip scanning transcripts for secrets")
    ap.add_argument("--pause", action="store_true",
                    help="wait for Enter before exiting (keeps a launcher-opened terminal window open)")
    ap.add_argument("--notify", action="store_true",
                    help="scan, print a one-line summary and send a desktop notification if anything "
                         "is stale or risky (what the scheduled audit runs)")
    ap.add_argument("--schedule", choices=["weekly", "daily", "off"],
                    help="turn the scheduled audit (a systemd user timer) on at that interval, or off")
    ap.add_argument("--version", action="version", version=f"claude-revoke {__version__}")
    args = ap.parse_args()
    if not args.pause:
        run(args, ap)
        return
    # Launched from the desktop entry: the terminal closes when we exit, so hold
    # the window until Enter, including after an error or a usage message.
    code = 0
    try:
        run(args, ap)
    except SystemExit as e:
        code = e.code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            code = 1
    except KeyboardInterrupt:
        code = 130
    except Exception:
        traceback.print_exc()
        code = 1
    try:
        input("\nPress Enter to close this window... ")
    except (EOFError, OSError):
        pass
    sys.exit(code)


def run(args, ap):
    global ACCENT
    try:
        ACCENT = parse_accent(args.accent)
    except ValueError as e:
        ap.error(str(e))

    if args.schedule:
        if args.roots != [str(HOME)] or args.no_secret_scan:
            print("Note: the scheduled run uses default options (whole home, secret scan on). "
                  "To change that: systemctl --user edit claude-revoke-audit.service", file=sys.stderr)
        sys.exit(schedule(args.schedule, args.dry_run))
    if args.restore:
        restore(args.restore)
        return
    print("Scanning Claude Code footprint (this can take a minute on a big home folder)...", file=sys.stderr)
    cats = scan_all(args.roots, not args.no_secret_scan)
    if args.notify:
        summary = summarize(cats)
        # One notification per change: the last summary is remembered, so a situation
        # you have already been told about is only logged, not shown again.
        memo = state_dir() / "last-notified"
        previous = memo.read_text() if memo.is_file() else None
        if summary is None:
            print("claude-revoke: nothing stale or risky found.")
        elif summary == previous:
            print("claude-revoke: " + summary + "  (unchanged since the last notification)")
        else:
            print("claude-revoke: " + summary)
            if not send_notification(summary):
                print("notify-send not found: install libnotify-bin (Debian/Ubuntu) or libnotify "
                      "to get desktop notifications.", file=sys.stderr)
        if (summary or "") != (previous or ""):
            memo.parent.mkdir(parents=True, exist_ok=True)
            memo.write_text(summary or "")
        return
    if args.report:
        print_report(cats, args.only)
        return
    if not sys.stdin.isatty():
        sys.exit("Interactive mode needs a terminal. Use --report for plain output.")
    os.environ.setdefault("ESCDELAY", "25")
    app_holder = {}

    def _run(scr):
        app = App(scr, cats, args.dry_run)
        app_holder["app"] = app
        app.run()

    curses.wrapper(_run)
    final = app_holder.get("app").final if app_holder.get("app") else []
    if final:
        print("\n".join(final))


if __name__ == "__main__":
    main()
