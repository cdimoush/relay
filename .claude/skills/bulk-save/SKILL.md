---
name: bulk-save
description: Commit and push all dirty agent repos at once
triggers:
  - save everything
  - bulk save
  - save all repos
  - commit everything
  - push everything
  - save all
allowed-tools: Bash, Read
---

# Bulk Save

Commit and push all dirty agent repos in one go.

## How to Run

Execute the bulk-save script:

```bash
/home/ubuntu/relay/scripts/bulk-save.sh
```

If the user provides a commit message, pass it as an argument:

```bash
/home/ubuntu/relay/scripts/bulk-save.sh "user's commit message"
```

If no message is provided, use the default (bulk save + timestamp).

## What It Does

Iterates all 7 repos (aura, clone, cyborg, gtc_wingman, isaac_research, memories, relay):
1. Checks if repo has uncommitted changes
2. If dirty: `git add -A`, `git commit`, `git push`
3. If clean: skips
4. Reports summary: "Saved N/7 repos, M clean"

## Output

Report the script's output directly to the user. The summary line at the end tells them exactly what happened.
