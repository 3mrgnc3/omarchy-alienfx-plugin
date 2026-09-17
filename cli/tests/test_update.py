"""The update check.

Nothing here touches the network. `update._git` is the single seam through
which every git call passes, so replacing it gives the whole module a remote
that answers exactly what a test wants, including by refusing to answer.

The real `git ls-remote` output is reproduced verbatim below, peeled `^{}`
duplicates and all, because parsing that shape correctly is most of the job.
"""

import json
import os

import pytest

from alienfx_ctl import state, update


#: Exactly what `git ls-remote origin 'refs/tags/v*'` printed for this repo.
#: Annotated tags appear twice: once for the tag object, once peeled to the
#: commit it points at. Both must collapse to one version.
LS_REMOTE = "\n".join([
    "e2c9fe3ffbcadb90b82634d7283d161775347fa1\trefs/tags/v1.0.0",
    "81fa98828abec6711d7c9eeeabba27ed38712b40\trefs/tags/v1.0.0^{}",
    "44fe4edf85847094cf9f75a1da9ac7bef5181db7\trefs/tags/v1.0.1",
    "46012165a13d7d64ee4c18199a6c06636dc6fbc8\trefs/tags/v1.0.1^{}",
    "c550d4bef60a6f524b970d94c35fac70f7f1f5c8\trefs/tags/v1.0.2",
    "1e86fd8869b065db48d57ba0d7e4276d1cb13c31\trefs/tags/v1.0.2^{}",
]) + "\n"

REMOTE = "https://github.com/3mrgnc3/omarchy-alienfx-plugin"


@pytest.fixture()
def checkout(tmp_path):
    """A directory that looks like an installed plugin, version 1.0.1."""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"id": "3mrgnc3.alienfx", "version": "1.0.1"}), encoding="utf-8")
    return str(tmp_path)


@pytest.fixture()
def remote(monkeypatch):
    """Stand in for the remote, and record what was asked of it.

    Returns a dict the test can mutate: `tags` and `url` become the replies,
    and `calls` accumulates the git argument lists actually issued - which is
    how the caching and opt-out tests prove that *no* call was made.
    """
    replies = {"tags": LS_REMOTE, "url": REMOTE + "\n", "calls": []}

    def fake_git(plugin_dir, args, timeout):
        replies["calls"].append(list(args))
        if args[0] == "ls-remote":
            return replies["tags"]
        if args[0] == "remote":
            return replies["url"]
        return None

    monkeypatch.setattr(update, "_git", fake_git)
    return replies


# ------------------------------------------------------------------ versions

def test_a_version_parses_with_or_without_its_v():
    """Tags carry the v, manifests do not, and both sides feed the same
    comparison."""
    assert update.parse_version("v1.0.2") == (1, 0, 2)
    assert update.parse_version("1.0.2") == (1, 0, 2)


@pytest.mark.parametrize("text", ["", None, "nightly", "1.0.2-rc1", "v", "1..2", "1.0.2a"])
def test_anything_that_is_not_a_plain_version_is_rejected(text):
    """A tag the author uses for something else must not be mistaken for a
    release, or the panel announces an update that does not exist. Pre-release
    tags are among them: a release candidate is not something to send to
    everybody running the plugin."""
    assert update.parse_version(text) is None


def test_versions_compare_numerically_not_as_text():
    """The whole point of parsing to a tuple. As strings "1.0.10" sorts below
    "1.0.9", so a tenth release would never be announced."""
    assert update._is_newer("1.0.9", "1.0.10")
    assert not update._is_newer("1.0.10", "1.0.9")


def test_an_equal_version_is_not_an_update():
    assert not update._is_newer("1.0.2", "1.0.2")


def test_a_newer_installed_copy_is_not_an_update():
    """Someone testing an unreleased build is ahead, not behind, and must not
    be nagged to install something older than what they are running."""
    assert not update._is_newer("1.1.0", "1.0.2")


# ------------------------------------------------------------------ manifest

def test_the_installed_version_comes_from_the_manifest(checkout):
    assert update.installed_version(checkout) == "1.0.1"


@pytest.mark.parametrize("content", ["", "{", '{"id": "x"}', '{"version": null}', "[]"])
def test_an_unusable_manifest_reports_no_version(tmp_path, content):
    """Degrades to showing nothing rather than raising. This runs on every
    panel open, so it must not be able to break the panel."""
    (tmp_path / "manifest.json").write_text(content, encoding="utf-8")
    assert update.installed_version(str(tmp_path)) == ""


def test_a_missing_manifest_reports_no_version(tmp_path):
    assert update.installed_version(str(tmp_path)) == ""


# ----------------------------------------------------------------------- url

