"""jfvb command-line entry point."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from jfvb import helm_client, images as images_mod
from jfvb.answers import Answers
from jfvb.answers_io import (
    EXAMPLE_ANSWERS_YAML,
    AnswersLoadError,
    load_answers,
)
from jfvb.builder import run_build
from jfvb.helm_client import HelmError
from jfvb.render import write_outputs
from jfvb.runbook import write_runbook
from jfvb.wizard import run_wizard

console = Console()


def _warn_about_untagged(found: list[str]) -> None:
    untagged = [image for image in found if images_mod.is_untagged(image)]
    if not untagged:
        return
    console.print(f"[yellow]![/yellow] {images_mod.UNTAGGED_WARNING}")
    for image in untagged:
        console.print(f"[dim]    · {image}[/dim]")


def _status_reporter(status, label: str):
    """Return an `on_progress` callback that keeps a rich `Status` spinner's
    text updated with what's actually running, so a slow network call
    counts up instead of sitting on a static "please wait" message.
    """

    def _report(message: str) -> None:
        status.update(f"[cyan]{label}[/cyan] [dim]({message})[/dim]")

    return _report


@click.group()
def main() -> None:
    """jfvb: JFrog Platform Values Builder."""


@main.command()
@click.option(
    "-o",
    "--output-dir",
    "output_dir",
    default="./output",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Directory to write customvalues.yaml, SUMMARY.md, RUNBOOK.md into.",
)
@click.option(
    "-a",
    "--answers",
    "answers_file",
    default=None,
    type=click.Path(path_type=Path, exists=True),
    help="YAML/JSON answers file for non-interactive mode (skips the wizard). "
    "See `jfvb example-answers`.",
)
@click.option(
    "--offline",
    is_flag=True,
    default=False,
    help="Skip live helm/network calls (no chart-baseline sizing fetch, no validation dry-run).",
)
@click.option(
    "--validate/--no-validate",
    default=True,
    show_default=True,
    help="Dry-run `helm template` with the generated file against the target chart version before saving.",
)
def build(
    output_dir: Path, answers_file: Path | None, offline: bool, validate: bool
) -> None:
    """Generate customvalues.yaml + SUMMARY.md + RUNBOOK.md.

    Interactive by default; pass --answers FILE to run non-interactively.
    """

    try:
        _run_build(output_dir, offline, validate, answers_file)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled -- nothing was written.[/yellow]")
        raise SystemExit(130)
    except AnswersLoadError as exc:
        console.print(f"\n[red]✗ Invalid answers file:[/red] {exc}")
        raise SystemExit(1)
    except HelmError as exc:
        console.print(f"\n[red]✗ {exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001 -- last-resort guard for a friendly exit
        console.print(f"\n[red]✗ Unexpected error:[/red] {exc}")
        console.print(
            "[dim]If this looks like a bug, please share this message and "
            "the command you ran.[/dim]"
        )
        raise SystemExit(1)


@main.command("example-answers")
@click.option(
    "-o",
    "--output",
    "output_path",
    default=None,
    type=click.Path(path_type=Path),
    help="Write the example to a file instead of stdout.",
)
def example_answers(output_path: Path | None) -> None:
    """Print a starter answers YAML for non-interactive `jfvb build --answers`."""

    if output_path:
        output_path.write_text(EXAMPLE_ANSWERS_YAML, encoding="utf-8")
        console.print(f"[green]✓[/green] Wrote example answers to [bold]{output_path}[/bold]")
    else:
        click.echo(EXAMPLE_ANSWERS_YAML, nl=False)


def _run_build(
    output_dir: Path,
    offline: bool,
    validate: bool,
    answers_file: Path | None = None,
) -> None:
    if answers_file is not None:
        answers = load_answers(answers_file)
        console.print(f"[dim]· loaded answers from {answers_file}[/dim]")
    else:
        answers = run_wizard()

    chart_dir = None
    chart_version = answers.chart_version
    repo_synced = False

    if not offline:
        try:
            with console.status("[cyan]Contacting charts.jfrog.io...[/cyan]") as status:
                progress = _status_reporter(status, "Contacting charts.jfrog.io")
                if not chart_version:
                    chart_version = helm_client.latest_version(on_progress=progress)
                    repo_synced = True
                chart_dir = helm_client.pull_chart(
                    chart_version, update_repo=not repo_synced, on_progress=progress
                )
            console.print(f"[dim]· using chart version {chart_version}[/dim]")
        except HelmError as exc:
            console.print(f"[yellow]![/yellow] {exc}")
            console.print(
                "[yellow]  Continuing with reference-architecture-only sizing "
                "(no live chart baseline).[/yellow]"
            )

    # Make the concrete version visible to version-gated rules. When the
    # user left chart_version blank and we resolved "latest" above, write
    # that back; when helm was unavailable, leave None so features treat
    # it as "latest path".
    if chart_version:
        answers.chart_version = chart_version

    with console.status("[cyan]Building customvalues.yaml, SUMMARY.md, RUNBOOK.md...[/cyan]"):
        build_output = run_build(answers, chart_dir=chart_dir)
        paths = write_outputs(answers, build_output, output_dir, chart_version)
        runbook_path = write_runbook(answers, build_output, output_dir, chart_version)

    table = Table(box=None, show_header=False, padding=(0, 2, 0, 0))
    table.add_column(style="green")
    table.add_column()
    table.add_row("✓", str(paths["values"]))
    table.add_row("✓", str(paths["summary"]))
    table.add_row("✓", str(runbook_path))
    console.print(Panel(table, title="[bold green]Done[/bold green]", border_style="green", expand=False))

    if build_output.warnings:
        console.print(
            f"[yellow]![/yellow] {len(build_output.warnings)} warning(s) to review -- see SUMMARY.md"
        )

    if validate and not offline and chart_version:
        try:
            with console.status("[cyan]Validating with `helm template`...[/cyan]") as status:
                progress = _status_reporter(status, "Validating with helm template")
                helm_client.template(
                    version=chart_version,
                    release_name=answers.release_name,
                    namespace=answers.namespace,
                    values_files=[paths["values"]],
                    update_repo=False,
                    on_progress=progress,
                )
            console.print("[green]✓[/green] Validation OK -- the chart rendered without errors.")
        except HelmError as exc:
            console.print(f"[red]✗ Validation FAILED:[/red] {exc}")
            console.print(
                f"[dim]  The files above were still written to {output_dir} -- "
                "fix the issue and re-run with --no-validate if you want to "
                "skip this check.[/dim]"
            )
            raise SystemExit(1)

    if answers.airgapped:
        _generate_airgap_image_list(answers, output_dir, chart_version, offline, paths)


def _generate_airgap_image_list(
    answers: Answers,
    output_dir: Path,
    chart_version: str | None,
    offline: bool,
    paths: dict[str, Path],
) -> None:
    """Air-gapped installs need the full image list to mirror before they
    can install anything -- generate it automatically as part of the build
    (rather than requiring a separate manual `jfvb images` call) whenever
    live helm access is available.

    This deliberately re-renders instead of reusing the validation dry-run's
    manifest: that render applied `global.imageRegistry`, so every image in
    it already points at the internal mirror. The list we want here is the
    *source* images to pull, so discovery strips that override (see
    `images.list_images`).
    """

    if offline or not chart_version:
        console.print(
            "[yellow]![/yellow] Air-gapped mode: skipping automatic image-list "
            "generation (offline mode or no chart version resolved) -- run "
            "`jfvb images --version <CHART_VERSION> -f "
            f"{paths['values']} -o {output_dir}/images.txt` once you have "
            "helm/network access."
        )
        return

    try:
        with console.status(
            "[cyan]Rendering chart to list images for air-gap prep...[/cyan]"
        ) as status:
            progress = _status_reporter(status, "Rendering chart for image list")
            found = images_mod.list_images(
                version=chart_version,
                values_files=[paths["values"]],
                namespace=answers.namespace,
                release_name=answers.release_name,
                on_progress=progress,
            )
    except HelmError as exc:
        console.print(f"[yellow]![/yellow] Couldn't auto-generate the image list: {exc}")
        console.print(
            "[dim]  Run `jfvb images --version <CHART_VERSION> -f "
            f"{paths['values']} -o {output_dir}/images.txt` manually once "
            "this is resolved.[/dim]"
        )
        return

    if not found:
        console.print(
            "[yellow]![/yellow] No images found while rendering for air-gap "
            "prep -- this looks wrong, please double check manually with "
            "`jfvb images`."
        )
        return

    images_path = images_mod.write_image_list(
        found, output_dir / "images.txt", chart_version=chart_version
    )
    console.print(
        f"[green]✓[/green] Wrote {len(found)} images (+ chart download steps) to "
        f"[bold]{images_path}[/bold] (air-gapped prep, source registries preserved)"
    )
    _warn_about_untagged(found)
    console.print(f"[yellow]![/yellow] {images_mod.JAS_WARNING}")

    if answers.custom_image_registry:
        mirror_path = images_mod.write_mirror_script(
            found, answers.custom_image_registry, output_dir / "mirror-images.sh"
        )
        console.print(f"[green]✓[/green] Wrote mirror script to [bold]{mirror_path}[/bold]")


@main.command()
@click.option("--version", "chart_version", required=True, help="jfrog-platform chart version.")
@click.option(
    "-f",
    "--values",
    "values_files",
    multiple=True,
    type=click.Path(path_type=Path, exists=True),
    help="Optional values file(s) to render with (e.g. your generated customvalues.yaml) so only the enabled modules' images are listed.",
)
@click.option(
    "-o",
    "--output",
    "output_path",
    default="images.txt",
    type=click.Path(path_type=Path),
    help="Where to write the image list.",
)
@click.option(
    "--target-registry",
    default=None,
    help="If set, also generate a mirror-images.sh (skopeo copy) script targeting this registry.",
)
@click.option("--namespace", default="jfrog-platform", show_default=True)
@click.option("--release-name", default="jfrog-platform", show_default=True)
@click.option(
    "--keep-registry-override/--strip-registry-override",
    default=False,
    show_default=True,
    help="Keep global.imageRegistry from the values file, listing images as they "
    "look AFTER mirroring. Default strips it so you get the source images to pull.",
)
def images(
    chart_version: str,
    values_files: tuple[Path, ...],
    output_path: Path,
    target_registry: str | None,
    namespace: str,
    release_name: str,
    keep_registry_override: bool,
) -> None:
    """List every container image referenced by the chart, for air-gapped prep."""

    try:
        with console.status(f"[cyan]Rendering jfrog-platform {chart_version}...[/cyan]") as status:
            progress = _status_reporter(status, f"Rendering jfrog-platform {chart_version}")
            found = images_mod.list_images(
                version=chart_version,
                values_files=list(values_files) or None,
                namespace=namespace,
                release_name=release_name,
                on_progress=progress,
                strip_registry_override=not keep_registry_override,
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled -- nothing was written.[/yellow]")
        raise SystemExit(130)
    except HelmError as exc:
        console.print(f"\n[red]✗ {exc}[/red]")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001 -- last-resort guard for a friendly exit
        console.print(f"\n[red]✗ Unexpected error:[/red] {exc}")
        raise SystemExit(1)

    if not found:
        console.print(
            "[yellow]![/yellow] No images were found in the rendered manifests -- "
            "double check the chart version and any -f values file(s) you passed."
        )

    images_mod.write_image_list(found, output_path, chart_version=chart_version)
    console.print(
        f"[green]✓[/green] Wrote {len(found)} images (+ chart download steps) to "
        f"[bold]{output_path}[/bold]"
    )
    _warn_about_untagged(found)
    console.print(f"[yellow]![/yellow] {images_mod.JAS_WARNING}")

    if target_registry:
        script_path = output_path.parent / "mirror-images.sh"
        images_mod.write_mirror_script(found, target_registry, script_path)
        console.print(f"[green]✓[/green] Wrote mirror script to [bold]{script_path}[/bold]")


if __name__ == "__main__":
    main()
