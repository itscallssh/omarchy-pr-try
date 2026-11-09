# omarchy-pr-try

Test Omarchy PRs safely on your own machine: browse open PRs, scan them for risk, try one, and go back cleanly.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/mit)

## What it does

Omarchy PRs touch system config, services and shell plugins — testing one blindly can break your machine. `omarchy-pr-try` makes it safe:

- **Browse** open PRs from the Omarchy repo with check status and base branch
- **Risk-scan** the added lines for dangerous patterns (sudo, eval, pipes-to-shell, SSH keys, system file edits, QML process spawning, etc.)
- **Try** a PR: snapshot the system, back up your config, switch your machine to the PR's code, run syntax checks and the repo's own tests in a sandbox
- **Comment** a "tested, works for me" note to the author when it's good
- **Go back**: restore your config, unlink the PR, report what happened

## Installation

```bash
# Put it somewhere in your PATH
mkdir -p ~/.local/bin
cp omarchy-pr-try ~/.local/bin/
chmod +x ~/.local/bin/omarchy-pr-try
omarchy-pr-try install   # adds a desktop launcher and shows a keybinding
```

Then launch from your app launcher, or run `omarchy-pr-try` in a terminal.

## Usage

```
omarchy-pr-try                 menu: browse PRs, then risk / checks / try
omarchy-pr-try list            print open PRs
omarchy-pr-try risk N          risk summary for PR N (remembers the commit you reviewed)
omarchy-pr-try test N          run checks on PR N (syntax, plus the repo's tests in a sandbox)
omarchy-pr-try try N [SHA]     switch this machine to PR N (SHA pins the exact commit)
omarchy-pr-try back            undo, then log how it went
omarchy-pr-try status          show which PR is active
omarchy-pr-try verify          check which PR is active and whether it is really being used
omarchy-pr-try history         PRs you have tried
omarchy-pr-try comment N [text] [good]   comment to the PR's author
omarchy-pr-try clean           delete the downloaded PR code and saved copies
omarchy-pr-try doctor          check that everything this tool needs is there
omarchy-pr-try install         add a launcher entry and show a keybinding
```

## Prerequisites

- Omarchy 4 (Quattro) — needs `omarchy-dev-link` / `omarchy-dev-unlink`
- `omarchy-snapshot` (works with the Limine bootloader; GRUB/systemd-boot have none)
- `gum`, `gh` (or `curl` + `jq`), `git`, `flock`
- `bubblewrap` (for running the repo's tests in a sandbox)

Check with `omarchy-pr-try doctor`.

## Safety features

- System snapshot taken before every `try` — the only way back for `/etc` and packages
- Config backup of `.config/omarchy`, `.config/hypr`, `.local/state/omarchy` (and folders the PR's own `config/` changes)
- If the PR is force-pushed since you reviewed it, you are warned and must confirm before testing the new code
- HIGH-risk PRs are a hard stop unless you type the PR number
- A `gh` rate limit / no GitHub credentials falls back to the public API

## Environment variables

```
OMARCHY_PR_REPO, OMARCHY_PR_REMOTE   repo to use (default omacom/omarchy)
OMARCHY_PR_DIR, OMARCHY_PR_STATE     downloads and state location
OMARCHY_PR_YES=1                     answer yes to ordinary prompts
OMARCHY_PR_ALLOW_HIGH=1              let a HIGH-risk PR through without typing its number
OMARCHY_PR_ALLOW_NO_SNAPSHOT=1       skip the snapshot requirement (accept the risk)
OMARCHY_PR_BACKUP_EXTRA="a b"        more paths (relative to $HOME) to back up
OMARCHY_PR_BACKUP_MAX_MB=500         refuse to back up more than this
OMARCHY_PR_TEST_NET=1                allow network inside the test sandbox
OMARCHY_PR_UNSAFE_TEST=1             run repo tests without a sandbox (asks first)
```

## Important: testing visual changes

When you `try` a PR, new terminal shells immediately pick up the PR's code
(`/etc/omarchy.conf` is updated by `omarchy-dev-link`). However, your running
graphical session (Hyprland/Quickshell) loads `OMARCHY_PATH` at session start and
does **not** hot-reload it. Dock UI, themes and QML plugins therefore only appear
after a **session restart** (log out / reboot). The tool's reboot prompt explains this
explicitly; a fresh terminal already shows the PR in use:

```bash
command -v omarchy-dock   # should resolve to the checkout's omarchy-dock
```

Use `omarchy-pr-try verify` to check active-PR state at any time.

## Changelog

### v0.3.2 (2026-11-09)

- Force-push detection now runs **before** the risk scan (warns and requires confirmation if the PR changed since review)
- Menu `try` failures are no longer silently swallowed
- New verification block after linking: shows where the PR is and how to verify it
- New `verify` subcommand and menu item
- Explained graphical-session reload requirement in the reboot flow
- Install hint when the tool is run from an obscure path
- Better `back` output (points to where downloaded PR code lives)
- Full test suite (162 tests) passes

## License

MIT — see [LICENSE](LICENSE).
