---
name: aura.vision
description: Assess, refine, and crystallize user visions into polished documents
argument-hint: <vision description or empty to process queue>
disable-model-invocation: true
allowed-tools: Bash(ls *), Bash(bd *), Bash(mv *), Read, Write, Glob, Grep, Task
---

# Vision

Take a raw vision — typed or queued — and turn it into a clear, grounded document. The core job is reading user intent: sometimes that means light polish, sometimes it means helping them think.

## Input

Either:
- A vision description (the argument)
- No argument → process next item from `.aura/visions/queue/`

## Queue Intake (no argument)

1. **Check queue:**
   ```bash
   ls -1 .aura/visions/queue/
   ```
   If empty or only `.gitkeep`: report "No visions in queue. Place a `.txt` file in `.aura/visions/queue/` to queue one." and stop.

2. **For each text file**, read it and process through Phases 1-4 below, then move the source:
   ```bash
   mv ".aura/visions/queue/<item>" ".aura/visions/processed/<item>_$(date +%Y%m%d_%H%M%S)"
   ```
   On failure: move to `.aura/visions/failed/` instead.

3. Continue to next item without user confirmation.

## Phase 1: Assess

Read the vision and make a judgment call — where does it sit on the spectrum?

**Lucid** — The user knows what they want. Clear intent, specific enough to act on. Maybe rough around the edges but the signal is strong.
→ Light touch. Polish, don't redesign.

**Loose** — The idea is there but it's scattered, exploratory, or missing key decisions. The user is thinking out loud.
→ Help them think. Develop concepts, surface options, ground it in the codebase.

**Mixed** — Some parts are clear, others need development.
→ Polish what's solid, expand what's vague.

This assessment drives everything: how many beads to create, how much research to do, and what the output looks like. Get this right.

## Phase 2: Create Beads

Create a proportional bead graph. The number of beads should match the actual work — not a formula.

**1 bead** — Lucid vision, just needs polish and write-up.
**2-3 beads** — Mixed clarity, some research needed, maybe codebase scanning.
**4-5 beads** — Loose vision requiring research, concept development, multiple output files, or codebase crawling.

### Create epic:
```bash
bd create --title "Vision: <short name>" --type epic --description "
## Vision
<1-2 sentences: what the user is thinking about>

## Clarity
<Lucid | Loose | Mixed> — <1 sentence why>
" --json
```

### Create tasks as needed:
```bash
bd create --title "<Verb phrase>" --type task --parent <epic-id> --description "
## Goal
<What this accomplishes>

## Details
<Enough context to act on>
" --json
```

Common task shapes:
- "Research <topic> in codebase" — scan files, understand patterns
- "Develop concept options for <decision>" — explore alternatives
- "Polish and structure vision" — clean up text, organize
- "Write vision document" — final output (always the last task)
- "Augment with codebase references" — add file links and pointers

Set dependencies only where genuinely needed. The "Write vision document" task usually depends on everything else.

## Phase 3: Execute

Work through tasks in order. For each:

1. **Claim:** `bd update <task-id> --status in_progress`
2. **Do the work:**
   - **Research tasks:** Scan codebase with Glob/Grep/Read. Note relevant files, patterns, constraints. Keep findings concise.
   - **Concept tasks:** Develop 2-3 distinct options. For each: name it, describe the approach, note tradeoffs. Don't pick a winner unless the user asked for a single proposal.
   - **Polish tasks:** Fix typos, tighten language. Preserve the user's voice — don't rewrite their ideas into corporate speak.
   - **Augment tasks:** Add `path/to/file.py:L42` references where they help ground the vision in the actual codebase.
3. **Close:** `bd close <task-id>`

### Writing the Vision Document

The final task always produces the output. Create the directory and write:

```bash
# Directory name: kebab-case, descriptive, max 50 chars
mkdir -p .aura/visions/<clever-name>
```

Write `.aura/visions/<clever-name>/vision.md` with this structure:

**For lucid visions** (polished):
```markdown
# <Title>

<The user's vision, cleaned up. Typos fixed, stuttering removed, language tightened. Their words, their ideas, just clearer.>

## Codebase Context

- `path/to/relevant.py:L10-L30` — <why this matters>
- `path/to/pattern.py:L55` — <existing pattern to follow>

## Next Steps

<1-3 concrete actions if obvious, or omit if the vision speaks for itself>
```

**For loose visions** (developed):
```markdown
# <Title>

## Vision

<Distilled version of what the user is reaching toward — their intent, clarified>

## Options

### Option A: <Name>
<Description, approach, tradeoffs>

### Option B: <Name>
<Description, approach, tradeoffs>

### Option C: <Name> (if warranted)
<Description, approach, tradeoffs>

## Codebase Context

- `path/to/relevant.py:L10-L30` — <why this matters>

## Open Questions

- <Decisions the user still needs to make>
```

**For mixed visions**: Blend both — polish the clear parts inline, develop options for the vague parts.

If the user requested additional files beyond vision.md (diagrams, configs, drafts), write those in the same directory.

## Phase 4: Wrap Up

1. **Close epic:**
   ```bash
   bd close <epic-id> --reason "Vision documented"
   ```

2. **Report:**
   ```
   ## Vision Complete

   **Document:** .aura/visions/<clever-name>/vision.md
   **Clarity:** <Lucid | Loose | Mixed>
   **Epic:** <epic-id>

   <1-2 sentence summary of what was produced>
   ```

## Working With Intent

The point of this skill is learning to read what the user actually wants — not just what they said. A rambling voice memo about "maybe we should think about caching" might be a clear request for a caching layer (lucid) or an invitation to explore performance options (loose). Context matters. When in doubt, lean toward developing options rather than committing to one path — it's easier for the user to pick from good options than to undo a premature decision.
