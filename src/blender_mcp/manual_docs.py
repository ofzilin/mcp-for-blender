"""Offline search over the Blender Manual.

The manual (``manual/**/*.rst``) explains *how* and *why*: what a Geometry Nodes
field is, the order modifiers run in, which editor holds a setting. It is not an
API reference -- for signatures, enum identifiers and operator parameters use the
live ``bpy_api_lookup`` tool instead, which reads the RNA of the Blender that is
actually running and so can never disagree with it.

This module only reads files from a directory on this machine. It makes no
network calls and needs no Blender connection, so it works with Blender closed.

Corpus location, in order:
  1. ``$BLENDER_MCP_MANUAL_DIR``
  2. ``~/.blender_mcp/manual``
  3. ``blender_mcp/data/manual`` inside the installed package

The manual is Blender Documentation Team, CC-BY-SA 4.0. It is not part of this
package's MIT-licensed source: install it separately with

    mcp-for-blender manual --install <path-to-blender-manual/manual>
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field
from difflib import get_close_matches
from pathlib import Path

MANUAL_DIR_ENV = "BLENDER_MCP_MANUAL_DIR"
DEFAULT_MANUAL_DIR = Path.home() / ".blender_mcp" / "manual"

MAX_PAGE_CHARS = 16000
MAX_SEARCH_RESULTS = 10

# ---------------------------------------------------------------------------
# Locating the corpus
# ---------------------------------------------------------------------------


def candidate_manual_dirs() -> list[Path]:
    """Directories that might hold the manual, most specific first."""
    dirs: list[Path] = []
    env = os.environ.get(MANUAL_DIR_ENV)
    if env:
        dirs.append(Path(env).expanduser())
    dirs.append(DEFAULT_MANUAL_DIR)
    dirs.append(Path(__file__).resolve().parent / "data" / "manual")
    return dirs


def find_manual_dir() -> Path | None:
    """First candidate that actually contains manual pages."""
    for directory in candidate_manual_dirs():
        try:
            if (directory / "index.rst").is_file():
                return directory
        except OSError:
            continue
    return None


def missing_corpus_message() -> str:
    looked = "\n".join(f"  - {d}" for d in candidate_manual_dirs())
    return (
        "The Blender Manual is not installed, so the documentation tools have "
        "nothing to search. Looked in:\n"
        f"{looked}\n\n"
        "Install it once with:\n"
        "  mcp-for-blender manual --install <path-to-blender-manual/manual>\n"
        f"or point {MANUAL_DIR_ENV} at a checkout of the manual's 'manual' folder.\n"
        "Everything else works without it. For API signatures, use bpy_api_lookup."
    )


# ---------------------------------------------------------------------------
# RST parsing
# ---------------------------------------------------------------------------

_ADORNMENT_CHARS = set("=-~^\"'`#*+.:_")
_INCLUDE_RE = re.compile(r"^(\s*)\.\.\s+include::\s+(\S+)\s*$")
_DIRECTIVE_RE = re.compile(r"^(\s*)\.\.\s+([A-Za-z][\w-]*)::\s*(.*)$")
_LABEL_RE = re.compile(r"^(\s*)\.\.\s+_[^:]+:\s*$")
_ROLE_RE = re.compile(r":([a-z]+):`([^`]*)`")
_SUBST_RE = re.compile(r"\|([A-Za-z0-9_][A-Za-z0-9_-]*)\|")
_COMMENT_RE = re.compile(r"^(\s*)\.\.(\s|$)")

_ADMONITIONS = {"note", "warning", "tip", "hint", "important", "caution",
                "danger", "attention", "seealso", "error"}
_VERSION_DIRECTIVES = {"versionadded", "versionchanged", "deprecated", "versionremoved"}
_SKIP_DIRECTIVES = {"index", "only", "raw", "meta", "highlight", "contents", "sectnum"}
_OPTION_RE = re.compile(r"^\s*:[a-z-]+:")


def _is_adornment(line: str) -> bool:
    text = line.strip()
    return len(text) >= 3 and text[0] in _ADORNMENT_CHARS and text == text[0] * len(text)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _dedent(lines: list[str]) -> list[str]:
    """Strip the common indentation from a block of lines."""
    indents = [_indent_of(line) for line in lines if line.strip()]
    if not indents:
        return [line.strip() for line in lines]
    cut = min(indents)
    return [line[cut:] if len(line) >= cut else line.lstrip() for line in lines]


def _take_block(lines: list[str], start: int, base_indent: int) -> tuple[list[str], int]:
    """Lines after a directive line, while they are indented deeper than it."""
    block: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            block.append("")
            i += 1
            continue
        if _indent_of(line) <= base_indent:
            break
        block.append(line)
        i += 1
    while block and not block[-1].strip():
        block.pop()
    return block, i


def _strip_options(block: list[str]) -> list[str]:
    """Drop a directive's ``:option:`` lines."""
    while block and _OPTION_RE.match(block[0]):
        block = block[1:]
    return block


