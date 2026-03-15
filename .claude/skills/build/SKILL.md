---
name: build
description: Execute a blueprint by implementing its sub-tasks in dependency order
argument-hint: <bead-id>
triggers:
  - implement
  - execute
  - build it
  - build this
  - do it
  - make it
  - ship it
  - go
  - get to work
  - start building
  - let's build
  - knock it out
  - get it done
  - make it happen
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# Build

Execute a blueprint (epic with sub-tasks) by implementing each task in dependency order.

## Input

A bead ID (e.g., `relay-abc`). Should be an epic labeled `blueprint` with child tasks. If no ID is given, check `bd list --labels=blueprint` and ask the user which one.

## Process

### Step 1: Load the blueprint

```bash
bd show <bead-id>
bd children <bead-id>
```

Read the epic's design field for the implementation approach. Inventory child tasks by status:
- `closed` → skip
- `in_progress` → stale from previous session, reset to `open`
- `open` → ready (subject to dependency checks)

### Step 2: Task loop

For each task in dependency order (check that dependencies are satisfied before starting):

1. **Claim the task:**
   ```bash
   bd update <task-id> --status=in_progress
   ```

2. **Read task details:**
   ```bash
   bd show <task-id>
   ```

3. **Implement.** Write the code, make the changes. Follow existing patterns in the codebase.

4. **Verify.** Run tests, lint, or whatever verification the task specifies:
   ```bash
   .venv/bin/python -m pytest tests/ -x -q
   ```

5. **Commit:**
   ```bash
   git add <files>
   git commit -m "<type>(<scope>): <description> (<task-id>)"
   ```

6. **Close:**
   ```bash
   bd close <task-id> --reason="<what was done>"
   ```

7. **Next task.** Check `bd children <epic-id>` for the next open task with satisfied dependencies.

### Step 3: Epic completion

When all child tasks are closed:

1. Run full verification (tests, lint).
2. Close the epic:
   ```bash
   bd close <epic-id> --reason="All tasks completed"
   ```
3. Report what was done — list tasks with their commit hashes.

## Rules

- **One task at a time.** Claim → implement → verify → commit → close → next.
- **Commit per task.** Each task gets its own commit. Don't batch.
- **Pre-edit snapshots.** Run `git add -A && git commit -m "pre-edit snapshot"` before starting work on relay source files.
- **If a task fails:** Add a comment on the bead describing what went wrong, reset to `open`, and move to the next task. Don't get stuck.
- **If all tasks need a restart:** Use `/safe-restart` at the end, not after each task.
- **Sub-agents for isolation:** For large tasks, consider spawning a sub-agent (Agent tool) to keep the main context clean. But don't over-use — simple tasks should be done inline.
- **Stay focused.** Implement what the task says. Don't scope-creep or refactor adjacent code.
