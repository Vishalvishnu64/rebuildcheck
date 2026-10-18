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

## How it works

1. The CLI runs nine defensive static checks against the requested repository.
2. It detects the documented test command and clones the committed repository
   into a temporary clean-room directory.
3. It runs that command without a shell, captures bounded output, and reports
   failures and timeouts clearly.
4. With `--docker`, it also runs `docker build` in the clean-room clone when
   Docker is installed; unavailable Docker is reported as skipped.
5. The score covers every evaluated static or execution check. The process
   exits non-zero when a required check or execution check fails.

## Known limitations and next ideas

`rebuildcheck` is a focused reproducibility smoke test, not a proof that every
clean-machine failure has been eliminated. It does not detect yet:

- Secret detection is incomplete: the scanner targets common token formats and
  literal assignments, so custom, short, encoded, or obfuscated credentials
  can be missed.
- Every machine-specific path: the path check looks for obvious user-specific
  Unix, Windows, and WSL patterns, but it does not understand every host-only
  setting or generated binary.
- Dependency graph problems: a recognised lockfile can still be stale,
  incomplete, missing hashes, or inconsistent with the dependency manifest.
  The checker does not resolve or install dependencies for the target project.
- Full runtime or container correctness: runtime files are checked for a
  conventional version pin and a Dockerfile is checked for a base image. A
  Docker build is optional, and neither check proves that all platforms,
  system packages, registries, or runtime services will be available.
- External test prerequisites: the clean-room run clones committed files and
  executes a small set of supported commands, but it does not provision
  databases, credentials, services, or arbitrary network access. Unsupported
  command forms are not executed through a shell.
- Delivery configuration: the tool does not currently validate CI workflow
  semantics, branch protection, remote permissions, or whether a repository's
  documented deployment process actually succeeds.

Useful next ideas are deeper manifest/lockfile consistency checks, broader
secret and host-path detectors, and optional service-aware or multi-platform
verification.

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

The Docker build needs network access on a clean machine: it pulls the pinned
Python base image and installs the `git` and `make` system packages with
`apt-get`. Once those layers are cached, the build-time test command itself is
offline.

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

Two small source fixtures show the intended outcomes. The deliberately broken
path and token are generated only inside the temporary materialized repository,
so the project itself contains no complete secret or machine-specific path:

- `python examples/materialize.py passing` creates a temporary standalone Git
  repo, exits 0, and reports **100/100**; all static checks pass and the
  clean-room `make test` passes.
- `python examples/materialize.py broken --no-run` exits non-zero, reports
  **66/100**, and lists the missing lockfile, absolute-path, and fake-secret
  fixes in priority order. Append `--json` to validate machine-readable output.

`materialize.py` copies a source snapshot to a temporary directory, injects
the deliberate broken values only for the broken fixture, creates its Git
commit, and runs the real CLI. The committed source snapshots themselves are
safe for the parent repository's 100/100 self-check.

## Sample output

Representative output from the clean self-check:

```text
rebuildcheck report: .
Score: 100/100

Static checks:
  PASS Git Repository — The directory is a Git worktree with at least one commit and a clean working tree.
  PASS Dependency Lockfile — A recognized, non-empty dependency lockfile is present.
  PASS Runtime Version — A conventional runtime-version file contains a concrete version pin.
  PASS Dockerfile — The root Dockerfile contains a FROM instruction for a container base image.
  PASS Test Command — The README documents a recognizable command for running tests.
  PASS Readme Sections — The README contains setup, run, and test documentation headings.
  PASS Environment Example — .env.example exists and all dotenv-like files contain safe placeholder or non-secret values.
  PASS Absolute Paths — No obvious user-specific absolute paths were found in scanned files.
  PASS Committed Secrets — No obvious secrets were found in scanned files.

Clean-room test: PASSED — Test command passed.
  Command: make test
Docker build: SKIPPED — Docker build was not requested; skipped.

Fixes (priority order):
  None — the repository is ready for a clean rebuild.
```