def _option_value(block: list[str], name: str) -> str | None:
    for line in block:
        if not line.strip().startswith(f":{name}:"):
            continue
        return line.split(":", 2)[2].strip()
    return None


def _apply_include_slice(lines: list[str], start_after: str | None, end_before: str | None) -> list[str]:
    if start_after:
        for i, line in enumerate(lines):
            if start_after in line:
                lines = lines[i + 1:]
                break
    if end_before:
        for i, line in enumerate(lines):
            if end_before in line:
                lines = lines[:i]
                break
    return lines


def _read_rst(path: Path, root: Path, depth: int = 0) -> list[str]:
    """Read one file, splicing ``.. include::`` targets from the same corpus."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()

    if depth > 4:
        return lines

    out: list[str] = []
    i = 0
    while i < len(lines):
        match = _INCLUDE_RE.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue

        target = match.group(2)
        block, next_i = _take_block(lines, i + 1, len(match.group(1)))
        included_path = (root / target.lstrip("/")) if target.startswith("/") else (path.parent / target)
        try:
            inside = included_path.resolve().is_relative_to(root)
        except (OSError, ValueError):
            inside = False
        if inside and included_path.is_file():
            included = _read_rst(included_path, root, depth + 1)
            included = _apply_include_slice(
                included, _option_value(block, "start-after"), _option_value(block, "end-before"))
            out.extend(included)
        i = next_i
    return out


def _split_headings(lines: list[str]) -> list[tuple[int, str, int, int]]:
    """Find headings. Returns (level, title, first_body_line, next_heading_line)."""
    found: list[tuple[int, str]] = []  # (line index of title, title)
    chars: dict[str, int] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_adornment(line) and i + 2 < len(lines):
            title = lines[i + 1].strip()
            if title and _is_adornment(lines[i + 2]) and lines[i + 2].strip()[0] == line.strip()[0]:
                level = chars.setdefault(line.strip()[0], len(chars))
                found.append((i + 1, title, level))
                i += 3
                continue
        if (
            line.strip()
            and not line.startswith(" ")
            and i + 1 < len(lines)
            and _is_adornment(lines[i + 1])
            and len(lines[i + 1].strip()) >= len(line.strip())
        ):
            level = chars.setdefault(lines[i + 1].strip()[0], len(chars))
            found.append((i, line.strip(), level))
            i += 2
            continue
        i += 1

    sections: list[tuple[int, str, int, int]] = []
    for index, (line_no, title, level) in enumerate(found):
        body_start = line_no + 1
        if body_start < len(lines) and _is_adornment(lines[body_start]):
            body_start += 1
        next_start = len(lines)
        if index + 1 < len(found):
            prev_line = found[index + 1][0]
            # An overline heading starts one line earlier; drop the adornment.
            if prev_line > 0 and _is_adornment(lines[prev_line - 1]):
                prev_line -= 1
            next_start = prev_line
        sections.append((level, title, body_start, next_start))
    return sections


def _clean_inline(text: str) -> str:
    """Turn RST inline markup into something a model reads as plain prose."""

    def role(match: re.Match[str]) -> str:
        label = match.group(2)
        # `Text <target>` keeps the readable text, drops the target.
        angled = re.match(r"^(.*?)\s*<[^>]+>$", label)
        if angled:
            label = angled.group(1) or label
        if match.group(1) == "menuselection":
            label = label.replace("-->", ">").replace("---", ">")
        return label

    text = _ROLE_RE.sub(role, text)
    text = _SUBST_RE.sub(lambda m: m.group(1).replace("_", " "), text)
    text = text.replace("``", "`")
    # Leftover directives that sat inside a table cell or list item.
    text = re.sub(r"\.\.\s+(?:figure|image|video|include|literalinclude)::\s*\S+", "", text)
    return text.rstrip()


def _is_markup_noise(line: str) -> bool:
    stripped = line.strip()
    return (
        not stripped
        or bool(_LABEL_RE.match(line))
        or stripped.startswith(".. |")
    )


_FIELD_TERM_RE = re.compile(r"^(\s*):([A-Za-z][\w ()-]{0,40}):\s+(.*)$")


def _field_term(line: str) -> str | None:
    """``:Term: value`` (a definition list) reads better as ``Term: value``."""
    match = _FIELD_TERM_RE.match(line)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}: {match.group(3)}"


def _clean_block(lines: list[str]) -> list[str]:
    """Clean a directive body: inline markup plus definition-list terms."""
    out: list[str] = []
    for line in lines:
        field = _field_term(line)
        out.append(_clean_inline(field) if field is not None else _clean_inline(line))
    return out


def _render(lines: list[str]) -> str:
    """RST-ish lines to readable Markdown-ish text."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _DIRECTIVE_RE.match(line)
        if match:
            indent, name, argument = len(match.group(1)), match.group(2), match.group(3).strip()
            block, next_i = _take_block(lines, i + 1, indent)

            if name in ("code-block", "sourcecode", "code"):
                lang = argument.split()[0] if argument else ""
                out.append(f"```{lang}")
                out.extend(_dedent(_strip_options(block)))
                out.append("```")
            elif name == "toctree":
                entries = _dedent(_strip_options(block))
                for entry in entries:
                    entry = entry.strip()
                    if entry:
                        out.append(f"- {_clean_inline(entry)}")
            elif name in ("figure", "image", "video"):
                caption = _dedent(_strip_options(block))
                out.extend(x for x in _clean_block([c for c in caption if c.strip()]))
            elif name in ("literalinclude", "include"):
                pass
            elif name in _ADMONITIONS:
                head = _dedent(_strip_options(block))
                if argument:
                    out.append(f"{name.capitalize()}: {_clean_inline(argument)}")
                elif name != "seealso":
                    out.append(f"{name.capitalize()}:")
                out.extend(_clean_block(head))
            elif name in _VERSION_DIRECTIVES:
                out.append(f"({name} {_clean_inline(argument)})")
                out.extend(_clean_block(_dedent(_strip_options(block))))
            elif name in _SKIP_DIRECTIVES:
                pass
            else:
                # Unknown directive: keep its body, drop the marker.
                if argument:
                    out.append(_clean_inline(argument))
                out.extend(_clean_block(_dedent(_strip_options(block))))
            i = next_i
            continue

        if _COMMENT_RE.match(line) and not _LABEL_RE.match(line):
            # A comment: the line plus anything indented under it.
            _, next_i = _take_block(lines, i + 1, _indent_of(line))
            i = next_i
            continue

        if _is_markup_noise(line):
            i += 1
            continue

        field = _field_term(line)
        out.append(_clean_inline(field) if field is not None else _clean_inline(line))
        i += 1

    # Collapse the runs of blank lines the removals leave behind.
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class Section:
    level: int
    title: str
    text: str