@pytest.mark.parametrize("raw", [
    "https://github.com/3mrgnc3/omarchy-alienfx-plugin",
    "https://github.com/3mrgnc3/omarchy-alienfx-plugin.git",
    "https://github.com/3mrgnc3/omarchy-alienfx-plugin/",
    "git@github.com:3mrgnc3/omarchy-alienfx-plugin.git",
    "ssh://git@github.com/3mrgnc3/omarchy-alienfx-plugin.git",
])
def test_every_way_of_writing_the_remote_reaches_the_same_page(raw):
    """People clone over https or ssh and should get a working link either
    way."""
    assert update.normalise_url(raw) == REMOTE


@pytest.mark.parametrize("raw", [
    "file:///etc/passwd",
    "/home/someone/repos/thing",
    "javascript:alert(1)",
    "https://github.com",
    "",
])
def test_a_remote_that_is_not_a_web_page_produces_no_link(raw):
    """The remote is whatever the checkout says, and the result is handed to a
    browser, so it is matched against a pattern rather than trusted. No link is
    a fine outcome; the version still shows, it just is not clickable."""
    assert update.normalise_url(raw) == ""


def test_credentials_in_the_remote_never_reach_the_browser():
    """A remote can carry a token. Opening it would write that into browser
    history and hand it to the page as a referrer."""
    assert update.normalise_url("https://user:t0ken@github.com/a/b") == "https://github.com/a/b"


def test_a_plaintext_remote_is_linked_over_tls():
    """No reason to open an unencrypted page on the user's behalf when the
    same page is served over https."""
    assert update.normalise_url("http://github.com/a/b") == "https://github.com/a/b"


@pytest.mark.parametrize("raw", [
    "https://github.com/a/b?utm_source=x",
    "https://github.com/a/b#readme",
])
def test_anything_after_the_path_is_discarded(raw):
    """The address is rebuilt from its parts rather than edited as text, so a
    query or fragment cannot survive by being overlooked."""
    assert update.normalise_url(raw) == "https://github.com/a/b"


# ------------------------------------------------------------------ ls-remote

def test_tags_are_read_from_the_ref_listing(checkout, remote):
    """Peeled `^{}` entries are duplicates of the tag above them, not extra
    releases."""
    assert update.remote_versions(checkout) == [(1, 0, 0), (1, 0, 1), (1, 0, 2)]


def test_tags_that_are_not_releases_are_ignored(checkout, remote):
    remote["tags"] = "\n".join([
        "aaa\trefs/tags/v1.0.0",
        "bbb\trefs/tags/vendor-drop",
        "ccc\trefs/tags/v2.0.0-beta",
        "ddd\tnot-a-ref-line",
    ]) + "\n"
    assert update.remote_versions(checkout) == [(1, 0, 0)]


def test_an_unreachable_remote_is_distinct_from_one_with_no_releases(checkout, monkeypatch):
    """None means "could not ask" and is retried within the hour; an empty list
    means "asked, nothing published" and is believed for a day. Collapsing them
    would either hammer the network or go quiet after one dropped connection."""
    monkeypatch.setattr(update, "_git", lambda d, a, t: None)
    assert update.remote_versions(checkout) is None
    monkeypatch.setattr(update, "_git", lambda d, a, t: "")
    assert update.remote_versions(checkout) == []


def test_the_check_only_ever_asks_for_refs(checkout, remote, config_root):
    """Two reasons, and the second is why the marketplace rejected the earlier
    design. A fetch writes into .git, and the shell hot-reloads a plugin whose
    checkout changes, so it would restart the panel that started it. And
    obtaining code this way at all, then running it, is a listing blocker: the
    version named to the user is not bound to the commit that would arrive."""
    update.check(checkout, refresh=True)
    verbs = [call[0] for call in remote["calls"]]
    assert set(verbs) == {"ls-remote", "remote"}
    for forbidden in ("fetch", "pull", "checkout", "merge", "reset", "clone"):
        assert not any(forbidden in call for call in remote["calls"]), forbidden


def test_the_check_reports_the_cli_its_own_version(checkout, remote, config_root):
    """Not the same as the installed version: the plugin folder and the CLI are
    updated by separate steps and can disagree. The panel compares the two to
    notice that the popup is newer than the command it calls."""
    from alienfx_ctl import __version__
    assert update.check(checkout, refresh=True)["cli"] == __version__
    assert update.check(checkout)["cli"] == __version__


def test_the_cli_version_is_reported_even_with_checking_off(checkout, remote, config_root):
    """Turning off the network check must not cost the panel its ability to
    notice a half-finished update, which is a purely local comparison."""
    open(update.disable_flag(), "w").close()
    assert update.check(checkout)["cli"] != ""


