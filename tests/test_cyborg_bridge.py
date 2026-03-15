"""Tests for cyborg bridge integration — cross-agent memory via cyborg brain access."""

import json
import os
import subprocess

import pytest

CYBORG_BRAIN = "/home/ubuntu/cyborg/brain"
CYBORG_NOTES = os.path.join(CYBORG_BRAIN, "notes")
CYBORG_INDEX = "/home/ubuntu/cyborg/.cyborg/notes.jsonl"
CYBORG_BRAIN_MD = os.path.join(CYBORG_BRAIN, "brain.md")

RELAY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY_CLAUDE_MD = os.path.join(RELAY_DIR, "CLAUDE.md")
SKILL_DIR = os.path.join(RELAY_DIR, ".claude", "skills", "cyborg-recall")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")


class TestCyborgBrainExists:
    """Verify the cyborg brain directory structure exists and has content."""

    def test_brain_directory_exists(self):
        assert os.path.isdir(CYBORG_BRAIN), f"Brain directory not found: {CYBORG_BRAIN}"

    def test_notes_directory_exists(self):
        assert os.path.isdir(CYBORG_NOTES), f"Notes directory not found: {CYBORG_NOTES}"

    def test_notes_directory_has_notes(self):
        entries = os.listdir(CYBORG_NOTES)
        assert len(entries) > 0, "Notes directory is empty"

    def test_brain_md_exists(self):
        assert os.path.isfile(CYBORG_BRAIN_MD), f"brain.md not found: {CYBORG_BRAIN_MD}"


class TestNotesJsonl:
    """Verify notes.jsonl exists and is valid JSONL."""

    def test_index_exists(self):
        assert os.path.isfile(CYBORG_INDEX), f"notes.jsonl not found: {CYBORG_INDEX}"

    def test_index_is_valid_jsonl(self):
        with open(CYBORG_INDEX, "r") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) > 0, "notes.jsonl is empty"
        for i, line in enumerate(lines):
            try:
                obj = json.loads(line)
                assert isinstance(obj, dict), f"Line {i+1} is not a JSON object"
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i+1} is not valid JSON: {e}")

    def test_index_entries_have_expected_fields(self):
        with open(CYBORG_INDEX, "r") as f:
            first_line = f.readline().strip()
        obj = json.loads(first_line)
        # At minimum, entries should have an id and title
        assert "id" in obj or "title" in obj, (
            f"First entry missing expected fields (id or title): {list(obj.keys())}"
        )


class TestGrepSearch:
    """Test that grep search against brain/notes/ finds results."""

    def test_grep_finds_results_for_common_term(self):
        """Search for a term that should exist in at least one note."""
        result = subprocess.run(
            ["grep", "-r", "-l", "-i", "the", CYBORG_NOTES, "--include=*.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "grep found no results for common term 'the'"
        assert len(result.stdout.strip().split("\n")) > 0, "No matching files found"


class TestClaudeMdSnippet:
    """Verify the CLAUDE.md snippet was added to relay's CLAUDE.md."""

    def test_snippet_exists_in_relay_claude_md(self):
        with open(RELAY_CLAUDE_MD, "r") as f:
            content = f.read()
        assert "Cyborg Brain Access" in content, (
            "Cyborg Brain Access section not found in relay CLAUDE.md"
        )

    def test_snippet_contains_read_only_rule(self):
        with open(RELAY_CLAUDE_MD, "r") as f:
            content = f.read()
        assert "Read-only" in content or "read-only" in content.lower(), (
            "Read-only instruction not found in snippet"
        )

    def test_snippet_contains_never_write_rule(self):
        with open(RELAY_CLAUDE_MD, "r") as f:
            content = f.read()
        assert "Never write" in content or "never write" in content.lower(), (
            "Never-write instruction not found in snippet"
        )

    def test_snippet_contains_cyborg_path(self):
        with open(RELAY_CLAUDE_MD, "r") as f:
            content = f.read()
        assert "/home/ubuntu/cyborg/brain/" in content, (
            "Cyborg brain path not found in snippet"
        )


class TestRecallSkill:
    """Verify the recall skill SKILL.md exists and has required frontmatter."""

    def test_skill_file_exists(self):
        assert os.path.isfile(SKILL_MD), f"SKILL.md not found: {SKILL_MD}"

    def test_skill_has_frontmatter(self):
        with open(SKILL_MD, "r") as f:
            content = f.read()
        assert content.startswith("---"), "SKILL.md does not start with YAML frontmatter"
        # Find closing ---
        second_delimiter = content.index("---", 3)
        assert second_delimiter > 3, "SKILL.md frontmatter not properly closed"

    def test_skill_has_required_fields(self):
        with open(SKILL_MD, "r") as f:
            content = f.read()
        required_fields = ["name:", "description:", "argument-hint:", "triggers:", "allowed-tools:"]
        for field in required_fields:
            assert field in content, f"Required frontmatter field missing: {field}"

    def test_skill_has_name_cyborg_recall(self):
        with open(SKILL_MD, "r") as f:
            content = f.read()
        assert "name: cyborg-recall" in content, "Skill name should be 'cyborg-recall'"

    def test_skill_body_references_cyborg_paths(self):
        with open(SKILL_MD, "r") as f:
            content = f.read()
        assert "/home/ubuntu/cyborg/brain/" in content, (
            "Skill body should reference absolute cyborg brain path"
        )
        assert "/home/ubuntu/cyborg/.cyborg/notes.jsonl" in content, (
            "Skill body should reference notes.jsonl path"
        )
