---
name: trade-study
description: Convert a concept into a structured comparison of implementation variants
argument-hint: <bead-id or new idea> [--variants N]
triggers:
  - trade study
  - compare approaches
  - explore options
  - fan out
  - variant study
  - trade off
  - compare implementations
  - what are the ways
  - weigh options
allowed-tools: Bash, Read, Glob, Grep, WebFetch, WebSearch, Agent
---

# Trade Study

Convert a concept bead (or new idea) into a trade study — a parent epic with child beads, each exploring a distinct implementation variant. This is a structured comparison exercise, not a decision or implementation plan.

## Input

Either:
- A bead ID of an existing concept (e.g., `relay-abc`) to convert into a trade study
- A new idea described inline by the user
- Optional: number of variants (default 3). User may say "give me 5 options" or "just 2"
- Optional: specific variant directions the user wants included (e.g., "one should use cron, one should be event-driven")

## Process

### Step 1: Ground the concept

Research before writing. This is not optional.

- **Read code**: 1-5 relevant files in the current repo. Understand what exists.
- **Read other repos**: If the concept spans projects (e.g., cyborg, memories), read relevant files there too.
- **Web search**: If the concept involves external tools, patterns, or prior art, search the web.
- Variants must be grounded in the actual codebase and real constraints. No hand-waving.

### Step 2: Create or convert the parent epic

**From existing concept bead:**
```bash
bd update <bead-id> \
  --type=feature \
  --set-labels=trade-study \
  --description="<rewritten problem statement framing the trade space>" \
  --design="<trade space overview: what we're comparing, key evaluation dimensions, constraints>"
```

**From scratch (new idea):**
```bash
bd create \
  --title="<concise trade study title>" \
  --type=feature \
  --priority=2 \
  --labels=trade-study \
  --description="<problem statement framing the trade space>" \
  --design="<trade space overview: what we're comparing, key evaluation dimensions, constraints>"
```

The description should frame **what problem we're solving and why**.
The design should frame **the dimensions along which variants differ** (e.g., complexity, scope, risk, effort, user impact).

### Step 3: Create variant child beads

Create N child beads (default 3). Each is a distinct implementation approach.

```bash
bd create \
  --title="Variant: <short variant name>" \
  --type=task \
  --priority=2 \
  --parent=<epic-id> \
  --labels=trade-study-variant \
  --description="<1-2 sentence summary of this approach>" \
  --design="<freeform grounded analysis — see Variant Content below>"
```

#### Variant Content (design field)

Freeform but substantive. Each variant should cover:
- **Approach**: What this variant does concretely. Reference specific files, APIs, patterns.
- **How it works**: Key implementation details — enough to evaluate feasibility.
- **Trade-offs**: What you gain and what you give up.
- **Effort sense**: Rough scope — is this a 1-hour change or a multi-day project?
- **What it enables**: Future possibilities this approach opens up.
- **What it limits**: Doors this approach closes or complicates.

Variants should be genuinely different approaches, not minor variations of the same idea. Push for creative diversity — different architectures, different scopes, different philosophies.

### Step 4: Report to user

Show the trade study tree conversationally:
- Epic title and problem framing (brief)
- Each variant with a 1-2 sentence summary
- Invite the user to discuss, refine, or pick a direction

Do NOT recommend a winner. Present the options and let the user drive the decision.

## Rules

- **Default 3 variants.** User can request more or fewer.
- **No winner-picking.** Present options neutrally. The user decides.
- **Ground everything.** Read code and research before writing variants. Reference real files and patterns.
- **Freeform is fine.** Variants don't need identical structure, but each must be substantive.
- **This is NOT a blueprint.** Don't create implementation sub-tasks. That happens after a variant is chosen and promoted via `/blueprint`.
- **Labels matter.** Parent = `trade-study`. Children = `trade-study-variant`. This is how we distinguish from concepts and blueprints.
- **Conversion is destructive.** When converting an existing concept, the old label and design are overwritten. This is intentional — the concept is becoming a trade study.
- **No markdown files.** Everything lives in beads.
- **User can seed variants.** If the user suggests specific directions, include those. Fill remaining slots with agent-generated variants.
- **Be creative.** When generating variants independently, push beyond the obvious. Include at least one unconventional or ambitious approach.
