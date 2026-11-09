# Fixes in v0.4.0: the PR now goes into the real system

## What was wrong

`try N` downloaded the PR into `~/.cache/omarchy-pr-try/checkout` (a hidden folder) and called
`omarchy-dev-link`, which only writes `OMARCHY_PATH` into `/etc/omarchy.conf`. On Omarchy 4,
which is a package (`/usr/bin/omarchy-*`, `/usr/share/omarchy`), that variable is read when a
program **starts**. The already-running Hyprland / Quickshell / bar session, and keybindings
started from it, kept running the packaged files, so the PR looked "not set", and it was not
obvious where the PR even was. `verify` also asked the shell it ran in, which has the same
problem, and it checked a command (`omarchy-dock`) that is specific to one PR.

## What changed

1. `try` now writes the PR's **changed files** into the system: `bin/omarchy*` to `/usr/bin`,
   `themes/` and `shell/` to `/usr/share/omarchy`, `config/` to `~/.config`. Everything started
   afterwards uses them; no reboot.
2. Safe by construction: originals are saved first in `/var/lib/omarchy-pr-try` (root-owned),
   a manifest records the checksum of every installed file, files are replaced atomically,
   symlinks/odd names/`/etc` are never written, and a failure rolls everything back.
3. `back` restores the originals. A file that changed since (a package update) is left alone.
4. `plan N` shows the exact mapping and what is not applied (install/, migrations/, etc/,
   default/, applications/) before anything happens.
5. `verify` checks each file's checksum and warns if another copy earlier in `PATH` hides it.
6. `--link` / `OMARCHY_PR_MODE=link` keep the old behaviour; old state files still `back` correctly.
7. Fixed: `back` after a dev-link printed an empty "PR # is now active" banner.

## Limits (honest)

- The mapping comes from how Omarchy 4 is packaged (third-party documentation), and this was
  tested against a stand-in tree and fake commands, not on a real install. Run `plan N` and try
  it on a spare machine or VM first. If a path is wrong it is one small function: `map_path`.
- The Quickshell UI has to be restarted to show QML changes; the tool cannot know the restart
  command, so it lists the `omarchy-restart-*` commands it finds.
- Things owned by the `omarchy-settings` package (etc/, default/, applications/) are listed as
  "not applied": testing those needs a package rebuild.

---

# Earlier fixes (v0.3.2)

## What was wrong

When you picked a PR, the tool did switch the machine (the checkout was linked via
`/etc/omarchy.conf` and your previous test of PR #16219 completed successfully), but
the flow left you unsure whether the PR was really "on your machine and set to see and
test it". The specific problems were:

1. **Force-pushed PRs gave stale risk reports.** `try N` ran the risk scan *before*
   checking whether the PR had been force-pushed/rebased since you reviewed it. If the
   PR moved, you were scanning the old commits.
2. **Errors from `try` were silently swallowed.** In the menu, `"Try this PR"*` used
   `"$SELF" try "$pr" || true`, so if the switch failed you saw nothing but the menu
   again — "picked but not doing anything".
3. **No clear confirmation after linking.** After `omarchy-dev-link` wrote
   `/etc/omarchy.conf`, the tool jumped straight to "Reboot now?" without showing you
   *that* the PR was active, *where* it was checked out, or how to **verify** it in a
   new shell.
4. **The graphical-session reload requirement was not explained.** The running
   Hyprland/quickshell session inherits `OMARCHY_PATH` at session start and does NOT
   re-read `/etc/omarchy.conf`. Dock UI, themes and QML plugins therefore only appear
   after a session restart — you had to reboot just to know that.
5. **No way to check "is the PR really active and in use?"** — no dedicated check.

## What was fixed

### 1. Force-push detection now runs BEFORE the risk scan
`check_reviewed "$pr"` was moved to before `show_pr_header` / `analyze` in `try N`.
If the PR was force-pushed/rebased since you reviewed it, you now see:

```
WARNING: PR #16219 changed since you reviewed it:
  reviewed : c752221c
  current  : a8be54aa
New commits since your review:
  ...
```

and you must explicitly confirm before continuing — so the risk summary you see is
always for the code you are about to try.

### 2. `try` failures in the menu are no longer silent
The menu now shows errors: `! 'try' failed for PR #N; see the errors above.`

### 3. Clear verification block after linking
After `omarchy-dev-link` succeeds, before the reboot prompt you now get:

```
PR #N is now active:
  commit   <sha>  ·  <title>
  base     <branch>
  /etc/omarchy.conf now points at the downloaded PR code
  new terminal shells will use this code immediately
  a running graphical session will NOT hot-reload these changes
  (the dock, themes and QML plugins only appear after a session restart)

  verify in a new shell:
    command -v omarchy-dock
  → should resolve to $CHECKOUT/bin/omarchy-dock
```

### 4. New `verify` subcommand
Run `omarchy-pr-try verify` anytime to check:
- which PR is active (if any)
- where `/etc/omarchy.conf` points
- whether the link points at a real checkout
- what `command -v omarchy-dock` resolves to in a fresh shell

It's also a menu item ("Verify active PR") in both states (PR active / not active).

### 5. Better `back` output
After restoring, it tells you where the downloaded PR code still lives so you can
`omarchy-pr-try clean` it.

### 6. Install hint
If you run the tool from an obscure path, it suggests `omarchy-pr-try install`
(adds a launcher and puts it in your PATH).

### 7. Help text updated
`verify` is documented in the usage block.

## How to test the fixes yourself

The full test suite still passes (162/162):

```bash
./omarchy-pr-try-test          # all green
```

Try the new flow end to end:

```bash
# 1. Run from the tool's dir (you get the install hint)
./omarchy-pr-try
# menu → Browse open PRs → pick a PR → Risk summary
# then back and click "Verify active PR"

# 2. Force-push detection (simulated on your machine):
#    if the PR has been rebased since you reviewed it, try N warns first.

# 3. Verify command (no active PR):
./omarchy-pr-try verify
```

## Important note about testing on your main machine

The PR's code takes effect **immediately in new terminal shells** (bash re-reads
`/etc/omarchy.conf`). Visual changes — the dock UI, themes, QML plugins — require a
**session restart** (log out / reboot), because the running Hyprland compositor does
not hot-reload `OMARCHY_PATH`. The tool's reboot prompt now explains this explicitly,
so you know exactly what needs a restart and what does not.

To confirm a PR is being used without rebooting, open a new terminal and run:
```bash
command -v omarchy-dock
# → should show /home/ssh/.cache/omarchy-pr-try/checkout/bin/omarchy-dock
```

## Summary of behavior (unchanged, for reference)

- `risk N`  — scan the PR's added lines for risky patterns
- `test N`  — syntax checks + repo's own tests inside a bubblewrap sandbox
- `try N`   — snapshot, back up config, switch this machine to the PR, link
- `back`    — restore config, unlink, report verdict
- `comment N [text] [good]` — post a note to the PR author
- `history` — PRs you have tried, with verdicts
