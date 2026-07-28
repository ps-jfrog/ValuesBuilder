import subprocess
import textwrap
from pathlib import Path

import pytest

from jfvb import helm_client
from jfvb.helm_client import HelmError, _explain_failure, _run


def _write_fake_helm(tmp_path: Path, script: str) -> Path:
    """Write an executable shell script standing in for the `helm` binary
    and return the directory it lives in, for prepending to PATH.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    helm_path = bin_dir / "helm"
    helm_path.write_text(textwrap.dedent(script))
    helm_path.chmod(0o755)
    return bin_dir


def _prepend_path(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")


def test_require_helm_missing(monkeypatch):
    monkeypatch.setattr(helm_client.shutil, "which", lambda _name: None)
    with pytest.raises(HelmError, match="not found on PATH"):
        helm_client._require_helm()


def test_run_reports_elapsed_progress_then_times_out(tmp_path, monkeypatch):
    bin_dir = _write_fake_helm(
        tmp_path,
        """\
        #!/usr/bin/env bash
        sleep 10
        """,
    )
    _prepend_path(monkeypatch, bin_dir)

    messages = []
    with pytest.raises(HelmError, match="timed out after 2s"):
        _run(["repo", "update", "jfrog"], timeout=2, on_progress=messages.append)

    assert any("elapsed" in m for m in messages), messages


def test_run_surfaces_nonzero_exit_with_stderr(tmp_path, monkeypatch):
    bin_dir = _write_fake_helm(
        tmp_path,
        """\
        #!/usr/bin/env bash
        echo "dial tcp: lookup charts.jfrog.io: no such host" 1>&2
        exit 1
        """,
    )
    _prepend_path(monkeypatch, bin_dir)

    with pytest.raises(HelmError, match="network"):
        _run(["repo", "update", "jfrog"], timeout=5)


def test_run_launch_failure_wrapped_as_helm_error(monkeypatch):
    # If the OS refuses to even launch the helm process (e.g. permission
    # denied, corrupt binary), that should surface as a clean HelmError,
    # not a raw OSError bubbling up to the caller.
    monkeypatch.setattr(helm_client, "_require_helm", lambda: "/usr/bin/helm")

    def _boom(*_args, **_kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(subprocess, "Popen", _boom)

    with pytest.raises(HelmError, match="Failed to launch"):
        _run(["version"], timeout=5)


def test_list_versions_raises_on_malformed_json(tmp_path, monkeypatch):
    bin_dir = _write_fake_helm(
        tmp_path,
        """\
        #!/usr/bin/env bash
        if [ "$1" = "repo" ]; then exit 0; fi
        echo "not json"
        """,
    )
    _prepend_path(monkeypatch, bin_dir)

    with pytest.raises(HelmError, match="Couldn't parse"):
        helm_client.list_versions()


def test_list_versions_raises_when_empty(tmp_path, monkeypatch):
    bin_dir = _write_fake_helm(
        tmp_path,
        """\
        #!/usr/bin/env bash
        if [ "$1" = "repo" ]; then exit 0; fi
        echo "[]"
        """,
    )
    _prepend_path(monkeypatch, bin_dir)

    with pytest.raises(HelmError, match="no versions"):
        helm_client.list_versions()


def test_explain_failure_flags_network_errors():
    message = _explain_failure(
        "helm repo update jfrog", 1, "Error: no such host charts.jfrog.io"
    )
    assert "network" in message.lower()


def test_explain_failure_flags_missing_version():
    message = _explain_failure(
        "helm pull jfrog/jfrog-platform", 1, "Error: chart version 99.99.99 not found"
    )
    assert "helm search repo" in message
