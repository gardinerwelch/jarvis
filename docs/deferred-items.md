# Deferred Items — jarvis

Running backlog of ideas consciously deferred rather than built: surfaced mid-build or in a review, ruled out of scope for the current work, and recorded so they aren't lost. Hand-maintained (flat-file convention; see the `deferred-item-ledger` skill). One entry per idea, newest first. ecosystem-status's Layer 6 reads this file.

Format: `**Date:** **Type:** (build·fix·decision·debt) **Relates to:** **Idea:** **Why deferred:**`

## Index

- [URGENT: bridge listens on every interface with no auth](#urgent-bridge-listens-on-every-interface-with-no-auth) — 2026-10-02

---

## URGENT: bridge listens on every interface with no auth

**Date:** 2026-10-02
**Type:** fix
**Relates to:** `bridge/server.mjs` (`server.listen(PORT)` at line 1248, the misleading "localhost" log at 1250, the Origin-only gate `originAllowed` at 79-91, `corsFor` at ~862-886, writes mode at 98-101/443-476, `bypassPermissions` at 1563-1564) · gabba-command-center widget spec `docs/superpowers/specs/2026-09-26-jarvis-command-center-widget-design.md` (D1: the Mini-hosting decision) · vault runbook `07 Tooling/How-To Library/Mac Mini Transition — M5 Hub + Digi-Station Runbook.md` (step 0 + "After the transition" §2)

**Idea:** Found by two read-only audits on 2026-10-02.
- **The bind.** `server.listen(PORT)` passes no host, so the bridge binds `::`, every network interface, while its log claims localhost.
- **No auth.** The only gate is the browser Origin header, which any non-browser client spoofs trivially. HTTP requests with no Origin are served.
- **Read-only default.** Any device on the LAN can reach `/tts` (spending ElevenLabs/Fish credits), `/stt`, `/media` and `/page`. Spoofing `Origin: http://localhost:5180` on the WebSocket gives the full agent with every MCP server (receipts-status, storage-status).
- **Writes mode** (`JARVIS_ALLOW_WRITES=1` / `npm run start:workspace`) sets `bypassPermissions`, so a LAN device could run shell and file actions on the host.

**Fix:**
1. `server.listen(PORT, process.env.JARVIS_BIND_HOST ?? '127.0.0.1')`, so it's local-only by default. Set the Tailscale IP later for the M5.
2. A shared-secret token required on every HTTP route and the WebSocket upgrade, with the face sending it from env.
3. Correct the log line to print the real bind address.
4. A test proving a no-token request is refused.

**Interim rule:** never run writes mode on a shared network. It wasn't listening at audit time (manual start, not a LaunchAgent).

**Why deferred:** surfaced in the gabba-command-center FounderOS-eval / M5-planning session, which doesn't touch JARVIS code. It's logged here so it isn't lost.
- **Sequencing:** do it **before** the M5 move puts the bridge on an always-on host, and before any Tailscale exposure. It's step 0 of the transition runbook.
- **Independent of the rest of the migration,** so it can land any time, ideally first.
- Prerequisite for the "JARVIS on the M5" step: brain on the M5, face on the laptop over HTTPS.
