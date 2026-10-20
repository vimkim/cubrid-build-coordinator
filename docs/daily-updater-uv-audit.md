# Daily updater Python CLI installation audit

Date: 2026-10-21, Asia/Seoul. Work tracker: 442.

Implementation, isolated verification, approved local integration, and live installation are complete.
The five CLI repositories' main branches were pushed. Dotfiles main contains the two approved local commits.
All 15 declared commands across the six Python distributions passed live checks as regular uv installations.
All six available Python CLI projects already had `pyproject.toml` and a committed `uv.lock`.
No project needed a new uv conversion. Five installers needed regular installation instead of editable installation or a source-environment link.
`cubrid-conf-tui` already used the required command.

The user clarified that `html-serve` became `docserve`.
The old configured checkout was absent, but the renamed checkout exists and is included below.
The dotfiles changes correct that inventory entry, its bootstrap, and its compatibility paths.

## Scope and discovery

The audit follows the live default inventories and all update branches in both launchers.
Discovery used source inspection, `uv tool list`, installed distribution metadata, and the CUBRID updater's read-only modes.
No full daily upkeep run was performed.

| Entry | Source and behavior |
| --- | --- |
| `daily-update` | The deployed launcher imports adjacent `daily_update/cli.py`. Its [project inventory](https://github.com/vimkim/dotfiles/blob/2622fa95747722827b555461c5b7572307d77062/private_dot_config/my-scripts/daily-update.toml) contains seven entries. |
| Project dispatch | [projects.py](https://github.com/vimkim/dotfiles/blob/2622fa95747722827b555461c5b7572307d77062/private_dot_config/my-scripts/daily_update/projects.py) refreshes each checkout, requires its local `justfile`, and runs `just --justfile ./justfile sync` there. |
| `daily-update-cubrid` | [daily-update-cubrid.py](https://github.com/vimkim/my-cubrid/blob/c1702709a3145260b2ec3773caad1ca2d0e1eb2b/daily-update-cubrid.py) defaults to the TUI and coordinator. Its installed copy matched that source byte for byte. It runs each checkout's local sync recipe without refreshing Git. |
| Configurable targets | `daily-update --config` replaces the TOML inventory. Positional directories replace the CUBRID updater's defaults. The audit covers current defaults and the generic dispatch behavior. Unspecified future inventories and directories are outside this snapshot. |

The deployed my-scripts directory is not a Git checkout. `chezmoi source-path` mapped its launcher and inventory to the dotfiles repository.
All updater integration changes were made in that source repository.

## Installation inventory before changes

`DU` means `daily-update`. `DC` means `daily-update-cubrid`.
The links identify the source revisions inspected before implementation.

| Canonical target | Updater | Classification | Original uv management and installer | Disposition |
| --- | --- | --- | --- | --- |
| [my-skills](https://github.com/vimkim/my-skills/blob/f849b070f55930e5abf0165456c5114ba8dd05da/justfile) | DU | Skill collection | No Python distribution. Sync delegates to `scripts/sync_skills.py` and the skills installer. | Preserve the collection workflow. |
| [my-cubrid-skills](https://github.com/vimkim/my-cubrid-skills/blob/62168f4655fde8d9d53331ac00baa195e5ff05f2/justfile) | DU | Skill collection | No Python distribution. `tools/sync-skills.sh` delegates to `tools/sync_skills.py` and the skills installer. | Preserve the collection workflow. The inspected local main was one commit ahead of its tracking ref. |
| [my-git-utils](https://github.com/vimkim/my-git-utils/blob/d90ba08e036ce0869ca06d5ac2f3f4d438944dde/justfile) | DU | Python CLI distribution | `pyproject.toml` and `uv.lock` exist. Sync installs an editable uv tool. | Change sync to a regular reinstall. |
| [herdr-next-to-current](https://github.com/vimkim/herdr-next-to-current/blob/fb1d4c4d65bc4c786ab565ff8f38869a99284686/justfile) | DU | Python CLI distribution | uv-managed. Sync requires primary main and installs an editable tool. | Change installation mode and retain checkout guards. |
| `html-serve`, resolved to [docserve](https://github.com/vimkim/docserve/blob/ae863c7b75d9100082e29987341a1d350c6934cf/justfile) | DU | Renamed Python CLI distribution | The configured `gh/html-serve` path was absent. `gh/docserve` exists, is uv-managed, and uses editable sync and optional checker installations. | Correct the inventory identity and paths. Make both recipes regular installations. Retain command aliases. |
| [dirpick](https://github.com/vimkim/dirpick/blob/01e5cba0f115f45644273ab446c50376d5e0652f/justfile) | DU | Rust CLI | `cargo install --locked --path . --root "$HOME/.local" --bin dirpick`, then a version check. | Preserve Cargo installation. |
| [cubrid-tc-chronicle](https://github.com/vimkim/cubrid-tc-chronicle/blob/603479c54c016c6d370f294749d0f3e7a9a3adad/justfile) | DU | Python CLI distribution | uv-managed. Sync requires primary main and installs an editable tool. | Change installation mode and retain guards and the version check. |
| [cubrid-conf-tui](https://github.com/vimkim/cubrid-conf-tui/blob/ab4c7988bc49a9738d6fb93662097f02afee22f0/justfile) | DC | Python CLI distribution | uv-managed. Sync already runs `uv tool install --reinstall .`, then a help check. | Verify without source changes. |
| [cubrid-build-coordinator](https://github.com/vimkim/cubrid-build-coordinator/blob/9b0d2870636e8bfa970dac08a46f10b80beea8d7/justfile) | DC | Python CLI distribution | uv-managed for development. Sync links the project's `.venv` entry point into a custom command directory. | Replace the custom installer with regular uv reinstall. Preserve the install alias and help check. |

Installed metadata confirmed editable ownership for Git utilities, Herdr, Chronicle, and docserve.
The TUI was a regular uv installation.
The coordinator was absent from `uv tool list`; its live executable linked to the permanent checkout's `.venv/bin/cubrid-build-coordinator`.

| Distribution | Preserved commands | Python requirement |
| --- | --- | --- |
| `my-git-utils` | `git-unsynced`, `git-log`, `git-log-pick`, `git-log-pr`, `gh-pr-info`, `gh-pr-associate` | `>=3.11` |
| `herdr-next-to-current` | `herdr-next-to-current` | `>=3.11` |
| `docserve` | `docserve`, `docserve-check`, `docserve-recent`, `html-serve`, `md-serve` | `>=3.10` |
| `cubrid-tc-chronicle` | `cubrid-tcc` | `>=3.12` |
| `cubrid-conf-tui` | `cubrid-conf-tui` | `>=3.11` |
| `cubrid-build-coordinator` | `cubrid-build-coordinator` | `>=3.10` |

### Other update branches

| Branch | Evidence and disposition |
| --- | --- |
| Dotfiles | [dotfiles.py](https://github.com/vimkim/dotfiles/blob/2622fa95747722827b555461c5b7572307d77062/private_dot_config/my-scripts/daily_update/dotfiles.py) refreshes the configured source and runs `chezmoi-apply-unmodified`. The helper applies managed files and symlinks and excludes run scripts. Keep this deployment workflow. |
| Codex | [tools.py](https://github.com/vimkim/dotfiles/blob/2622fa95747722827b555461c5b7572307d77062/private_dot_config/my-scripts/daily_update/tools.py) uses Homebrew upgrade when the executable and package database establish Homebrew ownership. Otherwise it invokes `codex update`. Keep both paths. |
| Claude Code | The same module resolves the installed executable and invokes `claude update`. Preserve native update behavior. |
| Third-party skills | [skills.py](https://github.com/vimkim/dotfiles/blob/2622fa95747722827b555461c5b7572307d77062/private_dot_config/my-scripts/daily_update/skills.py) invokes `npx --yes skills@1.7.0 update` with an explicit eligible list. Preserve source exclusions and recoverable pruning. |
| Collection installers | Their Python scripts reconcile skill files through `npx skills`; they do not install a project-owned Python CLI. No uv project conversion applies. |
| Updater launchers and dotfile scripts | These remain standalone scripts deployed by their existing owner. Their use of Python does not make them additional Python package installation targets. |

## Resulting behavior

Each affected sync recipe now delegates installation directly to:

```sh
uv tool install --reinstall .
```

docserve's explicit `checker-setup` uses `uv tool install --reinstall '.[check]'` and installs headless Chromium.
Normal docserve sync still installs serving support only. Checker support remains an explicit optional setup.
Its former pre-install legacy-package removal was removed, so a failed new build does not first uninstall the old command.

The user requested simple reinstall recipes without custom receipt or ownership machinery.
uv controls executable conflicts and replacement behavior. No new custom ownership guarantee is claimed.
The coordinator's known legacy links require an explicit `uv tool install --reinstall --force .` migration.
The observed live `.venv` link was migrated this way, followed by a successful ordinary sync.
The [coordinator installation guide](../README.md#requirements-and-installation) documents inspection of those two known legacy targets.

The coordinator retains `just install` as an alias for sync.
`UV_TOOL_BIN_DIR` replaces its former `CUBRID_BUILD_COORDINATOR_BIN_DIR` setting.
The recipes use uv's executable-directory handling. Documentation covers `uv tool dir --bin`, `uv tool update-shell`, and distribution-specific uninstall commands.

Packaging metadata, supported Python versions, dependencies, extras, package data, and command names were preserved.
Development still uses project environments and existing test recipes.
Primary-checkout, dirty-checkout, upstream, origin, and failure-propagation protections remain in place.

Tool installation resolves dependencies separately from the development lockfile.
Git utilities and the TUI retain their declared runtime dependency ranges.
docserve's optional checker retains its Playwright range. No new runtime dependency pinning policy was introduced.
See uv's [tool guide](https://docs.astral.sh/uv/guides/tools/) and [install command reference](https://docs.astral.sh/uv/reference/cli/#uv-tool-install).

## Verification

Installer checks used copied source fixtures with independent primary-main Git metadata.
Every fixture used temporary `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` paths, including paths with spaces.
The user's installed tools were not replaced.

All six Python projects passed these checks using their actual sync recipes:

1. Fresh installation registered the distribution and all declared commands with uv.
2. Repeated sync succeeded.
3. A package source marker changed while the version stayed unchanged. The next sync installed the changed marker.
4. Every declared command answered `--help` outside the checkout.
5. The project `.venv` contained an unusable Python launcher. Installed commands still worked.
6. Moving the source checkout did not break the installed commands or installed package imports.
7. An invalid build backend made sync fail while the previously installed commands and source marker remained usable.

The four formerly editable distributions also passed editable-to-regular conversion through sync.
Installed `direct_url.json` metadata confirmed regular installation.
The coordinator's `just install` alias passed.
Both known coordinator legacy symlinks were preserved when uv rejected the first sync, then migrated with explicit uv `--force`.
Subsequent ordinary sync and command checks passed.

The docserve optional recipe passed in separate temporary tool and browser directories.
After moving that fixture's source checkout, the installed checker rendered a packaged Mermaid diagram in both light and dark themes.
The built wheel contained 194 asset entries, including viewer files, vendor chunks, fonts, and third-party notices.

| Repository or check | Command | Result |
| --- | --- | --- |
| Git utilities | `just test`; `just lint` | 111 tests passed; lint and format checks passed. |
| Herdr | `just check` | Lint and build passed. 54 tests ran with one opt-in live Herdr test skipped. |
| Chronicle | `just check`; `just build` | 99 tests, lint, formatting, and package builds passed. |
| Coordinator | `just test`; `just build` | 51 tests and both package builds passed. |
| docserve | `just test`; `just build` | 95 tests ran with eight optional browser checks skipped initially; both package builds passed. |
| docserve optional checks | `just checker-test` | All 12 checker tests passed after adding the optional dependency to the task project environment. |
| docserve guide | `uv run --locked --extra check docserve-check mermaid docs/guide.md --render --json` | Passed. No eligible Mermaid blocks were found in this guide's illustrative examples. |
| Daily updater | `python3 -B -m unittest discover -s tests -p 'test_daily_update*.py' -v` | 55 tests passed after the inventory rename. Real Git/Just fixtures verify dispatch, checkout guards, and failures. |
| CUBRID updater | `python3 -B -m unittest discover -s tests -p test_daily_update_cubrid.py -v` | Eight tests passed, including default targets, explicit directories, local recipes, and failure propagation. |
| Rename integration | `python3 -B -m unittest discover -s tests -p test_html_serve_migration.py -v` | 11 tests passed for bootstrap protections, compatibility, and migration. |
| Bootstrap syntax | `bash -n` and `shellcheck` on the changed bootstrap | Passed. |
| Documentation and diffs | Markdown source validator and `git diff --check` | Passed for changed human-readable Markdown files and all task diffs. |

The live checker command initially lacked Python Playwright. Validation succeeded through the task project environment and isolated optional-tool installation.
Live docserve received its default serving-only sync. Optional live checker support remains an explicit `just checker-setup` action.
The opt-in live Herdr server test was not run. No GUI programs or full-system upkeep were launched.

Raw original-state evidence, installer harnesses, JSON results, and check logs remain outside Git under:

```text
/mnt/nvme/data-5/tmp/daily-updater-uv-audit-442/
```

Key files are `original-inventory.json`, `docserve-original.json`, `installer-results.json`, `docserve-checker-result.json`, and `task-commits.json`.
`integration-preflight.json`, `integration-*.json`, and `live-installations-final.json` record approved integration and live verification.
`verify_installers.py` reconstructs disposable fixtures from task or merged sources; `verify_docserve_checker.py` verifies optional setup and rendering.
These local logs and fixtures are verification artifacts, not repository source changes.

## Merged commits and former task checkouts

All six task branches were merged into their permanent checkouts' local `main` branches with fast-forward merges.
The five CLI main branches were pushed, and their remote tips were verified.
Dotfiles pushing was outside the approved scope. Its clean local main is two commits ahead of `origin/main`.
The coordinator rebased cleanly onto an intervening documentation commit, `d89a757`.
Its implementation commit is listed separately from the audit commits. The task checkout paths below are historical.

| Repository | Task checkout | Branch | Implementation commit |
| --- | --- | --- | --- |
| my-git-utils | `/home/vimkim/gh/my-git-utils-uv-tool` | `fix/regular-uv-tools` | `b3ca34171e23ca6a368161672c38388c4c5e1f88` |
| herdr-next-to-current | `/home/vimkim/gh/herdr-next-to-current-uv-tool` | `fix/regular-uv-tools` | `26a6de7db266dd04d1ea4a4b3eba829e2512a067` |
| cubrid-tc-chronicle | `/home/vimkim/gh/cubrid-tc-chronicle-uv-tool` | `fix/regular-uv-tools` | `38a45264f517e5c4a9d4bd5b67254b5739b8868a` |
| cubrid-build-coordinator | `/home/vimkim/gh/cubrid-build-coordinator-uv-tool` | `fix/uv-tool-install` | `2735631453b83d89fb84a68fba1fcacc4ad15af7`, followed by the audit and completion records. |
| docserve | `/home/vimkim/gh/docserve-uv-tool` | `fix/regular-uv-tools` | `13a2cd2503660b2bf0b7148936ae93709b4cc8fe` |
| dotfiles | `/home/vimkim/.local/share/chezmoi-uv-tool` | `fix/regular-uv-tools` | `3a5e294` documents regular installs; `d00b3dd1f300fd6470181793f630a6211230869e` follows the rename. |

## Integration and deployment results

After the approved merges, each changed CLI was refreshed from its permanent main checkout.
All declared commands ran outside the source checkout, and installed metadata confirmed regular installation.
The unchanged TUI also passed the final live installation check.

`chezmoi-apply-unmodified` ran once for all managed files and symlinks in the configured source checkout.
It exited successfully with no skipped targets. All three affected target diffs were empty afterward:

| Source | Absolute deployed target | Current state |
| --- | --- | --- |
| `private_dot_config/my-scripts/daily-update.toml` | `/home/vimkim/.config/my-scripts/daily-update.toml` | Verified: inventory selects `~/gh/docserve` and `vimkim/docserve`. |
| `private_dot_config/my-scripts/bin/executable_serve-html.py` | `/home/vimkim/.config/my-scripts/bin/serve-html.py` | Verified: retry path names the docserve checkout. |
| `private_dot_config/my-scripts/share/symlink_serve-html.tmpl` | `/home/vimkim/.config/my-scripts/share/serve-html` | Verified: symlink resolves to the existing docserve assets. |

The changed bootstrap is source-only for this helper deployment. The helper excludes run scripts.
Its legacy filename, command aliases, and backup location remain compatible.
No source checkout is unavailable after resolving the docserve rename.

Overall `chezmoi status` for managed files and symlinks was also empty after deployment.
No helper failures, remaining target differences, or deployment skips remain.
The installed coordinator now belongs to uv's tool environment rather than the source checkout's `.venv`.

All six destination checkouts are clean. Only this task's merged worktrees and branches are removed during completion, without force.
The final coordinator audit commit, push, and cleanup are recorded in tracker 442 and the local integration evidence.
Unrelated `cubrid-conf-tui/docs/incidents/` files were preserved in its permanent checkout.
The pre-existing my-cubrid-skills main commit and unrelated worktrees, including coordinator validation and Herdr design work, were preserved.
Only disposable task environments, caches, and build outputs were discarded with the merged task worktrees.
