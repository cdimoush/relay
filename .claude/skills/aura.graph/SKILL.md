---
name: aura.graph
description: Build a multi-phase task graph with tiered epics for long-running agent workflows
argument-hint: <project description or path to draft/plan document>
disable-model-invocation: true
allowed-tools: Bash(ls *), Bash(bd *), Bash(mkdir *), Read, Write, Glob, Grep, Task
---

# Graph: Multi-Phase Task Graph Builder

Build a tiered epic structure for work that spans multiple phases with many deliverables. The output is a beads graph where phase epics are containers, sub-tasks are children, and agent orchestration is encoded in the structure itself.

This skill is domain-agnostic. It works for documentation, code features, research, migrations, or any multi-step project with identifiable phases and deliverables.

## Input

The argument is either:
- A project description (what needs to be accomplished)
- A path to an existing plan, draft, or hierarchy document

## Phase 1: Understand the Work

1. **Read input** — If a file path is provided, read it. If a description, ask clarifying questions.

2. **Identify deliverables** — What are the concrete outputs? (pages, files, modules, endpoints, etc.) List every one. This count determines the size of Phase B.

3. **Identify phases** — Most work follows this pattern:
   - **Phase A: Planning** — Define what to build, review the plan, iterate until approved
   - **Phase B: Execution** — Produce each deliverable with a review gate per deliverable
   - **Phase C: Finalization** — Final QA pass, cross-deliverable consistency, packaging

   Adjust phase count and names to fit the work. Some projects need 2 phases, others need 4+. The pattern is: plan → execute → finalize.

4. **Identify the orchestration model** — For each phase, who does the work?
   - **Phase agent** — A top-level agent that claims the phase epic, dispatches sub-tasks, and arbitrates review loops
   - **Sub-agents** — Agents that pick up individual tasks (write a page, implement a module, etc.)
   - **Review agents** — Agents that review sub-agent output and provide pass/revise verdicts

5. **Write findings** to `.aura/plans/queue/<kebab-case-name>/graph-plan.md`

## Phase 2: Create the Graph

### Structural Rules

These rules prevent common misunderstandings when building multi-epic graphs.

1. **Epics are containers, not pipeline nodes.** An epic's only job is to group children and enforce "can't close until all children close" via `bd epic close-eligible`. Epics should never have upward deps from their children (`child blocks epic` = cycle).

2. **Phase sequencing is epic-to-epic.** If Phase B must wait for Phase A, add `bd dep add <phase-B-epic> <phase-A-epic>`. Do NOT add cross-phase child-to-child deps — this creates redundant gating and undermines the phase container model.

3. **Within-phase sequencing is sibling deps.** A.1 → A.2 → A.3 via `bd dep add`. Write/review pairs: B.write → B.review via `bd dep add`.

4. **Every sub-task uses `--parent`.** This is the most commonly missed step. Without `--parent`, the epic has 0 children and `bd epic close-eligible` doesn't work.

5. **Never create these deps:**
   - Epic depends on its own child (cycle)
   - Child depends on its own parent epic (redundant — parent-child handles this)
   - Child-to-child across phases when epic-to-epic dep exists (redundant gating)

### Create Master Epic

```bash
bd create --title "<Project Name>" --type epic --priority 1 --description "
## Overview
<2-3 sentences: what and why>

## Phases
- Phase A: <name> (<N> tasks)
- Phase B: <name> (<N> tasks)
- Phase C: <name> (<N> tasks)

## Deliverables
<Numbered list of all concrete outputs>
" --json
```

### Create Phase Epics

One epic per phase, parented to master:

```bash
bd create --title "Phase A: <Name>" --type epic --priority 1 \
  --parent <master-epic-id> --description "
## Agent Model
<Who orchestrates this phase? What sub-agents are dispatched? What review loops exist?>

## Sub-tasks
<Bulleted list of child task titles>

## Completion Criteria
<When can this phase close?>
" --json
```

Then wire inter-phase deps:

