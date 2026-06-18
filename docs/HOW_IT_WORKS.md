# Meeting Truth Layer ("Geppetto") — How It Works

*A plain-language overview for project managers and stakeholders.*

## What it is

Geppetto is a quiet assistant that listens to your meeting and checks what people
say against your project's approved records. When someone states something that
doesn't match the documented truth — "QA is done" when the tracker says 82% — it
privately flags it on your screen, in the moment, so you can decide whether to
speak up.

It works like a fact-checker sitting next to you: it never interrupts the meeting,
and only you see its notes.

## The problem it solves

In status meetings, confident statements often drift from reality — a deadline
that already slipped, a sign-off that never happened, a dependency that's still
open. Today you usually catch these *after* the meeting, when it's too late to
correct course in the room. Geppetto moves that catch to *during* the meeting.

## How it works, step by step

1. **It listens.** With your go-ahead, it captures the meeting audio and turns
   speech into text continuously as people talk.
2. **It spots claims.** It picks out statements that are checkable facts about the
   project — status, percentages, dates, ownership, approvals, decisions — and
   ignores everything else (greetings, opinions, questions, small talk).
3. **It checks each claim.** It compares the claim against your approved knowledge
   base (your trackers, specs, and documents) to see whether they agree.
4. **It alerts you privately.** If something is off, a card appears on your
   dashboard within a few seconds, showing the claim, what the records actually
   say, and a neutral suggestion for how you might respond.
5. **It saves the meeting.** When you end the session, it stores a full report you
   can revisit later, alongside your history of past meetings.

## What you see on screen

A private dashboard with two parts:

- **Live alerts** in the center — they appear as the meeting unfolds, newest first,
  color-coded by how serious the mismatch is.
- **Meeting history** on the side — every past meeting, ready to open or review,
  exactly as before. Ending a live meeting automatically adds it here.

Each alert is color-coded into one of five plain verdicts:

| Verdict | Meaning in plain terms |
|---|---|
| **Verified** | Matches the records — all good. |
| **Contradicted** | Conflicts with the records — worth addressing. |
| **Unverified** | No record either way — can't confirm it. |
| **Outdated** | Was true once, but newer records have moved on. |
| **Needs clarification** | Partly true or ambiguous — worth a quick question. |

Every alert also carries a confidence level (high / medium / low) and points to the
source it checked against, so you can judge how much weight to give it.

## What stays private

The alerts are yours alone — they show only on your local dashboard and are never
shared into the meeting or with other participants. The audio is used to transcribe
in the moment and isn't kept; only the final transcript and report are saved.

## What it doesn't do (today)

It doesn't identify *who* said something, it doesn't join or control your meeting
platform, and it's only as reliable as the records you give it — if a tracker is
stale, its checks will reflect that. It's a prompt for your judgment, not a
replacement for it.

## The short version

Listen → spot the factual claims → check them against your records → quietly tell
you when something doesn't line up, fast enough to act on it — then hand you a saved
report when you're done.