def test_git_is_told_never_to_prompt(checkout, monkeypatch):
    """A panel that opens an invisible credential prompt hangs with no symptom.
    Only reproducible on a private or moved remote, so it is pinned here."""
    seen = {}

    def capture(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        raise OSError("no git here")

    monkeypatch.setattr(update.subprocess, "run", capture)
    update._git(str(checkout), ["ls-remote"], 1)
    assert seen["GIT_TERMINAL_PROMPT"] == "0"
    assert "BatchMode=yes" in seen["GIT_SSH_COMMAND"]


# --------------------------------------------------------------------- check

def test_a_newer_tag_is_reported_as_an_update(checkout, remote, config_root):
    result = update.check(checkout, refresh=True)
    assert result["installed"] == "1.0.1"
    assert result["latest"] == "1.0.2"
    assert result["update"] is True
    assert result["url"] == REMOTE


def test_the_current_version_is_not_reported_as_an_update(tmp_path, remote, config_root):
    (tmp_path / "manifest.json").write_text('{"version": "1.0.2"}', encoding="utf-8")
    assert update.check(str(tmp_path), refresh=True)["update"] is False


def test_an_unreachable_remote_reports_no_opinion(checkout, monkeypatch, config_root):
    """Offline is the common case for a laptop. It shows the version and says
    nothing else - never an error, never a false "up to date"."""
    monkeypatch.setattr(update, "_git", lambda d, a, t: None)
    result = update.check(checkout, refresh=True)
    assert result["installed"] == "1.0.1"
    assert result["ok"] is False
    assert result["update"] is False
    assert result["latest"] == ""


def test_a_directory_that_is_not_a_checkout_is_not_an_error(tmp_path, config_root):
    """Someone may have copied the plugin in by hand. Nothing to compare
    against is not a failure."""
    result = update.check(str(tmp_path), refresh=True)
    assert result["update"] is False
    assert result["installed"] == ""


# --------------------------------------------------------------------- cache

def test_the_answer_is_cached_so_opening_the_panel_costs_nothing(checkout, remote, config_root):
    """The panel opens whenever the user wants a colour. Asking the network
    each time would be both slow and rude."""
    update.check(checkout, refresh=True)
    remote["calls"].clear()
    result = update.check(checkout)
    assert remote["calls"] == []
    assert result["latest"] == "1.0.2"


def test_refresh_ignores_the_cache(checkout, remote, config_root):
    update.check(checkout, refresh=True)
    remote["calls"].clear()
    update.check(checkout, refresh=True)
    assert remote["calls"] != []


def test_a_stale_cache_is_re_checked(checkout, remote, config_root):
    update.check(checkout, refresh=True)
    cached = state.read_json(update.cache_path())
    cached["checked"] -= update.OK_INTERVAL + 1
    state.write_json_atomic(update.cache_path(), cached)
    remote["calls"].clear()
    update.check(checkout)
    assert remote["calls"] != []


def test_a_failed_check_is_retried_sooner_than_a_successful_one(checkout, remote, config_root):
    """A dropped connection should not silence the check until tomorrow."""
    assert update.FAIL_INTERVAL < update.OK_INTERVAL
    update.check(checkout, refresh=True)
    cached = state.read_json(update.cache_path())
    cached["ok"] = False
    cached["checked"] -= update.FAIL_INTERVAL + 1
    state.write_json_atomic(update.cache_path(), cached)
    remote["calls"].clear()
    update.check(checkout)
    assert remote["calls"] != []


def test_a_cache_from_a_different_checkout_is_not_reused(checkout, remote, tmp_path, config_root):
    """Two checkouts can have different remotes entirely. Answering for one
    from the other's cache would be wrong rather than merely stale."""
    update.check(checkout, refresh=True)
    other = tmp_path / "other"
    other.mkdir()
    (other / "manifest.json").write_text('{"version": "1.0.1"}', encoding="utf-8")
    remote["calls"].clear()
    update.check(str(other))
    assert remote["calls"] != []


def test_updating_clears_the_indicator_without_waiting_for_the_cache(checkout, remote, config_root):
    """The nastiest possible bug here: the user updates, and the panel keeps
    telling them to update because the day-old answer is still cached. The
    installed version is re-read on every call, so the verdict is recomputed
    even when the remote half comes from cache."""
    update.check(checkout, refresh=True)
    with open(os.path.join(checkout, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump({"version": "1.0.2"}, handle)
    remote["calls"].clear()
    result = update.check(checkout)
    assert remote["calls"] == []
    assert result["installed"] == "1.0.2"
    assert result["update"] is False


def test_the_cache_never_leaks_an_internal_field(checkout, remote, config_root):
    """`dir` is bookkeeping for cache validity. It is a local path, so it has
    no business reaching the panel or anyone's screen."""
    assert "dir" not in update.check(checkout, refresh=True)
    assert "dir" not in update.check(checkout)


# ------------------------------------------------------------------- opt-out

def test_the_flag_file_stops_the_check_entirely(checkout, remote, config_root):
    """Opting out has to mean no connection is made, not merely that the
    indicator is hidden."""
    open(update.disable_flag(), "w").close()
    result = update.check(checkout, refresh=True)
    assert remote["calls"] == []
    assert result["disabled"] is True
    assert result["update"] is False


def test_opting_out_still_shows_which_version_is_installed(checkout, remote, config_root):
    """Turning off the network check should not cost the user the ability to
    see what they are running."""
    open(update.disable_flag(), "w").close()
    assert update.check(checkout)["installed"] == "1.0.1"
