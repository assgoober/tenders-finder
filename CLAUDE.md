# CLAUDE.md

**The contract for this repository is [`AGENTS.md`](AGENTS.md). Read it now,
before anything else, and treat it as if its text were here.**

This file is deliberately a pointer and not a copy.

Two agent tools read two different filenames -- Codex reads `AGENTS.md`,
Claude Code reads `CLAUDE.md` -- and the obvious answer is to keep the same
220 lines in both. They were byte-identical for about an hour, and that was
long enough to see the problem: whoever edits one will not always remember
the other, and a behaviour contract that disagrees with itself is worse than
one that is merely out of date, because each tool is confident.

This project has already paid for that lesson twice in a week:

- `HANDOFF.md` and `HANDOFF-NEXT.md` were both handoffs. One said "no
  tenders in the database" while the other said 180 and the database held
  2,751. Both were deleted into one file.
- `AGENTS.md` was created by copying this file and replacing "claude" with
  "Codex" throughout. That rewrote a sentence which was *about* a stale
  model name into `Codex-opus-5`, a model that does not exist -- a blind
  substring replace landing inside a word where it meant something else,
  which is the same defect that put `There are` inside the Hebrew word
  `הקישור` on the landing page the same day.

One file, two doors.

## Everything else

`AGENTS.md` -- the behaviour contract: architecture rules, done-criteria,
model and embedding constraints.

`REPOMIX.md` -- the knowledge base in Hebrew: 26 ADRs and a table of every
silent failure found on this project.

`HANDOFF.md` -- where the work stands and what is blocked.
