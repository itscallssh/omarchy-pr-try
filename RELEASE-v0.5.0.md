# omarchy-pr-try v0.5.0 — security, recovery and UI overhaul

Not production-ready yet: everything below was verified in a hermetic test tree (fake remote, fake HOME, fake root, real bubblewrap and shfmt), not on a real Omarchy 4 machine. See "Not tested".

## Breaking changes
- Unattended `try` needs a pinned commit: run `risk N` / `inspect N` first, or `try N <sha>`.
- `OMARCHY_PR_ALLOW_HIGH`, `OMARCHY_PR_ALLOW_NO_SNAPSHOT`, `OMARCHY_PR_UNSAFE_TEST`, `OMARCHY_PR_TEST_NET` are ignored (with a warning). Use `~/.config/omarchy-pr-try/policy.conf` (`allow-high = N@<sha>`, mode 600).
- CRITICAL findings block unattended live application completely.
- Sandbox tests no longer fall back to running on the host when isolation cannot be verified.
- Requires `python3` for live mode (the privileged helper).

## Security
Parsed (not sourced) `/etc/omarchy.conf`; narrow root helper with path allowlist, O_NOFOLLOW, staged-file checksums and atomic writes; pinned SHA re-checked before apply; force-push/merge/closure detection; terminal-escape and Unicode-control sanitization of all PR/API text; hardened git transports; validated environment; comments previewed and secret-checked; never approves.

## Recovery
Transaction record (manifest.env/.json, checkpoint, plan, system state, archive); preflight for pacman and `omarchy update` locks, space, bootloader, version; snapshot + verified config backup before any change; whole-transaction rollback on any failure (fixes the partial config-write bug); idempotent, verified `restore`; `verify-recovery`; orphan recovery; offline recovery.

## UI
Dashboard with the Omarchy mark, theme accent, readiness row, cached PR peek; GO BACK first while active; MARK AS TESTED / REPORT PROBLEM recorded locally; NO_COLOR; 40–200 column layouts.

## Tests
496 passed, 0 failed (287 in the pre-existing sections, 209 new across 40 scenarios). ShellCheck `-S warning`: clean. `bash -n`: OK.

## Not tested
Real Omarchy 4 install, real root/sudo, snapper/Limine snapshots, gum interactive TTY, gh against github.com, QEMU VM mode, `--net` with a real network, systemd-run limits.
