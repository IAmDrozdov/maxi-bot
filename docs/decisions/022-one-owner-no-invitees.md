# ADR-022: The bot has one Owner and nobody else

Status: Accepted (2026-10-11). Supersedes ADR-008 decision 3 (the access model: an admin, one-time invites, a whitelist) and its 2026-09-12 note that the admin can `/revoke` a user.

## Context

ADR-008 built the bot for sharing: an admin mints one-time invite links, a redeemed user is whitelisted in
sqlite, and the code keeps two kinds of people apart ever since. Routing has a branch per kind, the daily
spend cap and the voice-test cooldown apply to invitees only, `/status` hides other people's jobs, and the
access middleware carries two caches so a stranger cannot flood sqlite or the reply.

Nobody was ever invited to stay. On 2026-10-11 the production database held one row in `users`, one
unused invite, and one distinct sender across every job. The owner decided that day that the bot is
written for one person: the source is public, and anyone else runs their own instance.

## Decision

1. **One person.** The bot answers the **Owner**, the Telegram account named by `OWNER_TELEGRAM_ID`, and
   nobody else. "Admin", "invitee" and "user" stop being roles. The Mini App already admitted only this
   account (ADR-016); the bot now matches it.
2. **Everyone else gets nothing.** An update from any other account is dropped at the door: no reply, no
   database read, no log above debug level. Private chats only, as before.
3. **Invites are removed, not hidden.** `/invite`, `/users` and `/revoke` go, with the `users` and
   `invites` tables, the whitelist cache and the invite deep link in `/start`. `user_settings` and `jobs`
   stay; they are keyed by a Telegram id and hold the Owner's own history.
4. **Limits that existed for invitees go with them.** The rolling daily spend cap
   (`BOT_DAILY_USER_COST_LIMIT_USD`) and the voice-test cooldown never applied to the admin, so they are
   deleted. The per-job limit (`BOT_MAX_JOB_COST_USD`) applied to everyone and stays.
5. **One word, one setting.** Docs, glossaries and code say Owner. `ADMIN_TELEGRAM_ID` becomes
   `OWNER_TELEGRAM_ID` (`settings.owner_telegram_id`). The bot refuses to start without it, as the Mini App
   server already does: with no Owner there is nobody to answer.
6. **Living docs are rewritten; records are not.** README, `docs/runtime.md`, `docs/verifying.md`,
   `docs/architecture.md`, `AGENTS.md`, both glossaries and the interface and infrastructure READMEs
   describe one Owner. Older ADRs, notes decisions and tickets keep their words; ADR-008 gains a Status
   note pointing here.

## Considered Options

- Docs only, code unchanged. Rejected: `docs/runtime.md` would stay silent about branches that still run.
- Keep the code, document it as unused. Rejected: every new feature would still pay for an "is this the
  admin?" branch that has one answer.
- A one-line reply to strangers naming the repository. Rejected: it keeps the bot answering whoever writes
  to it, and keeps the rate limiter that exists only to make that safe.
- Keep `ADMIN_TELEGRAM_ID` as the variable's name. Rejected: the one setting that names the Owner would be
  the last place that says admin.

## Consequences

- Access is one comparison with no state. The `/start <token>` exception, the only path a stranger could
  take through the middleware, no longer exists.
- Routing loses the "unsupported file type" answer: a document no workflow accepts was a Capture for the
  admin and an error for an invitee, and is now always a Capture.
- A second person is a new decision. Bringing sharing back means a new ADR; ADR-008 decision 3 is the
  record of how it was done before, and a Backup taken before this change still holds the two tables.
- A deploy must carry the renamed variable: `deploy.sh` builds `bot.env` from the local `.env`, so the
  local file is renamed first. A bot started with only the old name stops at once and says why.
- The daily Backup's alert reads the same variable from the local `.env`, so `backup.py` changes in the
  same commit.
