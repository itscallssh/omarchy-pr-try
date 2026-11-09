# omarchy-pr-try

Test Omarchy PRs safely on your own machine: browse open PRs, scan them for risk, try one, and go back cleanly.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/mit)

## What it does

Omarchy PRs touch system config, services and shell plugins — testing one blindly can break your machine. `omarchy-pr-try` makes it safe:

- **Browse** open PRs from the Omarchy repo with check status and base branch
- **Risk-scan** the added lines for dangerous patterns (sudo, eval, pipes-to-shell, SSH keys, system file edits, QML process spawning, etc.)
- **Try** a PR: snapshot the system, back up your config, then **write the PR's changed files into the real system** (`/usr/bin`, `/usr/share/omarchy`, `~/.config`) so the running session uses them at once; syntax checks and the repo's own tests run in a sandbox
- **Comment** a "tested, works for me" note to the author when it's good
- **Go back**: put the original system files and your config back, report what happened

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
omarchy-pr-try plan N          show which system files PR N would replace (changes nothing)
omarchy-pr-try try N [SHA]     put PR N's files into the system (SHA pins the exact commit)
omarchy-pr-try try N --link    old way: only point OMARCHY_PATH at a checkout (needs a reboot)
omarchy-pr-try back            undo, then log how it went
omarchy-pr-try status          show which PR is active
omarchy-pr-try verify          check that the PR's files are in place and that nothing hides them
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
OMARCHY_PR_MODE=system|link          how `try` puts the PR in place (default: system)
OMARCHY_PR_SUDO=doas                 command that gets root for /usr (default: sudo)
OMARCHY_PR_YES=1                     answer yes to ordinary prompts
OMARCHY_PR_ALLOW_HIGH=1              let a HIGH-risk PR through without typing its number
OMARCHY_PR_ALLOW_NO_SNAPSHOT=1       skip the snapshot requirement (accept the risk)
OMARCHY_PR_BACKUP_EXTRA="a b"        more paths (relative to $HOME) to back up
OMARCHY_PR_BACKUP_MAX_MB=500         refuse to back up more than this
OMARCHY_PR_TEST_NET=1                allow network inside the test sandbox
OMARCHY_PR_UNSAFE_TEST=1             run repo tests without a sandbox (asks first)
```

## Where the PR goes (system mode, the default)

Omarchy 4 is a package: its commands are in `/usr/bin` and the rest is in
`/usr/share/omarchy`. `try N` writes the files the PR **changes** over the installed ones:

| PR file | goes to |
|---|---|
| `bin/omarchy*` | `/usr/bin/omarchy*` (and `/usr/share/omarchy/bin/` when that holds a real copy) |
| `themes/`, `shell/` | `/usr/share/omarchy/themes/`, `/usr/share/omarchy/shell/` |
| `config/` | `~/.config/` (the folders are backed up) |

Not applied, and listed under "Not applied" in the plan: `install/`, `migrations/` (they run
at install/update time), `etc/`, `default/`, `applications/` (owned by the
`omarchy-settings` package, these need a package rebuild), symlinks, and anything in
`bin/` that is not named `omarchy-*`.

Run `omarchy-pr-try plan N` first to see exactly which file goes where; it changes nothing.

- Commands start using the PR's files **immediately**, in any terminal and from keybindings. No reboot.
- Scripts and config reload by themselves. The Quickshell UI keeps running the QML it already
  loaded, so restart the shell (the tool lists the `omarchy-restart-*` commands it finds).
- The originals of every replaced file are saved in `/var/lib/omarchy-pr-try` (root-owned,
  with a checksum). `back` restores them. A file that a package update replaced in the
  meantime is **not** overwritten with an old copy; you get a warning instead.
- Do not run `omarchy update` / `pacman -Syu` while a PR is installed.
- `omarchy-pr-try verify` checks every file and warns when something earlier in your `PATH`
  hides the PR's command.
- A PR is applied on top of the Omarchy you have installed. If the PR's base branch is far
  ahead of your installed version, the mix may not work; the plan shows the base branch.

### Why not just a dev-link?

`omarchy-dev-link` only writes `OMARCHY_PATH` into `/etc/omarchy.conf`. Programs read it when they
**start**, so a Hyprland/Quickshell session that is already running keeps using the packaged
files, and the PR seems not to be there. `--link` is still available (`try N --link`), and
`back` still undoes older dev-link tries.

## Changelog

### v0.4.0 (2026-11-09)

- **`try` now installs the PR's files into the real system** (`/usr/bin`, `/usr/share/omarchy`,
  `~/.config`) instead of only setting a dev-link, so a running session uses them at once
- Originals are saved in a root-owned folder with checksums; `back` restores them, and
  leaves alone files a package update replaced meanwhile
- New `plan N` command: exactly which file goes where, what is not applied and why
- `verify` checks each file and detects a command hidden earlier in `PATH`
- Failed or interrupted installs are rolled back; `back` can finish an interrupted one
- `--link` / `OMARCHY_PR_MODE=link` keep the old dev-link behaviour
- `back` no longer prints an empty "PR # is now active" banner after a dev-link
- Search all PRs, cached PR list (from 0.3.3)

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
