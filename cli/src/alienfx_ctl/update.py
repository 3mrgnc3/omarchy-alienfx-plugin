"""Is a newer release published?

The plugin is installed as a git checkout, so "is there anything newer" can be
answered without downloading anything. `git ls-remote` asks the server for its
list of refs and their object ids, and that is the entire exchange: no objects
are transferred, nothing is written into `.git`, and the working tree is not
touched.

That last property is load-bearing rather than merely tidy. The shell
hot-reloads a plugin whenever its checkout changes, so a `git fetch` from the
panel would restart the panel that started it. `ls-remote` cannot, because it
writes nothing.

Versions are compared, not commit ids. Comparing commits would light the
indicator on every intermediate push, including half-finished work, and would
tell anyone carrying local commits of their own that they are "behind" when
they are not. Comparing `v*` tags means the author decides when a user is told
that something is worth having.

Nothing here updates anything. The panel shows a version and, when a newer tag
exists, a link to the repository; installing it remains the user's business,
via `omarchy plugin update` or however else they choose.

Opting out: create the flag file below and no check is ever made.

    touch ~/.config/omarchy-alienfx-plugin/update-check.disabled
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse

from . import __version__, state

#: Where `omarchy plugin add` puts us. Only a default: the panel knows the
#: directory it was actually loaded from and passes it, which is authoritative
#: and survives a non-standard install.
PLUGIN_ID = "3mrgnc3.alienfx"

CACHE_NAME = "update-check.json"
DISABLE_FLAG = "update-check.disabled"

#: A release is not urgent news, so ask rarely. A failure is retried sooner,
#: because the usual cause is a laptop that was shut in a tunnel and the answer
#: changes the moment it is opened somewhere with a signal.
OK_INTERVAL = 24 * 60 * 60
FAIL_INTERVAL = 60 * 60

#: Long enough for a slow link, short enough that nobody notices. The panel
#: does not wait for this: the indicator appears when the answer arrives.
TIMEOUT = 10

#: Tags that name a release. Anything else in the namespace is ignored.
_TAG_RE = re.compile(r"^refs/tags/v(\d+(?:\.\d+)*)(?:\^\{\})?$")

#: What may be handed to a browser. The remote is whatever the checkout says,
#: so the result is matched against this rather than trusted: https only, a
#: plain host, and nothing after the path.
_URL_RE = re.compile(r"^https://[A-Za-z0-9][A-Za-z0-9.-]*(?::\d+)?/[A-Za-z0-9._~/-]+$")

#: git@host:owner/repo - the scp-like form, which is not a URL and so has to be
#: recognised before urlsplit is given a chance to misread it as a path.
_SCP_RE = re.compile(r"^(?:[A-Za-z0-9._-]+@)?([A-Za-z0-9.-]+):(?!/)(.+)$")

#: Schemes that identify a repository reachable over the web. `git` and `ssh`
#: clones point at a page that is served over https, and a user who cloned that
#: way deserves a working link too.
_WEB_SCHEMES = ("https", "http", "ssh", "git")


def default_plugin_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "omarchy", "plugins", PLUGIN_ID)


def cache_path() -> str:
    return os.path.join(state.config_root(), CACHE_NAME)


def disable_flag() -> str:
    return os.path.join(state.config_root(), DISABLE_FLAG)


def disabled() -> bool:
    return os.path.exists(disable_flag())


def parse_version(text):
    """"v1.2.3" or "1.2.3" -> (1, 2, 3). Anything else -> None.

    Returned as a tuple so comparison is numeric: "1.0.10" is newer than
    "1.0.9", which string comparison gets backwards.
    """
    if not text:
        return None
    cleaned = str(text).strip()
    if cleaned[:1] in ("v", "V"):
        cleaned = cleaned[1:]
    if not re.fullmatch(r"\d+(\.\d+)*", cleaned):
        return None
    return tuple(int(part) for part in cleaned.split("."))


def installed_version(plugin_dir: str) -> str:
    """The version the running copy declares, read from its own manifest.

    Deliberately local: this is the half of the comparison that must work with
    no network, so the panel can always show what is installed even when it
    cannot say whether anything newer exists.
    """
    manifest = os.path.join(plugin_dir, "manifest.json")
    try:
        with open(manifest, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return ""
    version = data.get("version") if isinstance(data, dict) else None
    return str(version) if isinstance(version, (str, int, float)) else ""


def normalise_url(raw: str) -> str:
    """Turn a git remote into a web address, or return "" if it is not one.

    Two deliberate rewrites, both about what ends up in a browser:

    * Any userinfo is dropped. A remote can carry a token or password, and
      putting that in the address bar writes it into history and into whatever
      the page's referrer reaches.
    * http is upgraded to https. There is no reason to open a plaintext link on
      the user's behalf, and every host that serves these pages serves them
      over TLS.

    Returns "" rather than guessing at anything else. No link is a perfectly
    good outcome: the version still shows, it just is not clickable.
    """
    url = str(raw or "").strip()
    if not url:
        return ""

    match = _SCP_RE.match(url)
    if match:
        url = f"https://{match.group(1)}/{match.group(2)}"

    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in _WEB_SCHEMES or not parts.hostname or not parts.path:
        return ""

    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"

    path = parts.path
    if path.endswith(".git"):
        path = path[: -len(".git")]
    path = path.rstrip("/")

    # Rebuilt from the pieces rather than edited as text, so a query string or
    # fragment cannot survive by being overlooked.
    cleaned = f"https://{host}{path}"
    return cleaned if _URL_RE.match(cleaned) else ""


def _git(plugin_dir: str, args, timeout: float):
    """Run git in the checkout, returning stdout, or None if it did not work.

    The environment is pinned so this can never block: without
    GIT_TERMINAL_PROMPT=0 a private or moved repository stops to ask for
    credentials, and a panel that opens a hidden password prompt is a bug that
    only shows up on someone else's machine.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    try:
        done = subprocess.run(
            ["git", "-C", plugin_dir] + list(args),
            capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def remote_url(plugin_dir: str, timeout: float = TIMEOUT) -> str:
    """The web address of the remote this copy was cloned from.

    Local operation despite the name - it reads .git/config, not the network.
    """
    out = _git(plugin_dir, ["remote", "get-url", "origin"], timeout)
    return normalise_url(out.strip() if out else "")


def remote_versions(plugin_dir: str, timeout: float = TIMEOUT):
    """Release tags the remote advertises, newest last. None if it could not ask.

    None and [] mean different things and are kept apart: nothing published is
    a real answer worth caching for a day, while "could not reach it" should be
    retried sooner.
    """
    out = _git(plugin_dir, ["ls-remote", "origin", "refs/tags/v*"], timeout)
    if out is None:
        return None
    found = set()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        match = _TAG_RE.match(parts[1].strip())
        if match:
            parsed = parse_version(match.group(1))
            if parsed:
                found.add(parsed)
    return sorted(found)


def _format(version) -> str:
    return ".".join(str(part) for part in version) if version else ""


def _fresh(cached, plugin_dir: str, now: float) -> bool:
    if not isinstance(cached, dict) or cached.get("dir") != plugin_dir:
        return False
    checked = cached.get("checked")
    if not isinstance(checked, (int, float)):
        return False
    age = now - checked
    if age < 0:
        return False
    return age < (OK_INTERVAL if cached.get("ok") else FAIL_INTERVAL)


def check(plugin_dir=None, refresh: bool = False, timeout: float = TIMEOUT) -> dict:
    """Report the installed version and whether a newer release is published.

    Never raises and never fails: an unreachable remote, a directory that is
    not a checkout, or a manifest without a version all degrade to "no opinion
    about updates", which the panel renders as the version on its own.

    `cli` is this package's own version, which is not the same thing as
    `installed`: the plugin folder and the CLI are updated by separate steps, so
    they can disagree. When they do, the popup is newer than the command it
    calls and the panel says setup needs finishing rather than calling a
    subcommand that does not exist yet. Reported here because the panel already
    runs this on open, so it costs no extra process.
    """
    plugin_dir = os.path.abspath(plugin_dir or default_plugin_dir())
    now = time.time()
    local = installed_version(plugin_dir)

    if disabled():
        return {"installed": local, "cli": __version__, "latest": "",
                "update": False, "url": "", "checked": 0, "ok": False,
                "disabled": True}

    cached = state.read_json(cache_path())
    if not refresh and _fresh(cached, plugin_dir, now):
        # The version is re-read rather than taken from the cache: the user may
        # have updated since, and a stale "update available" on a copy that is
        # already current would be the most annoying possible bug here.
        result = dict(cached)
        result["installed"] = local
        result["cli"] = __version__
        result["update"] = _is_newer(local, result.get("latest"))
        result.pop("dir", None)
        return result

    url = remote_url(plugin_dir, timeout)
    versions = remote_versions(plugin_dir, timeout)
    latest = _format(versions[-1]) if versions else ""

    record = {"dir": plugin_dir, "latest": latest, "url": url,
              "checked": now, "ok": versions is not None}
    try:
        state.ensure_dirs()
        state.write_json_atomic(cache_path(), record)
    except OSError:
        pass

    result = dict(record)
    result.pop("dir", None)
    result["installed"] = local
    result["cli"] = __version__
    result["update"] = _is_newer(local, latest)
    return result


def _is_newer(installed, latest) -> bool:
    here, there = parse_version(installed), parse_version(latest)
    return bool(here and there and there > here)
