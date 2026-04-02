---
name: blueprint
description: Promote a concept to an actionable blueprint with implementation plan and sub-tasks
argument-hint: <bead-id>
triggers:
  - plan out
  - break down
  - scope
  - scope this
  - architect
  - map out
  - structure
  - flesh out
  - make actionable
  - plan the implementation
  - break into tasks
  - how do we build
  - turn into tasks
  - ready to build
allowed-tools: Bash, Read, Glob, Grep
---

# Blueprint

Promote a concept bead into an actionable blueprint — an epic with 2-6 sub-tasks that can be directly executed.

## Input

A bead ID (e.g., `relay-abc`). Should be a bead labeled `concept`. If no ID is given, check `bd list --labels=concept` and ask the user which one.

## Process

### Step 1: Read the concept

```bash
bd show <bead-id>
```

Understand the idea, the design thinking, and any trade-offs noted.

### Step 2: Explore the codebase

Read relevant files to understand what needs to change. Identify:
- Which files will be modified or created
- Dependencies between changes
- Risk areas (things that could break)

### Step 3: Promote to blueprint

1. **Convert to epic and swap label:**
   ```bash
   bd update <bead-id> --type=epic --labels=blueprint
   ```

2. **Write the implementation plan** into the design field:
   ```bash
   bd update <bead-id> --design="<implementation plan with approach, file changes, and acceptance criteria>"
   ```

3. **Tag with target repository** so auto-build can find eligible blueprints:
   ```bash
   bd update <bead-id> --metadata '{"target_repo": "<repo_name>"}'
   ```
   Known repos: `relay`, `memories`, `clone`, `cyborg`, `isaac_research`, `aura`, `gtc_wingman`.
   - If all tasks target a single repo, set `target_repo` to that repo name (string).
   - If tasks span multiple repos, set `target_repo` to a list: `["relay", "memories"]`.
   - Auto-build only picks up blueprints with a single `target_repo`. Multi-repo blueprints require manual work.

### Step 4: Create sub-tasks

Create 2-6 child tasks under the epic. Each task should be independently implementable and have clear scope.

```bash
bd create \
  --title="<task title>" \
  --type=task \
  --priority=<priority> \
  --parent=<bead-id> \
  --description="<what to do, which files to touch, expected outcome>"
```

Wire up dependencies between tasks:
```bash
bd dep add <downstream-task> <upstream-task>
```

### Step 5: Report

Show the user the blueprint structure:
- Epic title + implementation approach (brief)
- Task list with dependency order
- Total estimated scope

## Rules

- **2-6 sub-tasks.** If you need more, the scope is too big — split into multiple blueprints.
- **Each task must be concrete.** "Refactor stuff" is not a task. "Extract voice config into VoiceConfig dataclass in config.py" is.
- **Wire dependencies.** If task B needs task A done first, add the dep.
- **Don't implement.** This skill plans, it doesn't write code. That's `/build`.
- **Keep the design field updated.** The design is the source of truth for the implementation approach.
- **No markdown files.** Everything lives in beads.
- **Always tag target_repo.** Every blueprint must have `target_repo` metadata. This enables auto-build to pick it up for nightly autonomous building.
