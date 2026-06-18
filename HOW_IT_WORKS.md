# Meeting Truth Layer ("Geppetto 3") — How It Works

*A plain-language overview for project managers and stakeholders.*

## What it is

Geppetto 3 is a quiet assistant that listens to your meeting and checks what
people say against your project's approved records — in real time, privately,
on your screen.

When someone states something that doesn't match the documented truth — "QA is
done" when the tracker says 82% — it flags it on your dashboard in seconds, so
you can decide whether to speak up. It also knows when a fact is *outdated*: if
someone quotes an old number that was correct two weeks ago but has since moved,
it tells you what the current number is.

It works like a fact-checker sitting next to you: it never interrupts the meeting,
only you see its notes, and it learns from your project documents automatically.

## The problem it solves

In status meetings, confident statements often drift from reality — a deadline
that already slipped, a sign-off that never happened, a dependency that's still
open. Today you usually catch these *after* the meeting, when it's too late to
correct course in the room. Geppetto moves that catch to *during* the meeting.

## How it works, step by step

1. **Run `start.bat`.** The server starts and your browser opens automatically.
2. **Click "Start live meeting."** Audio capture starts — no separate step needed.
3. **It listens.** It transcribes speech continuously as people talk.
4. **It spots claims.** It picks out checkable facts about the project — status,
   percentages, dates, ownership, approvals, decisions — and ignores everything
   else (greetings, opinions, questions, small talk).
5. **It checks each claim.** It compares the claim against your knowledge base —
   your project documents in the `docs/` folder — to see whether they agree.
6. **It alerts you privately.** If something is off, a card appears on your
   dashboard within a few seconds, showing the claim, what the records actually
   say, and a neutral suggestion for how you might respond.
7. **Click "End meeting."** A structured report and meeting notes are saved
   automatically and added to your meeting history.

## What you see on screen

A private dashboard with three areas:

- **Live alerts** in the center — appear as the meeting unfolds, newest first,
  color-coded by verdict.
- **Meeting history** on the left — every past meeting, click to review alerts
  and transcript.
- **Knowledge Base panel** on the right — your current project facts, a Pending
  queue for PM review, and a manual update form.

Each alert is color-coded into one of five verdicts:

| Verdict | Meaning |
|---|---|
| **Verified** | Matches the records — all good. |
| **Contradicted** | Conflicts with the records — worth addressing. |
| **Outdated** | Was true once, but the records have since moved on. Shows the current value. |
| **Unverified** | No record either way — can't confirm it. |
| **Needs clarification** | Partly true or ambiguous — worth a quick question. |

Every alert also shows a confidence level (High / Medium / Low) and the source
document it checked against.

## How the knowledge base stays current

Drop your project documents into the `docs/` folder (Word, PDF, Markdown, or
plain text). Geppetto reads them automatically on startup and extracts dated
facts — QA percentages, budget figures, release dates, and more. It only
re-processes files that have changed since the last run, so syncs are fast.

You can also hit the **Sync** button in the dashboard at any time to pick up
newly added files without restarting.

At the end of every meeting, Geppetto automatically writes a structured set of
meeting notes into `docs/notes/` — covering current status, key achievements,
upcoming priorities, risks, decisions, and action items. These are picked up on
the next sync.

## The Pending tab — you stay in control

Geppetto never updates the knowledge base without your awareness:

- Facts auto-extracted from your documents appear as **Provisional** — they're
  used immediately but at reduced confidence, and flagged with ⏳ in alerts.
- Facts from meeting notes go into a **Pending queue** and are not used until
  you explicitly accept them.

Review both in the **Pending tab** and Confirm / Accept / Reject each one.
Rejected items are remembered and won't come back on the next sync.

## What stays private

The alerts are yours alone — they show only on your local dashboard and are
never shared into the meeting or with other participants. Everything runs on
your machine; the only external calls are to OpenAI (audio transcription) and
Anthropic (claim analysis).

## What it doesn't do (today)

It doesn't identify *who* said something, it doesn't join or control your
meeting platform, and it's only as reliable as the documents you give it — if
a tracker is stale, its checks will reflect that. It's a prompt for your
judgment, not a replacement for it.

## The short version

Drop your docs in `docs/` → run `start.bat` → click Start → speak →
see real-time alerts → click End → get structured notes. That's it.
