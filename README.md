# rebuildcheck

`rebuildcheck` is a Python 3.12 command-line tool for finding out whether a
repository is likely to survive a judge's clean rebuild. It checks the files
and Git state, clones the committed repository into a temporary directory, and
runs the documented test command there.

## Setup

1. Install Python 3.12.
2. Clone this repository and enter its directory.
3. No third-party Python packages are required; the core uses only the
   standard library. The pinned runtime is recorded in `.python-version`.

## Run

Run the checker with a repository path:

```text
python -m rebuildcheck <repo_path>
```

Useful options:

- `--json` prints a machine-readable report.
- `--docker` also attempts `docker build` when Docker is installed.
- `--no-run` performs static checks only and skips the clean-room execution.

For example, to check the current repository without running its tests:

```text
python -m rebuildcheck . --no-run
```

The command exits with status 0 only when every required check that ran passed.

## Test

Run the offline test suite from the project root:

```text
make test
```

The same command works without downloading dependencies:

```text
python -m unittest discover -s tests -v
```

## Deploy

Build the reproducible container image from the project root:

```text
docker build -t rebuildcheck .
```

The Dockerfile uses the real `python:3.12.8-slim-bookworm` tag together with
its immutable multi-architecture manifest digest. Its `/app` and `/repo`
paths are inside the container; no host-specific absolute path is baked into
the image.

The image runs the CLI entry point and defaults to its help screen. Verify the
image and run its test suite with:

```text
docker run --rm rebuildcheck --help
docker run --rm --entrypoint python rebuildcheck -m unittest discover -s tests -v
```

To check another local Git repository, mount it read-only at a container path:

```text
docker run --rm -v "$PWD/../other-repo:/repo:ro" rebuildcheck /repo
```

The image includes `git` and `make` because the clean-room verifier clones the
target and may execute a documented `make test` command. The Docker build also
runs the same offline test command during `docker build`.

## Example reports

Two small fixtures show the intended outcomes:

- `python examples/materialize.py passing` creates a temporary standalone Git
  repo, exits 0, and reports **100/100**; all static checks pass and the
  clean-room `make test` passes.
- `python -m rebuildcheck examples/broken --no-run` exits non-zero and lists
  priority-ordered fixes. It reports the deliberately missing lockfile,
  Unix home-path, and fake GitHub token findings. Add `--json` to receive
  valid machine-readable output; because this source snapshot is nested in the
  parent repository, the direct run also reports that it is not a standalone
  repository root.
- With the default run command (without `--no-run`), the expected broken sample
  report is **50/100** and the fixes are ordered as P1 secret/Git-root fixes,
  P2 path/lockfile fixes, then the P3 clean-room clone fix.
- `python examples/materialize.py broken --no-run` shows the same deliberate
  lockfile, absolute-path, and fake-secret failures in a standalone committed
  fixture.

The fixture directories are source snapshots because a normal parent Git clone
does not preserve nested `.git` metadata. `materialize.py` copies one snapshot
to a temporary directory, creates its commit, and runs the real CLI. The
`.rebuildcheck-fixture` marker keeps these intentionally broken sample trees
out of scans of the parent project itself; the materialized copies have no
marker and are scanned normally.
