---
name: cyborg-recall
description: Search the user's Cyborg brain for context and knowledge
argument-hint: <search query>
triggers:
  - remember
  - what did we discuss
  - what was that
  - brain search
  - check brain
  - look up
  - find in brain
  - recall
  - what do you know about
  - cyborg
allowed-tools: Read, Glob, Grep
---

# Cyborg Recall — Cross-Agent Brain Search

Search the user's personal knowledge base (Cyborg brain) and synthesize an answer with citations.

## Step 1: Validate Input

`$ARGUMENTS` contains the search query. If empty, respond:
> What are you looking for in the brain?

## Step 2: Search Brain

Search across multiple dimensions using absolute paths:

1. **Grep content** — search `/home/ubuntu/cyborg/brain/notes/` for query terms in markdown files
2. **Grep tags** — search frontmatter in `/home/ubuntu/cyborg/brain/notes/` for matching tags
3. **Check brain.md** — scan `/home/ubuntu/cyborg/brain/brain.md` for relevant summaries and wiki-links
4. **Check JSONL index** — scan `/home/ubuntu/cyborg/.cyborg/notes.jsonl` for matching titles, summaries, and tags

Cast a wide net — use multiple search terms derived from the query. Try synonyms and related terms.

## Step 3: Read Matching Notes

For the top 1-3 matches, read the full note to understand context. Prioritize:
- Direct keyword matches in content
- Tag matches in frontmatter
- Related notes linked from matches

Do NOT read more than 3 notes — keep it brief.

## Step 4: Synthesize Answer

Compose a concise answer that:
- Directly answers the query
- Cites specific notes: "(from cyborg: <note title>)"
- Mentions related notes the user might want to revisit

## Step 5: Respond

Keep it concise. Format:

```
<Direct answer to the query>

Sources:
- <note title> — <relevant detail>
- <note title> — <relevant detail>

Related: <other notes worth revisiting>
```

If nothing found:
> Nothing in the cyborg brain matching "<query>".

## Rules

- **Read-only.** Never write to `/home/ubuntu/cyborg/` — only the cyborg agent does that.
- Keep searches brief — read 1-3 matching notes max.
- Always cite sources when using brain context.
