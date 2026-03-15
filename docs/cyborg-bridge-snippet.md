## Cyborg Brain Access (Cross-Agent Memory)

You have read access to the user's personal knowledge base (Cyborg brain) at `/home/ubuntu/cyborg/brain/`. Use this to understand context the user may reference without repeating themselves.

When to check the brain:
- User references something vaguely ("that project we discussed", "the robotics thing")
- You need to understand user preferences or priorities
- A task requires context about the user's other projects or strategic thinking

How to search:
1. Quick scan: `grep -r "<keywords>" /home/ubuntu/cyborg/brain/notes/ --include="*.md" -l`
2. Check index: `grep "<keywords>" /home/ubuntu/cyborg/.cyborg/notes.jsonl`
3. Read overview: `/home/ubuntu/cyborg/brain/brain.md` (table of contents)
4. Read matches: open the full note files for context

Rules:
- Read-only. Never write to cyborg's brain — only cyborg does that.
- Keep searches brief — read 1-3 matching notes max.
- Cite sources when using brain context: "(from cyborg: <note title>)"
