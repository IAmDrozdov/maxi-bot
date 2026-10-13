---
name: update-readme
description: Refresh README.md and the pictures under docs/images/readme/ from the code drift since the last run - text from the git diff, Mini App pictures re-shot from the demo profile, chat pictures on an Owner-approved shot script. Use when the Owner runs /update-readme or asks to refresh or update the README.
disable-model-invocation: true
---

# Refresh the README and its pictures

`README.md` is the public face of a public repo, and its 17 pictures are taken from demo data, never
from the Owner's. Work only on what `drift.py` names. Manual only: the Owner calls this skill, nothing
triggers it. Needs `node` (global `WebSocket`), Google Chrome, `/usr/bin/sips`, `uv`.

Files beside this one: `map.json` (the stamp and the path map), `drift.py`, `shoot.mjs` + `cdp.mjs`
(the Mini App pictures), `chat-shots.md` (the chat pictures), `the-art-of-small-tools.md` (the
document the chat shoot uploads). The demo data is `tools/miniapp_local.py up --demo`.

## 1. Scope the drift

Run `python3 .claude/skills/update-readme/drift.py`. It lists the commits since the stamp, the README
sections and the pictures whose paths changed (uncommitted work counts; pictures in `pending` are
always listed), and `UNMAPPED`: watched paths no entry claims. Settle each one in `map.json`: add it
to an entry's `paths`, to `ignore`, or, for a new feature, give it a README section (`sections`),
and for a new Mini App screen demo data in `tools/miniapp_local.py`, a scene in `shoot.mjs` and a
`pictures` entry.

**Done when:** nothing is UNMAPPED and the stale sections and pictures are listed.

## 2. Update the text

For each stale section read only its README lines and `git diff <stamp> -- <its paths>`. Edit
surgically; copy commands, variables, defaults and limits from the code, never from memory. Keep the
README's shape: showcase first, UI labels verbatim in Russian, prose in English.

**Done when:** each stale section matches its diff, or is noted "no text change" with the reason.

## 3. Mini App pictures

`node .claude/skills/update-readme/shoot.mjs --only=<scenes>` (no `--only` shoots all twelve). It
starts the demo app, shoots 400x644 at scale 2 into `.local/readme-shots/`, checks no `🧪` is on the
page, and tears everything down. `Read` each PNG: composition, nothing cut off, no `🧪`. A screen that
moved means fixing its scene in `shoot.mjs`; changed data means the seed in `tools/miniapp_local.py`.

**Done when:** the script exited 0 and every stale picture is re-shot, or kept with a reason.

## 4. Chat pictures

Write `.local/readme/shot-script.md` from the template in `chat-shots.md`, with only the stale shots
and the full cost line (read the "before" counts with `infrastructure/droplet.sh sql`). Show the
Owner the path and the case list, then **stop until an explicit "да / ок / запускай"**. Then dispatch
`general-purpose` agents (`model: sonnet`, medium; tools as `chat-shots.md` names them), the first
shot alone as a pilot and the rest three per agent: "Follow `.claude/skills/update-readme/chat-shots.md` for shots N-M of
`.local/readme/shot-script.md`"; each writes to `.local/readme-shots/` and fills its Итог. Read the
pilot's frame before dispatching the rest, and run the cleanup from `chat-shots.md` yourself (or
name an agent for it) after the last shot or on abort. Without approval the shots stay stale.

**Done when:** the shots are re-shot with cleanup confirmed, or listed as pending.

## 5. Privacy review

`Read` every new chat picture against the checklist in `chat-shots.md`. Show the Owner the new chat
pictures; after their yes, copy every new image from `.local/readme-shots/` to `docs/images/readme/`
(Mini App pictures need no extra yes). Add or move the `<img>` blocks in `README.md`. Delete
rejected files and snapshots.

**Done when:** every new image passed.

## 6. Validate and gate

`python3 .claude/skills/update-readme/drift.py check`, then the gate in `CLAUDE.md`: ruff on the
changed Python, then `uv run pre-commit run --all-files > .local/pre-commit.log 2>&1; echo "exit=$?"`.

**Done when:** `OK` and pre-commit exits 0.

## 7. Stamp and report

`python3 .claude/skills/update-readme/drift.py stamp [--pending FILE ...]` with the picture files
still stale (`--pending chat-result.png ...`). `stamp` records HEAD and the uncommitted files the last
report listed (unchanged since), so the next run lists only newer work. No `git add`, commit or push. Report: commits
covered, sections changed or left, pictures re-shot, kept or pending, the shoot's cost, and the files
for the Owner to commit.

**Done when:** the report is delivered.

## Never
- Run without the Owner's call, or commit or push.
- Use `--from-db-dir` or any production data for a picture, or print `.local/miniapp_url.txt`.
- Take a full-page Telegram Web screenshot.
- Tap a paid button the shot script does not name, or run past the script's cost line.
- Leave shoot messages or Items behind, or start the bot locally (409 with production).
