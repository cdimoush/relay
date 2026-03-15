---
name: concept
description: Capture an idea or design exploration as a single beads concept
triggers:
  - plan
  - think about
  - brainstorm
  - design
  - explore idea
  - what if
  - consider
  - sketch out
  - noodle on
  - how would we
  - how should we
  - could we
  - idea for
  - what about
  - let me think
  - mull over
allowed-tools: Bash, Read, Glob, Grep
---

# Concept

Capture an idea as a single bead labeled `concept`. This is the lightest unit of planning — one bead, no sub-tasks, no implementation plan. Just the idea crystallized.

## Input

Either:
- A topic/idea from the user's message (e.g., "think about adding rate limiting")
- An explicit request to explore something

## Process

1. **Explore if needed.** If the concept relates to existing code, read relevant files to ground the idea in reality. Keep exploration brief — this is thinking, not implementation planning.

2. **Create one bead:**
   ```bash
   bd create \
     --title="<concise concept title>" \
     --type=task \
     --priority=2 \
     --labels=concept \
     --description="<what the idea is and why it matters>" \
     --design="<technical thinking, trade-offs, options considered>"
   ```

3. **Report to user:** Share the concept summary and bead ID. Keep it conversational — this is a thinking exercise, not a spec review.

## Rules

- **One bead only.** Never create sub-tasks or child beads for a concept.
- **No markdown files.** Don't write plans/ docs, design docs, or READMEs.
- **Label is `concept`.** This is what distinguishes it from regular tasks.
- **Keep design brief.** 3-10 sentences. If you need more, the idea is too big — suggest splitting into multiple concepts.
- **Don't over-explore.** Read 1-3 files max. If it needs deep research, say so and let the user decide.
- **Type is `task`**, not epic. It becomes an epic later if promoted to blueprint.
