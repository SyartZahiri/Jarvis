"""Small, dependency-free Markdown → styled segments converter for the Tk transcript.

Supported: fenced code blocks, headings (#–###), bullet and numbered lists, block quotes,
horizontal rules, pipe tables (shown monospaced), inline code, **bold**, *italic* and
[links](url). Anything else is shown as text, so unknown syntax never disappears.
"""
from __future__ import annotations

import re

Segment = tuple[str, tuple[str, ...]]
INLINE = re.compile(r"(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|(?<![\w*])\*[^*\s][^*\n]*\*(?!\w)"
                    r"|\[[^\]\n]+\]\([^)\s]+\))")


def inline(text: str, base: tuple[str, ...] = ()) -> list[Segment]:
    segments: list[Segment] = []
    position = 0
    for match in INLINE.finditer(text):
        if match.start() > position:
            segments.append((text[position:match.start()], base))
        token = match.group(0)
        if token.startswith("`"):
            segments.append((token[1:-1], base + ("code",)))
        elif token.startswith(("**", "__")):
            segments.append((token[2:-2], base + ("bold",)))
        elif token.startswith("*"):
            segments.append((token[1:-1], base + ("italic",)))
        else:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token).groups()
            segments.append((label, base + ("link",)))
            if url != label:
                segments.append((f" ({url})", base + ("link_url",)))
        position = match.end()
    if position < len(text):
        segments.append((text[position:], base))
    return segments


def render(markdown: str) -> list[Segment]:
    segments: list[Segment] = []
    in_code = False
    for line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code:
                language = stripped[3:].strip()
                if language:
                    segments.append((language.upper() + "\n", ("code_lang",)))
            in_code = not in_code
            continue
        if in_code:
            segments.append((line + "\n", ("code_block",)))
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            segments.extend(inline(heading.group(2), (f"h{level}",)))
            segments.append(("\n", (f"h{level}",)))
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            segments.append(("—" * 3 + "\n", ("rule",)))
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|", stripped):
                continue  # Table separator row.
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            segments.append(("  ".join(f"{cell:<18}" for cell in cells).rstrip() + "\n",
                             ("table",)))
            continue
        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        numbered = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", line)
        if bullet:
            depth = min(len(bullet.group(1)) // 2, 3)
            segments.append(("   " * depth + "•  ", ("bullet_mark",)))
            segments.extend(inline(bullet.group(2), ("bullet",)))
            segments.append(("\n", ("bullet",)))
            continue
        if numbered:
            depth = min(len(numbered.group(1)) // 2, 3)
            segments.append(("   " * depth + numbered.group(2) + "  ", ("bullet_mark",)))
            segments.extend(inline(numbered.group(3), ("bullet",)))
            segments.append(("\n", ("bullet",)))
            continue
        if stripped.startswith(">"):
            segments.extend(inline(stripped.lstrip("> "), ("quote",)))
            segments.append(("\n", ("quote",)))
            continue
        segments.extend(inline(line))
        segments.append(("\n", ()))
    while segments and segments[-1][0] == "\n":
        segments.pop()
    return segments


def plain(markdown: str) -> str:
    """Text without Markdown syntax, e.g. for speech output."""
    return "".join(text for text, tags in render(markdown) if "code_lang" not in tags)
