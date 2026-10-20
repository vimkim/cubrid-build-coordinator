# Manage the uv environment and install the CLI from this checkout.
default:
    @just --list

# Prepare the locked project environment.
setup:
    uv sync --locked

test:
    uv run --locked python -B -m unittest discover -s tests -v

test-bash:
    COORDINATOR_UNDER_TEST="$PWD/tests/fixtures/bash/cubrid-build-coordinator.sh" INSPECTOR_UNDER_TEST="$PWD/tests/fixtures/bash/cubrid-lock-status.py" uv run --locked python -B -m unittest discover -s tests -p 'test_*legacy.py' -v

# Build the source distribution and wheel.
build:
    uv build

alias install := sync

# Install or refresh the CLI for daily-update-cubrid.
sync:
    #!/usr/bin/env bash
    set -euo pipefail
    source_cli="$(uv run --locked python -c 'import sysconfig; print(sysconfig.get_path("scripts") + "/cubrid-build-coordinator")')"
    source_cli="$(realpath -- "$source_cli")"
    legacy_cli="$(realpath -- bin/cubrid-build-coordinator)"
    install_dir="${CUBRID_BUILD_COORDINATOR_BIN_DIR:-$HOME/.local/bin}"
    mkdir -p -- "$install_dir"
    destination="$(realpath -- "$install_dir")/cubrid-build-coordinator"
    if [[ -e "$destination" || -L "$destination" ]]; then
        current_cli="$(readlink -f -- "$destination" || true)"
        if [[ ! -L "$destination" || ( "$current_cli" != "$source_cli" && "$current_cli" != "$legacy_cli" ) ]]; then
            printf 'Refusing to replace existing path: %s\n' "$destination" >&2
            exit 1
        fi
        if [[ "$current_cli" != "$source_cli" ]]; then
            ln -sfn -- "$source_cli" "$destination"
        fi
    else
        ln -s -- "$source_cli" "$destination"
    fi
    "$destination" --help >/dev/null
    printf 'Installed %s -> %s\n' "$destination" "$source_cli"
