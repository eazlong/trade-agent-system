"""Agent Skills Loader — loads SKILL.md files for agent context injection."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default search root: ~/.tradelogx/agents/
_HOME_AGENTS = Path.home() / '.tradelogx' / 'agents'


@dataclass
class SkillMetadata:
    """Parsed frontmatter from a SKILL.md file."""
    name: str = ''
    description: str = ''
    when_to_use: str = ''
    references: list[str] = field(default_factory=list)
    license: str = ''
    # Raw unrecognised fields
    extra: dict[str, Any] = field(default_factory=dict)


class AgentSkillsLoader:
    """
    Loader for agent SKILL.md files.

    Supports progressive loading:
    - list_skills()     → brief summary for selection
    - load_skill()      → full content for context injection
    - build_summary()   → XML block for system prompt
    """

    def __init__(self, search_root: Path | None = None):
        self.search_root: Path = search_root or _HOME_AGENTS
        self.search_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def list_skills(self) -> list[dict[str, Any]]:
        """
        List all available skills with their metadata.

        Returns:
            List of dicts with 'name', 'path', 'description', 'when_to_use',
            'source' (always 'agents'), 'available' (always True for md-based skills).
        """
        result: list[dict[str, Any]] = []

        if not self.search_root.exists():
            return result

        for skill_dir in sorted(self.search_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / 'SKILL.md'
            if not skill_file.exists():
                continue

            meta = self._parse_frontmatter(skill_file.read_text(encoding='utf-8'))
            result.append({
                'name': skill_dir.name,
                'path': str(skill_file),
                'description': meta.description or skill_dir.name,
                'when_to_use': meta.when_to_use,
                'references': meta.references,
                'source': 'agents',
                'available': True,
            })

        return result

    def load_skill(self, name: str) -> str | None:
        """
        Load the full content of a skill by name.

        Args:
            name: Skill directory name.

        Returns:
            Raw SKILL.md content (including frontmatter), or None if not found.
        """
        if not self.search_root.exists():
            return None
        skill_file = self.search_root / name / 'SKILL.md'
        if skill_file.exists():
            return skill_file.read_text(encoding='utf-8')
        return None

    def load_skills_content(self, names: list[str]) -> str:
        """
        Load and format multiple skills for context injection.

        Args:
            names: List of skill names to load.

        Returns:
            Concatenated skills block.
        """
        parts: list[str] = []
        for name in names:
            content = self.load_skill(name)
            if content:
                parts.append(self._format_skill_block(name, content))
        return '\n\n---\n\n'.join(parts)

    def build_summary(self) -> str:
        """
        Build an XML summary of all skills for progressive loading.

        The agent reads this to decide which skills to load for the current task.

        Returns:
            XML-formatted skills inventory.
        """
        skills = self.list_skills()
        if not skills:
            return ''

        def esc(s: str) -> str:
            return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        lines = ['<agent_skills>']
        for s in skills:
            name = esc(s['name'])
            desc = esc(s['description'])
            when = esc(s.get('when_to_use', ''))
            src = s['source']

            lines.append(f'  <skill name="{name}" source="{src}">')
            lines.append(f'    <description>{desc}</description>')
            if when:
                lines.append(f'    <when_to_use>{when}</when_to_use>')
            lines.append('  </skill>')
        lines.append('</agent_skills>')

        return '\n'.join(lines)

    def get_always_skills(self) -> list[str]:
        """
        Skills that should always be injected (based on frontmatter 'always' flag).

        Returns:
            List of skill names marked always=true.
        """
        result: list[str] = []
        for s in self.list_skills():
            content = self.load_skill(s['name'])
            if content:
                meta = self._parse_frontmatter(content)
                # Check for 'always' in extra fields (nanobot / Claude Code skill format)
                if meta.extra.get('always'):
                    result.append(s['name'])
        return result

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _format_skill_block(self, name: str, content: str) -> str:
        """Strip frontmatter and wrap in a named section."""
        body = self._strip_frontmatter(content)
        return f'### Skill: {name}\n\n{body}'

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter block from markdown content."""
        if content.startswith('---'):
            match = re.match(r'^---\n.*?\n---\n', content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content.strip()

    def _parse_frontmatter(self, content: str) -> SkillMetadata:
        """Parse YAML frontmatter into a SkillMetadata dataclass."""
        meta = SkillMetadata()
        if not content.startswith('---'):
            return meta

        match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
        if not match:
            return meta

        for line in match.group(1).split('\n'):
            if ':' not in line:
                continue
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"\'')

            if not value:
                continue

            if key == 'name':
                meta.name = value
            elif key == 'description':
                meta.description = value
            elif key == 'when_to_use':
                meta.when_to_use = value
            elif key == 'references':
                # Multi-line list: lines starting with '  - '
                pass  # Handled below
            elif key == 'license':
                meta.license = value
            else:
                # Collect extra fields for extensibility
                if value.lower() in ('true', 'false'):
                    meta.extra[key] = value.lower() == 'true'
                elif value.isdigit():
                    meta.extra[key] = int(value)
                else:
                    meta.extra[key] = value

        # Parse references list (indented '- ' lines after 'references:')
        ref_section_match = re.search(
            r'^references:\s*\n((?:\s+- .+\n)*)',
            match.group(1),
            re.MULTILINE,
        )
        if ref_section_match:
            for ref_line in ref_section_match.group(1).split('\n'):
                ref = ref_line.strip().lstrip('- ').strip()
                if ref:
                    meta.references.append(ref)

        return meta

    def resolve_references(self, skill_names: list[str]) -> list[str]:
        """
        Resolve skill references (from frontmatter) to full skill names.

        For skills that reference other skills, expand them recursively.

        Args:
            skill_names: Starting skill names.

        Returns:
            Deduplicated list of all referenced skill names.
        """
        seen: set[str] = set()
        stack = list(skill_names)

        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)

            content = self.load_skill(name)
            if content:
                meta = self._parse_frontmatter(content)
                for ref in meta.references:
                    # References can be paths like '.agents/skills/quant-logic-verification'
                    # or just skill names. Extract the skill dir name.
                    ref_path = Path(ref)
                    if ref_path.suffix in ('.md', '.txt'):
                        ref_name = ref_path.stem
                    else:
                        ref_name = ref_path.name
                    if ref_name and ref_name not in seen and self.load_skill(ref_name):
                        stack.append(ref_name)
                        stack.append(ref_name)

        return list(seen)


# Singleton instance
_loader_instance: AgentSkillsLoader | None = None


def get_skills_loader() -> AgentSkillsLoader:
    """Get the singleton AgentSkillsLoader instance."""
    global _loader_instance
    if _loader_instance is None:
        _loader_instance = AgentSkillsLoader()
    return _loader_instance