@dataclass
class Doc:
    path: str
    title: str
    sections: list[Section] = field(default_factory=list)
    text: str = ""
    length: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    title_counts: dict[str, int] = field(default_factory=dict)

    def section(self, name: str) -> Section | None:
        """A section by title, case-insensitive; a substring match as fallback."""
        wanted = name.strip().lower()
        for section in self.sections:
            if section.title.lower() == wanted:
                return section
        for section in self.sections:
            if wanted in section.title.lower():
                return section
        return None


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _load_doc(path: Path, root: Path) -> Doc | None:
    lines = _read_rst(path, root)
    if not lines:
        return None
    headings = _split_headings(lines)
    if not headings:
        title = path.stem.replace("_", " ").title()
        headings = [(0, title, 0, len(lines))]

    sections: list[Section] = []
    for level, title, start, end in headings:
        body = _render(lines[start:end])
        sections.append(Section(level=level, title=_clean_inline(title), text=body))

    doc_title = sections[0].title if sections else path.stem
    body_text = "\n\n".join(s.text for s in sections)
    tokens = _tokens(body_text)
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    title_counts: dict[str, int] = {}
    for token in _tokens(doc_title):
        title_counts[token] = title_counts.get(token, 0) + 1
    return Doc(
        path=path.relative_to(root).as_posix(),
        title=doc_title,
        sections=sections,
        text=body_text,
        length=len(tokens),
        counts=counts,
        title_counts=title_counts,
    )


