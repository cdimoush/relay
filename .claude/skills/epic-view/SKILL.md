---
name: epic-view
description: Format a bead epic for Telegram — show epic header, description, and children in dependency order
triggers:
  - show epic
  - epic view
  - view blueprint
  - show blueprint
  - format epic
allowed-tools: Bash, Read
user-invocable: true
---

# Epic View

Format a bead epic for clean Telegram display. Shows epic header info and children in dependency (topological) order.

## Input

A bead ID (e.g., `1fq`, `relay-1fq`). Passed as the skill argument.

## Process

1. **Fetch the epic as JSON:**
   ```bash
   bd show <id> --json
   ```

2. **Parse and format.** Use Python to:
   - Extract epic: id, title, labels, description
   - Extract children (dependents with `dependency_type: parent-child`)
   - For each child, fetch its own dependents to find inter-child dependencies
   - Topologically sort children by their inter-dependencies (children with no deps first)
   - If no inter-dependencies exist, preserve original order

3. **Output this exact format** (plain text, Telegram-friendly):

   ```
   <b>{epic_id}</b> — {title}
   🏷 {labels}

   {description}

   ── Tasks ──
   {status_icon} <b>{child_id}</b> · {child_title}
   {status_icon} <b>{child_id}</b> · {child_title}
   ...
   ```

   Status icons:
   - `open` → ○
   - `in_progress` → ◐
   - `closed` → ●

4. **Send as a single message.** The output IS the message — don't wrap it in code blocks or add commentary.

## Rules

- Output plain text with minimal HTML bold tags (Telegram supports `<b>`)
- No markdown headers, no code blocks
- Keep it scannable — one line per child task
- If the bead is not an epic or has no children, just show the single bead info
