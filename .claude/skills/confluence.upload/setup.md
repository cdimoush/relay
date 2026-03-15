# Confluence Upload — First-Time Setup

## Dependencies

Two tools are required: `mark` (markdown-to-Confluence CLI) and `google-chrome` (headless Chrome for mermaid diagram rendering).

### 1. Install mark CLI (v15.3.0)

Pre-built Go binary from GitHub releases. No Go toolchain needed.

```bash
curl -L -o /tmp/mark.tar.gz \
  https://github.com/kovetskiy/mark/releases/download/v15.3.0/mark_Linux_x86_64.tar.gz
tar -xzf /tmp/mark.tar.gz -C /tmp/
cp /tmp/mark /usr/local/bin/mark && chmod +x /usr/local/bin/mark
rm /tmp/mark.tar.gz /tmp/mark
mark --version
# Expected: mark version 15.3.0@...
```

### 2. Install Google Chrome (for mermaid rendering)

Mark uses headless Chrome to render mermaid code blocks to PNG. Without it, mark panics on any file containing mermaid diagrams.

```bash
curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb
apt-get update -qq && apt-get install -y -qq /tmp/chrome.deb
rm /tmp/chrome.deb
which google-chrome
# Expected: /usr/bin/google-chrome
```

### 3. Confluence API Token

Create a personal API token at: https://id.atlassian.com/manage-profile/security/api-tokens

This token is used as the password for mark's HTTP Basic auth against Confluence Cloud. Store it in an environment variable — never commit it.

## Verification

```bash
mark --version          # mark is installed
which google-chrome     # Chrome is installed for mermaid
```

## Notes

- These dependencies are ephemeral to the container. If the container is rebuilt, re-run this setup.
- Mark source: https://github.com/kovetskiy/mark
- For newer mark releases, check https://github.com/kovetskiy/mark/releases and update the download URL above.
