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

The image runs the CLI entry point. Use `docker run --rm rebuildcheck --help`
to verify the image after building, then provide the repository path mounted
into the container when checking another project.
