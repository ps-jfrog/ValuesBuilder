"""Thin wrapper around a locally installed `helm` binary.

jfvb never vendors chart data -- everything is fetched live from
`https://charts.jfrog.io` for the chart version the user asks about, per
the "always live" design choice. This keeps sizing baselines, the image
list, and the airgap-key validation step honest against the actual chart
release being installed, at the cost of requiring `helm` + network access
to be usable.

Every helm invocation goes through `_run`, which polls the subprocess
rather than blocking on a single `communicate(timeout=...)` call. That
lets us (a) report elapsed time back to the caller every second via
`on_progress` -- so a slow network call never *looks* stuck, it just
counts up -- and (b) enforce a real timeout with a clear, actionable
error instead of hanging indefinitely.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Callable

JFROG_REPO_NAME = "jfrog"
JFROG_REPO_URL = "https://charts.jfrog.io"
CHART_NAME = "jfrog-platform"

# Called with a human-readable string describing what's currently running
# (and how long it's been running), so a caller (typically the CLI's
# spinner) can show live progress instead of a static "please wait".
ProgressCallback = Callable[[str], None]

# Helm operations that hit the network get a shorter leash than `pull`
# (which downloads real chart bytes and can legitimately take longer on
# a slow connection).
_DEFAULT_TIMEOUT = 30
_PULL_TIMEOUT = 180
_TEMPLATE_TIMEOUT = 120
_POLL_INTERVAL = 1.0


class HelmError(RuntimeError):
    """Raised when the local helm binary is missing or a helm command fails."""


@dataclass
class HelmResult:
    stdout: str
    stderr: str


def _new_process_group_kwargs() -> dict:
    """Launch helm in its own process group so a timeout/interrupt can kill
    it *and* any subprocess it might spawn, not just the immediate PID --
    a plain `proc.kill()` only signals the direct child.
    """

    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # Windows


def _kill_process_group(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.kill()


def _require_helm() -> str:
    helm_path = shutil.which("helm")
    if not helm_path:
        raise HelmError(
            "The 'helm' CLI was not found on PATH. jfvb shells out to helm "
            "to fetch chart data, render templates, and list versions -- "
            "install Helm 3.17+ and make sure it's on PATH, then retry "
            "(or pass --offline to skip live chart calls entirely)."
        )
    return helm_path


def _run(
    args: list[str],
    timeout: int = _DEFAULT_TIMEOUT,
    on_progress: ProgressCallback | None = None,
) -> HelmResult:
    """Run `helm <args>`, polling every `_POLL_INTERVAL`s so `on_progress`
    gets a chance to report elapsed time instead of the caller seeing
    nothing at all until the whole thing finishes (or times out).
    """

    helm_path = _require_helm()
    display = "helm " + " ".join(args)

    try:
        proc = subprocess.Popen(
            [helm_path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **_new_process_group_kwargs(),
        )
    except OSError as exc:
        raise HelmError(f"Failed to launch '{display}': {exc}") from exc

    start = time.monotonic()
    stdout = stderr = ""
    if on_progress:
        on_progress(display)
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=_POLL_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    _kill_process_group(proc)
                    proc.communicate()
                    raise HelmError(
                        f"'{display}' timed out after {timeout}s with no "
                        "response. This usually means charts.jfrog.io isn't "
                        "reachable (check your network/VPN/proxy). You can "
                        "re-run with --offline to skip live helm calls, or "
                        "--no-validate to skip the final dry-run."
                    ) from None
                if on_progress:
                    on_progress(f"{display}  ({int(elapsed)}s elapsed)")
    except KeyboardInterrupt:
        _kill_process_group(proc)
        proc.communicate()
        raise

    if proc.returncode != 0:
        raise HelmError(_explain_failure(display, proc.returncode, stderr))
    return HelmResult(stdout=stdout, stderr=stderr)


def _explain_failure(display: str, returncode: int, stderr: str) -> str:
    stderr = stderr.strip()
    lowered = stderr.lower()
    hint = ""
    if "no cached repo" in lowered or "no repositories" in lowered:
        hint = " (hint: the JFrog chart repo may not be configured -- this should be automatic; try re-running)"
    elif "not found" in lowered and "version" in lowered:
        hint = (
            " (hint: that chart version may not exist -- run `helm search "
            f"repo {JFROG_REPO_NAME}/{CHART_NAME} --versions` to see what's available)"
        )
    elif any(
        term in lowered
        for term in (
            "dial tcp",
            "no such host",
            "connection refused",
            "i/o timeout",
            "tls handshake",
            "x509",
            "certificate signed by unknown authority",
        )
    ):
        hint = " (hint: looks network-related -- check connectivity/VPN/proxy to charts.jfrog.io)"
    elif "execution error at" in lowered or "template:" in lowered:
        # A chart-side `fail`/template error -- the chart itself is telling
        # you what's wrong (e.g. jfrog-platform 11.6.0 requires nginx TLS to
        # be configured). Don't mislabel that as a connectivity problem.
        hint = (
            " (hint: the chart rejected these values -- read the chart's own "
            "message above; this is a values problem, not a network one)"
        )
    return f"'{display}' failed (exit {returncode}):\n{stderr}{hint}"


def ensure_repo(on_progress: ProgressCallback | None = None) -> None:
    """Add (idempotently) and update the JFrog chart repo."""

    helm_path = _require_helm()
    try:
        subprocess.run(
            [helm_path, "repo", "add", JFROG_REPO_NAME, JFROG_REPO_URL],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )  # exit code is non-zero (harmlessly) if the repo already exists
    except subprocess.TimeoutExpired as exc:
        raise HelmError(
            "'helm repo add' timed out after 15s -- helm shouldn't need "
            "the network just to register a repo URL locally; something "
            "is likely wrong with the local helm config/cache."
        ) from exc
    _run(["repo", "update", JFROG_REPO_NAME], on_progress=on_progress)


def list_versions(
    update_repo: bool = True, on_progress: ProgressCallback | None = None
) -> list[str]:
    if update_repo:
        ensure_repo(on_progress=on_progress)
    result = _run(
        [
            "search",
            "repo",
            f"{JFROG_REPO_NAME}/{CHART_NAME}",
            "--versions",
            "-o",
            "json",
        ],
        on_progress=on_progress,
    )
    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HelmError(
            "Couldn't parse `helm search repo` output as JSON -- got:\n"
            f"{result.stdout[:500]!r}"
        ) from exc
    if not entries:
        raise HelmError(
            f"`helm search repo {JFROG_REPO_NAME}/{CHART_NAME}` returned no "
            "versions. The repo may not have synced correctly -- try "
            f"`helm repo update {JFROG_REPO_NAME}` manually to check."
        )
    return [entry["version"] for entry in entries]


def latest_version(on_progress: ProgressCallback | None = None) -> str:
    versions = list_versions(on_progress=on_progress)
    return versions[0]


def pull_chart(
    version: str,
    dest_dir: Path | None = None,
    update_repo: bool = True,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Download and untar the chart for `version`, returning the chart dir."""

    if update_repo:
        ensure_repo(on_progress=on_progress)
    dest_dir = dest_dir or Path(mkdtemp(prefix="jfvb-chart-"))
    dest_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "pull",
            f"{JFROG_REPO_NAME}/{CHART_NAME}",
            "--version",
            version,
            "--untar",
            "--untardir",
            str(dest_dir),
        ],
        timeout=_PULL_TIMEOUT,
        on_progress=on_progress,
    )
    chart_dir = dest_dir / CHART_NAME
    if not chart_dir.is_dir():
        raise HelmError(
            f"helm pull reported success but {chart_dir} doesn't exist -- "
            "the chart layout may have changed; please report this."
        )
    return chart_dir


def template(
    version: str,
    release_name: str = "jfrog-platform",
    namespace: str = "jfrog-platform",
    values_files: list[Path] | None = None,
    show_only: list[str] | None = None,
    update_repo: bool = True,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Render the chart and return the full rendered manifest text."""

    if update_repo:
        ensure_repo(on_progress=on_progress)
    args = [
        "template",
        release_name,
        f"{JFROG_REPO_NAME}/{CHART_NAME}",
        "--version",
        version,
        "--namespace",
        namespace,
    ]
    for values_file in values_files or []:
        args += ["-f", str(values_file)]
    for target in show_only or []:
        args += ["--show-only", target]
    result = _run(args, timeout=_TEMPLATE_TIMEOUT, on_progress=on_progress)
    return result.stdout
