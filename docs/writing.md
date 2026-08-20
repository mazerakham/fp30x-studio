# Writing rules for this repo

Most of this code was written by an LLM agent working from a conversation with one
person. That origin leaves specific marks. They are cheap to remove once named, and
this file names the ones found so far so they are not reintroduced.

## The unnamed protagonist

Until 2026-08-20 the source referred to a "he" 49 times across 13 files, and never
said who. Examples, all real:

    is honest evidence of what he played
    The thing he leaves running while he plays
    short enough to catch him lifting his hands between pieces

The referent was the person the agent was talking to. That works in a conversation
and fails in a repository, where the reader is a stranger who cannot resolve the
pronoun. The same bug in second person appeared in the research docs, which addressed
that person as "you" 97 times.

**Rule.** Name the role, not the person: the player, the listener, the caller. If a
sentence needs a specific human to make sense, it belongs in a commit message or a
notebook, not in the source.

**Check.**

    git grep -nIw -e he -e his -e him -e she -e her -e you -e your -- '*.py'

Expected output is empty. Second person is fine in `README.md`, which addresses its
reader on purpose.

## Length has to be earned, not budgeted

A one-function module can honestly need three paragraphs: when to call it, what the
arguments mean, what happens afterwards. A ratio of comment to code measures nothing
on its own, and this repo runs about 22% without that being a defect.

The test is per-sentence. A sentence earns its place if a competent stranger would be
worse off without it. Sentences that fail are usually one of:

- restating what the next line of code plainly does
- narrating how the decision was reached, when only the decision matters
- rhetorical symmetry, most often `not X, but Y` or a pair of clauses split on a
  semicolon for cadence

The third is the hardest to notice because it reads well in isolation. At density it
becomes wallpaper, and a reader stops trusting that any given sentence carries
information.

## Commit messages

Median body length in this repo's first twenty commits was 20 lines; the longest was
79. Nobody reads a 79-line commit message. State what changed and the one fact a
reader needs to evaluate whether it was right.

## Research notes are not documentation

`docs/research/` holds briefs commissioned by, addressed to, and quoting one person,
including an adversarial review of that person's own product thesis. They are useful
as a record of how decisions were made. They are not documents to publish, and they
are excluded from the tree in `.gitignore`. Public documents state the result and are
written to someone who was not in the room.
