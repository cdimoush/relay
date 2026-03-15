---
name: aura.scope
description: Research codebase, produce a scope file, and create beads epic with tasks
argument-hint: <vision description>
disable-model-invocation: true
allowed-tools: Bash(ls *), Bash(bd *), Read, Write, Glob, Grep, Task
---

# Scope Feature

Research the codebase against a user's vision, write a scope file from a template, then create a beads epic with child tasks.

## Input

The argument is a vision description - what the user wants to achieve.

## Phase 1: Research & Scope

1. **Discover templates:**
   ```bash
   ls .claude/templates/
   ```
   Read each template to understand its sections.

2. **Select template** - Choose the best fit. Default to `feature.md` if unsure.

3. **Research codebase** - Explore thoroughly:
   - Existing architecture and patterns
   - Files that will be affected
   - Constraints and dependencies
   - How similar features are implemented
   - Cite specific file paths and line numbers

4. **Populate template** - Fill in every section with research findings. Replace all `<placeholder>` markers.

5. **Write scope file** - Save to `.aura/plans/queue/<kebab-case-name>/scope.md`
   - Generate name from the vision (max 50 chars, lowercase, hyphens)

## Phase 2: Create Beads

### Create the Epic

The epic is the top-level container. Its description should give anyone enough context to understand the work without reading the scope file.

```bash
bd create --title "<Feature/Chore Name>" --type epic --description "
## Overview
<2-3 sentences: what this does and why>

## Scope File
.aura/plans/queue/<name>/scope.md

## Approach
<1 paragraph: key design decisions and implementation strategy>

## Acceptance Criteria
- <Testable criterion from scope>
- <Testable criterion from scope>
" --json
```

### Create Child Tasks (5-7 max)

Each task should be coarse-grained — one meaningful unit of work, not a single function or file change. Use `--parent` to link to the epic.

```bash
bd create --title "<Verb phrase: what this task does>" --type task --parent <epic-id> --description "
## Goal
<1-2 sentences: what this task accomplishes and why it matters for the epic>

## Files
- path/to/file.py - <what changes and why>
- path/to/other.py - <what changes and why>

## Implementation
1. <Concrete step with enough detail to act on>
2. <Concrete step>
3. <Concrete step>

## Verification
- Run: <specific command to test this task>
- Expect: <what success looks like - output, behavior, file state>
- Check: <any regression to watch for>
" --json
```

### Set Dependencies

Only add dependencies where task B genuinely cannot start without task A's output. Don't over-chain — parallel-safe tasks should stay independent.

```bash
bd dep add <dependent-task-id> <blocker-task-id>
```

### Verify Bead Graph

After all `bd create` and `bd dep add` calls, confirm the graph is correct:

```bash
bd children <epic-id>
```

Verify the expected child count matches the number of tasks created.

> **Note:** `bd create --parent` creates implicit blocking — `bd ready` won't surface children while the epic is open. This is expected. The execute skill uses `bd children <epic-id>` with manual status/dependency filtering for task discovery instead of `bd ready`. Alternatively, a cleanup pass using `bd dep remove <child> <epic>` after creation could remove the implicit blocking while preserving the parent link — this needs verification during implementation.

### Task Guidelines

- **5-7 tasks max.** If you need more, the work is too big for one scope — suggest splitting into sub-epics.
- **Verb-phrase titles:** "Add vault storage API", not "Vault storage"
- **Tests in every task:** The Verification section must have a concrete command and expected output. Never "add tests" as a vague requirement.
- **Coarse-grained:** A task should take 15-60 minutes to implement, not 5 minutes.
- **No research-only tasks:** Research is Phase 1. Tasks are implementation.

## Phase 3: Review Tasks

One review pass after beads are created. Check:

- Each task has a concrete Verification section (command + expected output)?
- Dependencies are minimal and acyclic?
- Tasks cover the full scope without gaps?
- No task is trivially small (should be merged) or too large (should be split)?
- Task count is 5-7?

```bash
bd show <epic-id> --json
bd ready --json
```

If issues found, fix with `bd update <task-id> --description "..."` or `bd dep add/remove`.

## Phase 4: Report

```
## Scope Complete

**Scope:** .aura/plans/queue/<name>/scope.md
**Epic:** <epic-id> - <title>

**Tasks:**
1. <task-id>: <title>
2. <task-id>: <title>
...

**Ready:** <list of unblocked task-ids>

Run /aura.execute <epic-id> to implement.
```