class ManualIndex:
    """In-memory index over the manual. Built once, on first use."""

    def __init__(self, root: Path):
        self.root = root
        self.docs: list[Doc] = []
        self._by_path: dict[str, Doc] = {}
        self._doc_freq: dict[str, int] = {}
        self._avg_len = 1.0
        self._load()

    def _load(self) -> None:
        for path in sorted(self.root.rglob("*.rst")):
            doc = _load_doc(path, self.root)
            if doc is not None:
                self.docs.append(doc)
                self._by_path[doc.path] = doc
        for doc in self.docs:
            for term in set(doc.counts) | set(doc.title_counts):
                self._doc_freq[term] = self._doc_freq.get(term, 0) + 1
        total = sum(doc.length for doc in self.docs)
        self._avg_len = (total / len(self.docs)) if self.docs else 1.0

    # -- search ---------------------------------------------------------
    def _idf(self, term: str) -> float:
        df = self._doc_freq.get(term, 0)
        n = len(self.docs)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, doc: Doc, terms: list[str], phrase: str) -> float:
        k1, b = 1.5, 0.75
        norm = k1 * (1 - b + b * doc.length / self._avg_len)
        total = 0.0
        for term in terms:
            tf = doc.counts.get(term, 0)
            if tf:
                total += self._idf(term) * tf * (k1 + 1) / (tf + norm)
            # A term in the page title is worth about three body hits.
            title_tf = doc.title_counts.get(term, 0)
            if title_tf:
                total += 3.0 * self._idf(term) * title_tf
        if phrase:
            if phrase in doc.title.lower():
                total += 6.0
            if phrase in doc.text.lower():
                total += 1.5
        return total

    def search(self, query: str, limit: int = 6) -> list[tuple[float, Doc]]:
        terms = list(dict.fromkeys(_tokens(query)))
        if not terms:
            return []
        phrase = " ".join(query.lower().split())
        scored = []
        for doc in self.docs:
            value = self.score(doc, terms, phrase)
            if value > 0:
                scored.append((value, doc))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[:limit]

    def snippet(self, doc: Doc, terms: list[str], width: int = 260) -> str:
        text = re.sub(r"\s+", " ", doc.text)
        lowered = text.lower()
        for term in terms:
            position = lowered.find(term)
            if position == -1:
                continue
            start = max(0, position - width // 3)
            end = min(len(text), start + width)
            piece = text[start:end].strip()
            return ("..." if start else "") + piece + ("..." if end < len(text) else "")
        return text[:width].strip() + ("..." if len(text) > width else "")

    def resolve_path(self, requested: str) -> Doc | None:
        cleaned = requested.strip().replace("\\", "/").lstrip("/")
        candidates = [cleaned]
        if not cleaned.endswith(".rst"):
            candidates.append(cleaned + ".rst")
        if cleaned.endswith("/") or cleaned == "":
            candidates.append(cleaned + "index.rst")
        candidates.append(cleaned + "/index.rst")
        for candidate in candidates:
            doc = self._by_path.get(candidate)
            if doc is not None:
                return doc
        return None

    def similar_paths(self, requested: str, count: int = 5) -> list[str]:
        return get_close_matches(requested.strip().lstrip("/"), list(self._by_path), n=count, cutoff=0.5)

    def by_title(self, title: str) -> Doc | None:
        wanted = title.strip().lower()
        for doc in self.docs:
            if doc.title.lower() == wanted:
                return doc
        return None

    @property
    def total_pages(self) -> int:
        return len(self.docs)


_INDEX: ManualIndex | None = None
_INDEX_ROOT: Path | None = None
_INDEX_LOCK = threading.Lock()


def get_index() -> ManualIndex | None:
    """The shared index, built on first use. None when no corpus is installed."""
    global _INDEX, _INDEX_ROOT
    root = find_manual_dir()
    if root is None:
        return None
    with _INDEX_LOCK:
        if _INDEX is None or _INDEX_ROOT != root:
            _INDEX = ManualIndex(root)
            _INDEX_ROOT = root
    return _INDEX


# ---------------------------------------------------------------------------
# Tool-facing API (server.py wraps these)
# ---------------------------------------------------------------------------


def search(query: str, limit: int = 6) -> str:
    index = get_index()
    if index is None:
        return missing_corpus_message()
    limit = max(1, min(int(limit or 6), MAX_SEARCH_RESULTS))
    terms = list(dict.fromkeys(_tokens(query)))
    hits = index.search(query, limit)
    if not hits:
        return (
            f"No manual page matches {query!r} ({index.total_pages} pages searched). "
            "Try fewer or more general words, or use bpy_api_lookup for API names."
        )
    lines = [f"{len(hits)} of {index.total_pages} manual pages, best first:"]
    for number, (score, doc) in enumerate(hits, 1):
        lines.append(f"\n{number}. {doc.title}  [{doc.path}]")
        lines.append("   " + index.snippet(doc, terms))
    lines.append("\nRead one with get_manual_page(path).")
    return "\n".join(lines)


def page(path: str, section: str | None = None, max_chars: int = 8000) -> str:
    index = get_index()
    if index is None:
        return missing_corpus_message()
    doc = index.resolve_path(path)
    if doc is None:
        by_title = index.by_title(path)
        if by_title is not None:
            doc = by_title
        else:
            similar = index.similar_paths(path)
            hint = f" Did you mean: {', '.join(similar)}?" if similar else ""
            return f"No manual page at {path!r}.{hint}"

    header = f"# {doc.title}\n[{doc.path}]"
    if section:
        found = doc.section(section)
        if found is None:
            titles = ", ".join(s.title for s in doc.sections[:12])
            return f"{doc.path} has no section {section!r}. Sections: {titles}"
        body = f"## {found.title}\n\n{found.text}"
    else:
        parts: list[str] = []
        intro = doc.sections[0].text if doc.sections else ""
        if intro.strip():
            parts.append(intro)
        for s in doc.sections[1:]:
            heading = "#" * min(2 + s.level, 6)
            parts.append(f"{heading} {s.title}" + (f"\n\n{s.text}" if s.text.strip() else ""))
        body = "\n\n".join(parts)

    text = f"{header}\n\n{body}".strip()
    limit = max(500, min(int(max_chars or 8000), MAX_PAGE_CHARS))
    if len(text) > limit:
        text = text[:limit].rstrip() + (
            f"\n\n[truncated at {limit} characters. Read a narrower section with "
            f'get_manual_page(path="{doc.path}", section="<title>").]'
        )
    return text


def stats() -> str:
    index = get_index()
    if index is None:
        return missing_corpus_message()
    total_chars = sum(len(doc.text) for doc in index.docs)
    return (
        f"Manual corpus: {index.root}\n"
        f"{index.total_pages} pages, {total_chars / 1_000_000:.1f}M characters, "
        f"{len(index._doc_freq)} distinct terms"
    )


def install(source: str, destination: str | None = None) -> str:
    """Copy a manual checkout into the default location (used by the CLI)."""
    src = Path(source).expanduser()
    if not src.is_dir():
        raise SystemExit(f"Not a directory: {src}")
    # Accept either the manual folder or a checkout that contains it.
    if not (src / "index.rst").exists() and (src / "manual" / "index.rst").exists():
        src = src / "manual"
    if not (src / "index.rst").exists():
        raise SystemExit(f"{src} does not look like the Blender Manual (no index.rst)")
    dest = Path(destination).expanduser() if destination else DEFAULT_MANUAL_DIR
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    count = sum(1 for _ in dest.rglob("*.rst"))
    return f"Installed {count} manual pages into {dest}"


def _main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "stats"}:
        print(stats())
        print()
        print("usage: mcp-for-blender manual [--install DIR] [--search QUERY] [stats]")
        return 0
    if argv[0] == "--install":
        print(install(argv[1]) if len(argv) > 1 else "usage: --install <dir>")
        return 0
    if argv[0] == "--search":
        print(search(" ".join(argv[1:])))
        return 0
    print(f"Unknown argument: {argv[0]}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    """Entry point for `mcp-for-blender manual`."""
    return _main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
