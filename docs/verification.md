# Python extraction verification

Date: 2026-10-20, Asia/Seoul.

The reference is `vimkim/my-cubrid` commit `601223548ef761e46bd407fb1eaf20dd050f9a1d`.
The original coordinator has SHA-256 `cf9642bb2cb56c1324c1bb32b92d2144e8bbba58b9d45131e6860dc4f25e53b6`.
The fixtures under `tests/fixtures/bash` preserve that coordinator and its sibling inspector byte for byte.
The imported test assertions remain intact. Entrypoint discovery and diagnostic-failure injection accommodate the Python package.
The shared-recipe test runs when a `my-cubrid` checkout is available.

## Automated acceptance

From this project's root:

```sh
python3 -B -m unittest discover -s tests -v

COORDINATOR_UNDER_TEST="$PWD/tests/fixtures/bash/cubrid-build-coordinator.sh" \
INSPECTOR_UNDER_TEST="$PWD/tests/fixtures/bash/cubrid-lock-status.py" \
python3 -B -m unittest discover -s tests -p 'test_*legacy.py' -v
```

Python 3.14.6 and Python 3.10.6: **51 tests passed on each**. Bash baseline: **28 tests passed**.
The full suite includes a real 30-second stop timeout against a disposable native executable.
Fixtures use temporary installations, registries, commands, and lock namespaces.

| Area | Evidence |
| --- | --- |
| Resource identity | Symlinks, missing components, loops, filesystem bytes, newline paths, GNU canonicalization, and mixed-caller newline hash compatibility |
| Mixed concurrency | Bash/Python exclusion in both directions, aliases, shared installs across different builds, and distinct resources |
| Build ownership | Same-build contention and build-lock retention through a runtime wait |
| Installation | Compile then 75, install-only retry, native-process refusal, arbitrary targets, and both JDK transitions |
| Workenv | Failure propagation, initialization refusal, shell cleanup/source behavior, separate stdout, named registrations, and stale PWD repair |
| Supervision | Nonzero status, argument boundaries, stdin, PTY input, TERM/INT/HUP cleanup retention, and no daemon lock inheritance |
| Stop/delete | Compile-before-stop, registered Server/HA-Server ordering, no restart, real stop timeout, broad paths, canonical aliases, and storage markers |
| Inspection | Live supervisor owners, unrelated openers, owner replacement, unknown state, schema version 1, and credential redaction |

Two deliberate protections supplement the original behavior: selection must return the locked installation, and deletion checks canonical guarded paths.
The full canonical path remains available for filesystem work, while lock hashes retain Bash's trailing-newline stripping for mixed callers.
The Python supervisor also handles SIGQUIT consistently with its other forwarded signals.
Waits use monotonic deadlines. Polling cadence and diagnostic formatting can differ from Bash.
Launch failures retain Bash's status 1, rather than conventional shell statuses 126 or 127.

## Caller integration

Set `CUBRID_BUILD_COORDINATOR_CLI` to the reviewed launcher before running these commands from the candidate `my-cubrid` checkout:

```sh
python3 -B tests/runtime-lock-test.py
python3 -B tests/lock-status-test.py
python3 -B tests/coordinator-shim-test.py
python3 -B tests/runtime-environment-test.py
CUB_WORKENV_CLI=/path/to/cubrid-workenv/bin/cub-workenv \
python3 -B tests/workenv-integration-test.py
python3 -B tests/workenv-container-test.py
```

Results: **15 + 13 + 3 + 15 + 1 tests passed**, followed by a successful real rootless Podman lifetime test.
The shim test proves exec PID preservation, exact arguments, stdin, exit status, missing-CLI guidance, editor/debugger recipe input, build-log failure propagation, and build-only ctest.
The workenv test uses the actual selector and verifies selected environment loading without changing saved workenv state.

The Podman test bind-mounts harmless native `true` probes into the local UBI 9 image.
After its test completes, installation and deletion both return 75 while the container remains available.
It signals the Python supervisor, proves owned container removal, then obtains the released installation lock with status 0.
The fixture uses one `XDG_RUNTIME_DIR` for every Podman operation because crun also uses that setting for its state path.
An initial inconsistent probe environment failed. The probes succeeded after every call received the fixture environment.

Local raw Podman evidence is `/tmp/cwe-container-4k69h_s1/results.json` and its adjacent logs.
Its verdict is `pass`. The harness deleted its owned container.
Temporary evidence is supplemental. The committed tests are the reproducible verification record.

## Limits and integration order

These checks do not compile a complete CUBRID source tree or run SQL against user databases.
The container test proves installation lifetime and cleanup, not CUBRID SQL behavior.
The native idle check retains its cooperative and visibility limits described in the [README](../README.md).
GitHub Actions is configured for Python 3.10 and 3.14, but no hosted run has occurred before publication.

A wheel installation into a temporary virtual environment passed.
The installed command and module entrypoints worked from outside the source checkout.
Source-link installation, repeated installation, and refusal to replace a foreign executable also passed.

Markdown source checks passed. Mermaid validation of the existing integration guide could not run because the installed checker lacks Playwright.
The checker requests `just checker-setup` from docserve's primary checkout.
No diagram source changed during this extraction.

After approval, merge the Python project before the `my-cubrid` shim.
Install the launcher from the permanent checkout before consumers use the merged shim.
Retain the old read-only `cubrid-lock-status.py` compatibility helper in `my-cubrid` for direct consumers.
The Python coordinator uses the packaged inspector and does not depend on that helper.
No changes to `cubrid-workenv` or the CUBRID engine are required.

## Build stage terminology verification — 2026-10-21

The current stages are `configure`, `build`, and `install`; `build-and-install`
combines the last two. Explicit stopping uses `build-and-install --stop-runtime`.
The extraction evidence above retains the command names used at that time.
`tests/cli_compat.py` translates current actions only for the frozen Bash fixture;
production commands use the new names directly.

With the three candidate checkouts selected through
`MY_CUBRID_UNDER_TEST`, `CUBRID_BUILD_COORDINATOR_CLI`, and `CUB_WORKENV_CLI`:

| Check | Result |
| --- | --- |
| Coordinator acceptance and validation | 88 passed |
| Frozen Bash baseline | 28 passed |
| Companion my-cubrid runtime locks, lock status, shim/recipes, loader, and workenv integration | 15 + 13 + 4 + 15 + 1 passed |
| Companion cubrid-workenv unit suite | 39 passed |

The real CMake recipe test proves that `configure-build` produces artifacts
while installation is locked, `build-and-install` defers with 75, `install`
uses existing artifacts after the source changes, and
`configure-build-and-install` refreshes the installation without initializing a DB.
Stop-option validation and retry guidance, stage-specific success messages,
and build-log failure propagation are covered. The original Bash fixture is unchanged.
No complete CUBRID engine rebuild or native DB/container regression run was performed
for this terminology change.

Apply the coordinator and shared recipes together; the coordinator's installed
CLI must be refreshed from the permanent checkout before using the new recipes.
The companion workenv change updates diagnostics, workflow guidance, and its
shared-recipe probe without changing host initialization behavior.
