# CUBRID build coordinator

A foreground Python CLI for CUBRID build directories and installations on Linux.
It serializes builds, protects installations during commands, and defers installation when a prefix is busy.
Compilation can finish while an editor, database, or test harness continues running.
Busy operations return **75**, so an installation can be retried later.

한국어 설명: [무엇을 하는 도구인가, 왜 필요한가, 각 action을 사용하지 않으면 무엇이 달라지는가](docs/overview.ko.md).

## Requirements and installation

Use Python 3.10 or newer, Linux `/proc`, GNU `realpath`, and CMake.
Host runtime selection also needs Bash and `cub-workenv`.
Use [uv](https://docs.astral.sh/uv/) to manage the project environment.
The Python package has no third-party runtime dependencies.

Prepare the locked environment and run the CLI:

```sh
uv sync --locked
uv run --locked cubrid-build-coordinator --help
```

The `.python-version` file selects Python 3.14 for development. Python 3.10 remains supported.
Commit `uv.lock` after dependency changes. Use `uv add` or `uv remove` to manage dependencies.

Run `just sync` to install or refresh an independent CLI with `uv tool install --reinstall .`.
`just install` remains an alias. The regular installation runs without the source checkout or its `.venv`.
Run sync again after source changes, even when the package version stays the same.
Use `uv run --locked cubrid-build-coordinator` for development against the current source.

uv manages the tool environment and executable directory, normally `~/.local/bin`.
Use `uv tool dir --bin` to locate the commands, and `UV_TOOL_BIN_DIR` to choose another directory.
`UV_TOOL_BIN_DIR` replaces `CUBRID_BUILD_COORDINATOR_BIN_DIR`.
Run `uv tool update-shell` if the command directory is absent from PATH.
Remove the installed CLI with `uv tool uninstall cubrid-build-coordinator`.
Tool installation resolves dependencies separately from the project's `uv.lock`.
This package currently has no third-party runtime dependencies.

An existing legacy symlink can make the first sync fail with `Executable already exists`.
Inspect its target with `readlink -f "$(uv tool dir --bin)/cubrid-build-coordinator"`.
If it points to this checkout's `bin/cubrid-build-coordinator` or `.venv/bin/cubrid-build-coordinator`,
migrate it once with `uv tool install --reinstall --force .`. Subsequent refreshes use `just sync`.

The `my-cubrid` daily updater includes this project in its default list.
To update only this tool, run `daily-update-cubrid /path/to/cubrid-build-coordinator`.

Existing `my-cubrid` recipes use an `exec` shim at `bin/cubrid-build-coordinator.sh`.
Install this CLI before integrating that shim.
For candidate testing, set `CUBRID_BUILD_COORDINATOR_CLI` to this checkout's absolute launcher path.
The shim has no fallback implementation.

## Actions

All actions except `locks` and help require `PRESET_MODE`, `CUBRID_BUILD_DIR`, and `CUBRID`.
Timeout arguments are non-negative integer seconds.

| Action | Behavior |
| --- | --- |
| `configure` | Configure the selected CMake preset under the build lock |
| `compile` | Compile under the build lock |
| `target TARGET` | Build a target under both locks after checking native processes |
| `build [SECONDS]` | Compile, acquire an idle installation, then install |
| `install [SECONDS]` | Install existing build output into an idle installation |
| `install-target TARGET [SECONDS]` | Build a possible installation target under both locks |
| `runtime [SECONDS] [--database NAME] -- COMMAND...` | Lock the installation, select the host environment, then supervise the command |
| `installation-use [SECONDS] [--database NAME] -- COMMAND...` | Supervise a command under the installation lock without host selection |
| `installation-delete [SECONDS]` | Require both locks, native inactivity, and deletion guards before removing the prefix |
| `stop-and-build` | Compile, select and explicitly stop the runtime, wait for exit, then install |
| `locks [--all] [--json]` | Inspect owners without creating or acquiring locks |

The build-lock default is 600 seconds, configured by `CUBRID_BUILD_LOCK_TIMEOUT`.
The runtime default is 300 seconds, configured by `CUBRID_RUNTIME_LOCK_TIMEOUT`.
Full and partial installations default to **zero** seconds, regardless of that runtime setting.
Timeouts limit acquisition or idle waiting, not compilation or command execution.
The timeout before `--database` is optional. All arguments and applicable timeout defaults are checked before locks or commands.
Use the command separator. The CLI never shell-expands command arguments.

```sh
cubrid-build-coordinator build
# If compilation succeeds but installation returns 75:
cubrid-build-coordinator install
cubrid-build-coordinator runtime 300 --database example -- csql -S -u dba example
cubrid-build-coordinator installation-use 300 -- ctest --test-dir "$CUBRID_BUILD_DIR"
cubrid-build-coordinator locks --all --json
```

Success returns 0, explicit usage errors return 64, and contention returns 75.
Child and selection failures retain their status. Signal termination returns `128 + signal`.
As with the Bash implementation, a command launch failure returns 1.
Inspector argument errors return 2. Unknown inspection state returns 1.

## Target validation and failure reports

Before configuration or compilation, the coordinator resolves the selected preset's `binaryDir` and compares it with the locked `CUBRID_BUILD_DIR`.
Target actions also compare the cached `CMAKE_INSTALL_PREFIX` with the locked `CUBRID` before running the target.
A mismatch returns 64 and prints both paths and the correction needed. Missing or unsupported path definitions also return 64.
Full installation uses the canonical locked build and installation paths, including after host environment selection.
Installation actions reject non-empty [DESTDIR](https://cmake.org/cmake/help/latest/envvar/DESTDIR.html), including target preset environments, because it relocates files beyond the locked prefix.
Target checks also reject a cached [CMAKE_STAGING_PREFIX](https://cmake.org/cmake/help/latest/variable/CMAKE_STAGING_PREFIX.html) outside the locked installation.
Native tool options that override build directories/files or supply `DESTDIR` are rejected before building.
Native options with macros also require literal values so that path overrides cannot be hidden by expansion.

The resolver reads project/user presets, includes, inheritance, and path macros without executing CMake.
It follows the [CMake preset path rules](https://cmake.org/cmake/help/latest/manual/cmake-presets.7.html).
Inherited `${fileDir}` values from a different preset directory are rejected because CMake versions interpret them differently.
Use `${sourceDir}` or an absolute path for that case.
The prefix check covers conventional CMake installation targets. It cannot determine every path changed by custom target scripts or absolute installation destinations.
Direct edits to preset files and filesystem aliases still require coordination by callers.

Failures print the action, reason, and exit status on stderr.
Child failures also identify the execution phase and command. Common credential options and inline shell/Python/SQL code are hidden in command displays.
Signal failures name the signal. Child output remains available on the original streams.
Busy reports preserve the requested action in retry guidance. A compiled `build` suggests `install`.
Stop failures list the stop commands attempted and explain that services may remain stopped.
Installation failures report possible partial changes and the absence of automatic rollback or restart.

## Ownership and limits

Build locks use canonical build paths. Installation locks use canonical installation paths.
Both retain the Bash namespace, SHA-256 filesystem-byte keys, and exclusive Linux `flock` semantics:

```text
${XDG_RUNTIME_DIR:-/tmp}/cubrid-dev-locks-${UID}/
    build-<SHA256 of realpath -m CUBRID_BUILD_DIR>.lock
    runtime-<SHA256 of realpath -m CUBRID>.lock
```

Bash strips trailing newline bytes from canonical paths before hashing.
Python retains that lock-key quirk for mixed callers, while using complete canonical paths for filesystem operations.

The coordinator acquires the build lock first and retains it through installation waits.
Its foreground supervisor owns non-inheritable descriptors and waits through child cleanup.
TERM, INT, and HUP reach the immediate child. This is not a process-tree termination mechanism.
Forced supervisor termination cannot guarantee cleanup ordering.

Native executable observation supplements the cooperative lock.
The scan cannot exclude a concurrent direct launch or identify processes that only map libraries.
Permissions and PID namespaces also limit visibility.
Different users or runtime directories do not share this namespace.
Lock files remain after release. Removing a lock pathname can split callers across different inodes.

`cub-workenv` owns initialization, selection, configuration, and database registration.
The Bash adapter evaluates its existing emitted shell code and captures the resulting environment through a separate descriptor.
Selection failure prevents command execution. A selected installation must match the locked installation.
Build and install never initialize databases. Database-name checks establish registration only.

Ordinary builds never stop processes.
`stop-and-build` stops registered servers, service, and broker, then waits up to 30 seconds without forced killing or automatic restart.
Deletion refuses prefixes containing cwd, home, the build directory, or regular `*_vinf`/`*_lgat` files.
The path guards check canonical aliases as well as lexical paths.
They do not inventory external database storage.
JDK destination adjustment and installation can leave partial changes after failure.

## Development and verification

Run `just test` for the Python acceptance and imported baseline suites.
Run `just test-bash` for the unchanged Bash baseline fixtures.
Both recipes run through `uv run --locked`.
Use `just setup` for `uv sync --locked`, and `just build` for `uv build`.
The build produces a wheel and source distribution under `dist/`.
CI uses uv with both Python 3.10 and 3.14.
See [verification evidence](docs/verification.md) for the original extraction checks.
Optional shared-recipe checks use `MY_CUBRID_UNDER_TEST` when that checkout is available.

The implementation separates action policy, locks, process supervision, workenv adaptation, and owner inspection.
GNU `realpath -m -z` deliberately remains the canonicalization authority, including for missing components and symlink loops.
Python handles locking and hashing directly, so the CLI no longer requires the `flock` or `sha256sum` executables.
Owner JSON retains schema version 1 and credential redaction.

The inspector and baseline test fixtures originate from `vimkim/my-cubrid` at
`601223548ef761e46bd407fb1eaf20dd050f9a1d`.
The tests retain the Bash fixture for parity checks.
