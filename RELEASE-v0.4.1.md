# omarchy-pr-try v0.4.1

Released 2026-11-20 · [compare with v0.4.0](https://github.com/itscallssh/omarchy-pr-try/compare/v0.4.0...v0.4.1)

A small correctness and usability release: `verify` finally reports failure through its
exit status, the menu matches what the docs promised, and the PR card/status line no
longer break on narrow terminals or long PR titles.

## Fixed

- **`verify` always exited 0.** In system mode it ran `sys_check`, printed a warning when
  a file was missing, replaced, or hidden by an earlier `PATH` command, and then returned
  success anyway. It now returns the check result, so a script or wrapper can tell a good
  install from a broken one.
- **`Verify active PR` was missing when no PR was active.** The README and the v0.3.2 notes
  say the item is available in both states; only the active-PR menu had it. It is now in
  both.
- **The PR card and status line broke on narrow terminals.** The card used the terminal
  width minus four without a floor and never truncated long PR titles, so it could overflow
  or ask gum for a negative width. The card width is now clamped (minimum 20 columns) and
  titles are truncated; the active-PR status line truncates its title to the terminal width.
- **An empty risk badge left a trailing space** in the status line.
- **The picker legend did not explain `?`**, the check status shown when the PR list comes
  from the public API without check information.

## Checks

- `bash -n omarchy-pr-try` and `bash -n omarchy-pr-try-test` pass.
- `./omarchy-pr-try-test` → **280 passed, 0 failed** (up from 274).
- New assertions cover `verify`'s exit status (0 when everything is in place, 1 when a
  command is hidden by `PATH`) and the presence of "Verify active PR" in the menu with and
  without an active PR, through a `gum` stand-in.
- Not checked: the TUI was not reviewed on a real screen (no interactive terminal in the
  build environment), so this release does not claim a visual verification of the card
  layout.

## Upgrade

Nothing to migrate: replace `omarchy-pr-try` in your `PATH` (or re-run
`omarchy-pr-try install`) and keep using the same commands. State, backups and history
formats are unchanged.

```bash
omarchy-pr-try install
omarchy-pr-try version   # omarchy-pr-try 0.4.1
omarchy-pr-try doctor
```

## Known limits

- No tag or GitHub Release was published automatically: creating one needs `gh` to be
  authenticated (`gh auth login`) or the Releases page in the browser.
- `verify` now exits non-zero on a real problem, which is a behaviour change for anyone who
  ran it in a script and ignored the output. That is the point of the fix; the printed
  report is unchanged.
