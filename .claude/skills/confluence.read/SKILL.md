---
name: confluence.read
description: Read Confluence pages via REST API and display contents
argument-hint: <confluence-url-or-page-id>
allowed-tools: Bash, Read, AskUserQuestion
---

# Read from Confluence

Fetch and display one or more Confluence Cloud pages using the REST API. Accepts a page URL, page ID, or page title search.

## Phase 0: Credential Check

Check if credentials are set:

```bash
[ -n "$MARK_USERNAME" ] && [ -n "$MARK_PASSWORD" ] && [ -n "$MARK_BASE_URL" ] && echo "CREDS_OK" || echo "CREDS_MISSING"
```

If `CREDS_MISSING`, use **AskUserQuestion** to ask for:
- Confluence email (username)
- API token (password) — direct the user to https://id.atlassian.com/manage-profile/security/api-tokens if needed

Then set env vars for this session:

```bash
export MARK_USERNAME="<email>"
export MARK_PASSWORD="<token>"
export MARK_BASE_URL="https://apptronik.atlassian.net/wiki"
```

The base URL is always `https://apptronik.atlassian.net/wiki`. Do not ask for it.

## Phase 1: Resolve Page ID

The user provides one of:

1. **Full URL**: `https://apptronik.atlassian.net/wiki/spaces/TEC/pages/6109298731/Gantry` — extract page ID with regex `/pages/(\d+)`
2. **Bare page ID**: `6109298731` — use directly
3. **Page title**: `Gantry` — search via CQL (see below)

### Title search

If the input is not a URL or numeric ID, search by title:

```python
import json, urllib.request, urllib.parse, base64, os

username = os.environ['MARK_USERNAME']
password = os.environ['MARK_PASSWORD']
base_url = os.environ['MARK_BASE_URL']

auth = base64.b64encode(f"{username}:{password}".encode()).decode()
headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

title = "<user-input>"
cql = urllib.parse.quote(f'space=TEC AND title="{title}"')
req = urllib.request.Request(
    f"{base_url}/rest/api/content/search?cql={cql}&limit=5",
    headers=headers
)
with urllib.request.urlopen(req) as resp:
    data = json.load(resp)

for r in data["results"]:
    print(f'{r["id"]}  {r["title"]}')
```

If multiple results, use **AskUserQuestion** to let the user pick. If exactly one result, use it. If zero results, tell the user and stop.

## Phase 2: Fetch Page

Fetch the page body, metadata, and child pages in one call:

```python
import json, urllib.request, base64, os, re, html

username = os.environ['MARK_USERNAME']
password = os.environ['MARK_PASSWORD']
base_url = os.environ['MARK_BASE_URL']
page_id = "<resolved-page-id>"

auth = base64.b64encode(f"{username}:{password}".encode()).decode()
headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}

# Fetch page with body and children
req = urllib.request.Request(
    f"{base_url}/rest/api/content/{page_id}?expand=body.storage,version,ancestors",
    headers=headers
)
with urllib.request.urlopen(req) as resp:
    data = json.load(resp)

title = data["title"]
version = data["version"]["number"]
body_html = data["body"]["storage"]["value"]
ancestors = " > ".join(a["title"] for a in data.get("ancestors", []))

# Fetch child pages
req2 = urllib.request.Request(
    f"{base_url}/rest/api/content/{page_id}/child/page?limit=50",
    headers=headers
)
with urllib.request.urlopen(req2) as resp2:
    children = json.load(resp2)

print(f"Title: {title}")
print(f"Page ID: {page_id}")
print(f"Version: {version}")
print(f"Location: {ancestors}")
print(f"URL: {base_url.replace('/wiki','')}/wiki/spaces/TEC/pages/{page_id}")
print(f"Children: {children['size']}")
for c in children["results"]:
    print(f"  - {c['title']} (id: {c['id']})")
print()
print("--- Body (storage format HTML) ---")
print(body_html)
```

## Phase 3: Convert to Readable Text

The body comes back as Confluence storage format (XML/HTML). Convert it to readable text for display:

### Conversion rules

Apply these transformations to the storage-format HTML:

1. Strip `<ac:structured-macro>` code blocks — extract the `<![CDATA[...]]>` content and wrap in markdown fenced code blocks
2. Strip `<ac:image>` tags — replace with `[image: {ri:filename}]` placeholder
3. Convert `<h2>` to `## `, `<h3>` to `### `, etc.
4. Convert `<table>` to markdown tables
5. Convert `<code>` to backtick-wrapped inline code
6. Convert `<a href="...">` to markdown links
7. Strip remaining HTML tags
8. Decode HTML entities (`&amp;` -> `&`, `&lt;` -> `<`, etc.)
9. Collapse excessive blank lines

This does NOT need to be a perfect round-trip back to the original markdown. The goal is **readable text** the agent can understand and reason about.

## Phase 4: Display

Present the result to the user:

```
## <Page Title>

**Page ID**: <id> | **Version**: <version> | **Location**: <ancestor chain>
**URL**: <full-url>
**Children**: <count> — <child1>, <child2>, ...

---

<converted body text>
```

## Phase 5: Recursive Read (optional)

If the user asks to read a page "and its children" or "recursively", repeat Phase 2-4 for each child page. Process children sequentially to avoid rate limiting.

## Notes

- The REST API returns Confluence **storage format** (XML-ish HTML), not the original markdown. The conversion in Phase 3 is lossy but readable.
- Mermaid diagrams are stored as PNG attachments (`<ac:image>` tags), not as mermaid source. The original mermaid source is lost after upload.
- Rate limit: Confluence Cloud allows ~100 requests/minute. For bulk reads, add a 0.5s delay between requests.
- Page IDs are numeric and stable. Bookmarking them is reliable.
