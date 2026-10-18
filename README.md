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

1. The CLI detects a supported project type from root-level marker files and
   includes it in the human and JSON reports. If the root has no recognised
   project, it reports recognised projects in immediate subfolders.
2. It runs ten defensive static checks against the requested repository,
   including Windows-to-Linux portability checks.
3. It detects the documented test command and clones the committed repository
   into a temporary clean-room directory.
4. It runs that command without a shell, captures bounded output, and reports
   failures and timeouts clearly.
5. With `--docker`, it also runs `docker build` in the clean-room clone when
   Docker is installed; unavailable Docker is reported as skipped.
6. The score covers every evaluated static or execution check. The process
   exits non-zero when a required check or execution check fails.

## Windows-to-Linux portability check

The `portability` check looks for CRLF line endings in shell scripts,
Makefiles, and Dockerfiles; shebang scripts that are not executable in the Git
index; and README paths whose capitalisation differs from the repository path.
Each finding includes its relative file path, line number, and a concrete fix in
the human-readable and structured reports. Secret evidence is bounded to at
most four leading characters followed by `****`; complete credential values
are never printed. NUL-containing binary files are ignored rather than treated
as text.

## Known limitations and next ideas

`rebuildcheck` is a focused reproducibility smoke test, not a proof that every
clean-machine failure has been eliminated. It does not detect yet:

- Secret detection is incomplete: the scanner targets common token formats and
  literal assignments, skips recognised lockfiles and build/binary output, and
  treats obvious dummy values as safe. Custom, short, encoded, or obfuscated
  credentials can still be missed or require review.
- Every machine-specific path: the path check looks for obvious user-specific
  Unix, Windows, and WSL patterns, but it does not understand every host-only
  setting or generated binary.
- Every portability issue: the portability check is intentionally conservative
  and only recognises common script names, shebangs, and README path forms. It
  does not model all filesystem semantics, generated files, or tools that
  rewrite line endings outside Git.
- Every project in a monorepo: only immediate subfolders are inspected when
  the root has no recognised project files; nested workspaces and mixed or
  ambiguous subfolders are not recursively classified.
- Every environment-variable reference: the missing `.env.example` priority
  uses a bounded set of common source-code patterns, so unusual configuration
  loaders may not be recognised.
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
secret and host-path detectors, recursive workspace discovery, and optional
service-aware or multi-platform verification.

## Test

Run the offline test suite from the project root:

```text
make test
```

The same command works without downloading dependencies:

```text
python -m unittest discover -s tests -v
```

## Supported ecosystems

Project type is inferred from the files present at the repository root. The
checks recognise these reproducibility markers and test commands:

| Ecosystem | Dependency lockfiles | Pinned runtime | Test commands |
| --- | --- | --- | --- |
| Python | `requirements.lock`, `poetry.lock`, `uv.lock` | `.python-version` | Pytest or Python's unittest module |
| Node | `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml` | `.nvmrc` | `npm test` |
| Go | `go.sum` | `go.mod` `go` directive | `go test` |
| Rust | `Cargo.lock` | `rust-toolchain.toml` | `cargo test` |
| Dart | `pubspec.lock` | Not inferred from `pubspec.yaml` | `dart test` |
| Flutter | `pubspec.lock` | Not inferred from `pubspec.yaml` | `flutter test` |

The project type is reported as `unknown` when no supported marker is present
or when markers from multiple ecosystems make a single classification
ambiguous. A Flutter type requires a clear `flutter` or `flutter_test`
dependency in `pubspec.yaml`; otherwise a Dart pubspec is reported as Dart.
Lockfiles are checked for presence and usable content; the tool does not
resolve dependencies or install another ecosystem's toolchain.

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
  **70/100**, and lists the missing lockfile, absolute-path, and fake-secret
  fixes in priority order. Append `--json` to validate machine-readable output.

`materialize.py` copies a source snapshot to a temporary directory, injects
the deliberate broken values only for the broken fixture, creates its Git
commit, and runs the real CLI. The committed source snapshots themselves are
safe for the parent repository's 100/100 self-check.

## Sample output

Representative output from the clean self-check:

```text
rebuildcheck report: .
Project type: Python
Score: 100/100

Static checks:
  PASS Git Repository — The directory is a Git worktree with at least one commit and a clean working tree.
  PASS Dependency Lockfile — A recognized, non-empty dependency lockfile is present.
  PASS Runtime Version — A conventional runtime-version file contains a concrete version pin.
  PASS Dockerfile — The root Dockerfile contains a FROM instruction for a container base image.
  PASS Test Command — The README documents a recognizable command for running tests.
  PASS Readme Sections — The README contains setup, run, and test documentation headings.
  PASS Portability — No Windows-to-Linux portability hazards were found.
  PASS Environment Example — .env.example exists and all dotenv-like files contain safe placeholder or non-secret values.
  PASS Absolute Paths — No obvious user-specific absolute paths were found in scanned files.
  PASS Committed Secrets — No obvious secrets were found in scanned files.

Clean-room test: PASSED — Test command passed.
  Command: make test
Docker build: SKIPPED — Docker build was not requested; skipped.

Fixes (priority order):
  None — the repository is ready for a clean rebuild.
```
