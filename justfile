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
    uv tool install --reinstall .
    @"$(uv tool dir --bin)/cubrid-build-coordinator" --help >/dev/null
