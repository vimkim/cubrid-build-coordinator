# Run from the checkout, or link its launcher into the user's PATH.
default:
    @just --list

test:
    python3 -B -m unittest discover -s tests -v

test-bash:
    COORDINATOR_UNDER_TEST="$PWD/tests/fixtures/bash/cubrid-build-coordinator.sh" INSPECTOR_UNDER_TEST="$PWD/tests/fixtures/bash/cubrid-lock-status.py" python3 -B -m unittest discover -s tests -p 'test_*legacy.py' -v

# Preserve existing files and links to other checkouts.
install:
    #!/usr/bin/env bash
    set -euo pipefail
    source_cli="$(realpath -- bin/cubrid-build-coordinator)"
    install_dir="${CUBRID_BUILD_COORDINATOR_BIN_DIR:-$HOME/.local/bin}"
    mkdir -p -- "$install_dir"
    destination="$(realpath -- "$install_dir")/cubrid-build-coordinator"
    if [[ -e "$destination" || -L "$destination" ]]; then
        if [[ ! -L "$destination" || "$(readlink -f -- "$destination")" != "$source_cli" ]]; then
            printf 'Refusing to replace existing path: %s\n' "$destination" >&2
            exit 1
        fi
    else
        ln -s -- "$source_cli" "$destination"
    fi
    "$destination" --help >/dev/null
    printf 'Installed %s -> %s\n' "$destination" "$source_cli"