```bash
bd dep add <phase-B-id> <phase-A-id>
bd dep add <phase-C-id> <phase-B-id>
```

### Create Sub-tasks

#### Phase A Pattern (Planning)

Typically 2-3 tasks in a linear chain:

```bash
bd create --title "A.1: <Define/research step>" --type task --priority 1 \
  --parent <phase-A-id> --description "..." --json
bd create --title "A.2: <Author/draft step>" --type task --priority 1 \
  --parent <phase-A-id> --description "..." --json
bd create --title "A.3: <Review step>" --type task --priority 1 \
  --parent <phase-A-id> --description "..." --json

bd dep add <A.2-id> <A.1-id>
bd dep add <A.3-id> <A.2-id>
```

#### Phase B Pattern (Execution)

Write/review pairs for each deliverable. Use parallel subagents for bulk creation.

For each deliverable, create a PAIR:

```bash
# Write bead
bd create --title "B.<2n-1>: Write <Deliverable>" --type task --priority 2 \
  --parent <phase-B-id> --description "
## Objective
<What to produce>

## Research Files
<Specific files the sub-agent MUST read before writing>

## Style Reference
<Format, length, tone constraints>

## Output
<Exact file path for the deliverable>
" --json

# Review bead
bd create --title "B.<2n>: Review <Deliverable>" --type task --priority 2 \
  --parent <phase-B-id> --description "
## Objective
Review draft of <deliverable> for accuracy, completeness, and style.

## Input
<Path to the draft file>

## Review Criteria
<Numbered checklist — what passes, what fails>

## Output
Verdict: pass | revise: <specific issues>
" --json

# Pair dependency
bd dep add <review-id> <write-id>
```

**Naming convention:** Odd-numbered B tasks are writes, even are reviews. B.1/B.2, B.3/B.4, ..., B.19/B.20. This makes the pairing mechanical and scannable.

#### Phase C Pattern (Finalization)

Usually a single task:

```bash
bd create --title "C.1: <Final pass description>" --type task --priority 2 \
  --parent <phase-C-id> --description "..." --json
```

### Task Description Guidelines

Every task description should be **self-sufficient** — a sub-agent reading only the bead should know exactly what to do. Include:

- **Objective** — What to produce and why
- **Input files** — What to read before starting (specific paths)
- **Output** — Exact file path for the deliverable
- **Constraints** — Length, format, style, boundary rules
- **Research paths** — Codebase files to explore (for code-adjacent work)

Reference shared context documents rather than duplicating content across beads. Store shared context in the project's `.aura/` subdirectory (e.g., `.aura/confluence/1_draft/00-hierarchy.md`).

## Phase 3: Verify

```bash
# Check epic children are registered
bd epic status

# Check no cycles
bd dep cycles

# Check ready work (should be: master epic + Phase A + A.1 only)
bd ready

# Check blocked count (should be: total - ready)
bd blocked | head -3

# Visual verification
bd graph --all
```

**Verification checklist:**
- [ ] Every sub-task has a parent (check `bd epic status` child counts)
- [ ] No dependency cycles (`bd dep cycles`)
- [ ] Only Phase A work is ready (not B or C tasks)
- [ ] Phase epics have inter-phase deps (B blocked by A, C blocked by B)
- [ ] Write/review pairs are dep-linked
- [ ] `bd epic close-eligible` returns nothing (no epic is ready to close yet)

## Phase 4: Report

```
## Graph Complete

**Plan:** .aura/plans/queue/<name>/graph-plan.md
**Master Epic:** <id> - <title>

**Phases:**
- Phase A: <id> (<N> children)
- Phase B: <id> (<N> children)
- Phase C: <id> (<N> children)

**Total:** <N> beads across <L> layers
**Ready:** <list of unblocked bead-ids>

**Agent workflow:**
1. Claim Phase A, complete A.1 → A.2 → A.3, close Phase A
2. Claim Phase B, dispatch B write/review pairs, arbitrate review loops, close Phase B
3. Claim Phase C, dispatch C.1, close Phase C
4. Close master epic
```
