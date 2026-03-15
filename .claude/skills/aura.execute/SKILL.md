---
name: aura.execute
description: Implement beads tasks from an epic using sub-agents with review gates
argument-hint: <epic-id>
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Task
---

# Execute Epic

Pick up beads tasks from an epic and implement them in dependency order. Each task runs in a dedicated sub-agent for fresh context, with a separate review sub-agent before commit.

## Input

A beads epic ID (e.g., `perles-abc`). The epic should have child tasks already created by `aura.scope`.

## Phase 1: Setup & Resume Detection

1. **Read epic and scope:**
   ```bash
   bd show <epic-id> --json
   ```
   Read the scope file linked in the epic description. Understand the overall goal, approach, and acceptance criteria.

2. **Inventory task states:**
   ```bash
   bd children <epic-id> --json
   ```
   Categorize each child task by status:
   - `closed` → skip (already done)
   - `in_progress` → stale from a previous session; reset to `open` with audit comment:
     ```bash
     bd comments add <task-id> "Reset from in_progress to open — stale from prior session"
     bd update <task-id> --status open
     ```
   - `open` → ready for work (subject to dependency checks)

3. **Report what remains:**
   ```
   ## Resume Report

   **Epic:** <epic-id> - <title>
   **Already closed:** <count> tasks (skipped)
   **Reset to open:** <count> tasks (were stale in_progress)
   **Remaining:** <count> tasks to implement
   ```

4. **If all tasks closed** → skip to Phase 3 (Epic Completion).

## Phase 2: Sub-Agent Task Loop

For each task in dependency order (use `bd children <epic-id>` each iteration, filtered to `open` status, checking that per-task non-parent dependencies are satisfied):

> **Why not `bd ready`?** Parent-child linking creates implicit blocking — `bd ready` won't surface children while the epic is open. `bd children` + manual filtering gives the orchestrator full visibility into all tasks regardless of epic state.

### Step A: Implementation Sub-Agent

Spawn a **Task sub-agent** (subagent_type: `ticket-dev`) with a prompt containing:

- **Task bead description** — the full Goal, Files, Implementation, and Verification sections from `bd show <task-id>`
- **Epic context** — the approach paragraph from the epic description
- **Relevant Patterns** — the Relevant Patterns section from the scope file (file paths + line ranges that implementation should follow)
- **Instruction**: "Implement this task following the Implementation steps. Run the Verification commands and confirm expected output. Include the full verification command output (stdout/stderr) in your completion report. Do NOT commit — leave changes staged or unstaged for the orchestrator."
- **Budget guardrail**: "If implementation requires reading more than 8-10 files or more than 3 failed verification attempts, stop and report BLOCKED with a summary of what you tried." (This threshold is parametric — defined alongside `MAX_REVISION_ROUNDS` at the top of the skill.)

Each sub-agent gets fresh context (~20-30K tokens) regardless of task position in the sequence. This prevents context degradation on later tasks.

### Step B: Review Sub-Agent

After the implementation sub-agent completes, spawn a **review sub-agent** (subagent_type: `general-purpose`, model: `sonnet`) with:

- **`git diff` output** — the actual code changes (staged + unstaged)
- **Task Goal** — what the task was supposed to accomplish
- **Task Verification** — the expected verification criteria
- **Verification command output** — the stdout/stderr from the implementation sub-agent's verification run

The review sub-agent has NO implementation history — it sees changes with fresh eyes. Using `sonnet` (not `haiku`) because the reviewer must understand codebase patterns and domain context to catch real issues.

**Review instructions:**
- Check: do the changes match the stated Goal?
- Check: would the Verification criteria pass based on the diff?
- Check: no dead code, TODOs, or obvious bugs?
- Check: uses `logging` (not `print`) — use `logger = logging.getLogger(__name__)`
- Check: `ruff check .` and `ruff format --check .` pass
- Return: `APPROVE` or `REVISE` with specific issues listed

**If REVISE** (max `MAX_REVISION_ROUNDS = 1` — named constant at top of skill, parametric):
- Pass the specific issues back to a new implementation sub-agent (1 revision round max)
- Re-run review sub-agent on the updated diff
- If still `REVISE` after max revisions → add a comment on the bead describing unresolved issues, reset task to `open`, and move on:
  ```bash
  bd comments add <task-id> "Review failed after 1 revision: <issues>"
  bd update <task-id> --status open
  ```

### Step C: Commit & Close (Orchestrator)

The orchestrator (this skill) owns git state — sub-agents never commit.

1. **Lint & format** before committing:
   ```bash
   ruff check --fix .
   ruff format .
   ```

2. **Commit:**
   ```bash
   git add <files>
   git commit -m "<type>(<scope>): <description> (<task-id>)"
   ```

2. **Close:**
   ```bash
   bd close <task-id> --reason "<what was done>"
   ```

3. **Next:**
   ```bash
   bd children <epic-id>
   ```
   Filter for next `open` task with satisfied dependencies. Use human-readable output here (no `--json`) to keep the orchestrator loop lean. Reserve `--json` for `bd show <task-id>` when composing sub-agent prompts.

## Phase 3: Epic Completion

1. **Verify all tasks closed:**
   ```bash
   bd children <epic-id>
   ```
   Confirm every child task has `closed` status. If any remain open, report them in the summary.

2. **Run full verification** from the epic's acceptance criteria

3. **Close epic:**
   ```bash
   bd close <epic-id> --reason "All tasks completed"
   ```

4. **Report:**
   ```
   ## Epic Complete

   **Epic:** <epic-id> - <title>

   **Tasks Completed:**
   1. <task-id>: <title> — <commit-hash>
   ...

   **Tasks Skipped (review failures):**
   - <task-id>: <title> — <reason>

   **Epic closed.**
   ```

## Error Handling

- If implementation sub-agent fails: comment on bead, reset to open, continue with next ready task
- If blocked by external dependency: report to user and stop
- If tests fail after revision round: comment on bead, reset to open, move on
- Sub-agents are isolated — one failure does not corrupt the orchestrator's context
