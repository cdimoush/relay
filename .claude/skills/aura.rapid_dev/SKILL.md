---
name: aura.rapid_dev
description: Vision to beads to implementation in a single pass — for lightweight work
argument-hint: <vision description>
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Rapid Dev

Take a vision, do quick research, create a small bead graph, and implement it — all in one pass. For work that doesn't need a full proposal.

## Input

A vision description — what the user wants to build.

## When to Use

- Simple features or chores (3-5 tasks)
- Clear requirements, minimal research needed
- Can be completed in a single session
- When running scope + execute separately is overkill
- Tasks are individually simple — if any task is complex enough to need sub-agent isolation, use scope + execute instead

If complexity grows past 5 tasks, stop and suggest `aura.scope` + `aura.execute` instead.

## Phase 1: Quick Research (5 min)

1. Parse the vision into concrete requirements
2. Scan codebase — find relevant files, patterns, constraints
3. Keep it focused — enough to plan, not a deep dive

## Phase 2: Create Bead Graph

1. **Create epic:**
   ```bash
   bd create --title "<Name>" --type epic --description "
   ## Overview
   <2-3 sentences: what and why>

   ## Approach
   <1 paragraph: how>
   " --json
   ```

2. **Create tasks** (3-5, keep it small):
   ```bash
   bd create --title "<Verb phrase>" --type task --parent <epic-id> --description "
   ## Goal
   <What this accomplishes>

   ## Files
   - path/to/file.py - <what changes>

   ## Implementation
   1. <Step>
   2. <Step>

   ## Verification
   - Run: <command>
   - Expect: <result>
   " --json
   ```

3. **Set dependencies** only where genuinely needed:
   ```bash
   bd dep add <dependent-id> <blocker-id>
   ```

4. **Sanity check** — does the graph make sense? >5 tasks? Suggest full workflow.

## Phase 3: Implement

For each task in dependency order:

1. **Claim:** `bd update <task-id> --status in_progress`
2. **Implement** following the task description
3. **Verify:** run the task's Verification command

> **Guardrail:** Rapid dev implements tasks inline (no sub-agents) for speed. This means context accumulates across tasks. If any single task requires reading more than 3-4 files or produces significant code changes, pause and suggest switching to the full scope + execute workflow.
4. **Lint, format & commit:**
   ```bash
   ruff check --fix .
   ruff format .
   git add <files>
   git commit -m "<type>(<scope>): <description> (<task-id>)"
   bd close <task-id>
   ```
5. **Next:** `bd ready --json` → repeat

## Phase 4: Wrap Up

1. **Close epic:**
   ```bash
   bd close <epic-id> --reason "All tasks completed"
   ```

2. **Report:**
   ```
   ## Rapid Dev Complete

   **Epic:** <epic-id> - <title>
   **Tasks:** <count> completed
   **Commits:** <list>
   ```

## Error Handling

- If a task is harder than expected, comment and ask the user
- If scope creep happens (>5 tasks), pause and recommend full workflow
