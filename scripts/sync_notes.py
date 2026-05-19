#!/usr/bin/env python3
"""
Sync notes from RESEARCH folder into Zola content/notes/.

- Reads markdown files from $RESEARCH_DIR (default: /Users/nmbr7/.NMBR7/Projects/RESEARCH)
- Converts YAML frontmatter -> TOML frontmatter
- Writes section _index.md per category
- Writes top-level _index.md

Run: python3 scripts/sync_notes.py
"""

import os
import re
import shutil
import sys
from pathlib import Path

SRC = Path(os.environ.get("RESEARCH_DIR", "/Users/nmbr7/.NMBR7/Projects/RESEARCH"))
DST = Path(__file__).resolve().parent.parent / "content" / "notes"

CATEGORIES = {
    "database": {
        "title": "Database",
        "weight": 10,
        "description": "Storage engines, query optimization, WAL, replication, and analytics systems.",
    },
    "distributed": {
        "title": "Distributed Systems",
        "weight": 20,
        "description": "Consensus, replication, messaging, and fault-tolerance protocols.",
    },
    "hardware": {
        "title": "Hardware",
        "weight": 30,
        "description": "CPU microarchitecture, accelerators, interconnects, and performance counters.",
    },
    "os": {
        "title": "Operating Systems",
        "weight": 40,
        "description": "Kernel internals, syscalls, virtualization, filesystems, and async I/O.",
    },
    "programming": {
        "title": "Programming",
        "weight": 50,
        "description": "Languages, compilers, data structures, and low-level systems work.",
    },
    "reference": {
        "title": "Reference",
        "weight": 100,
        "description": "Glossary, paper index, and external bookmarks.",
    },
}

ACRONYMS = {
    "db", "wal", "lsm", "ha", "cpu", "gpu", "tpu", "ooo", "pcie", "isa",
    "io", "kvm", "vfio", "jit", "vcs", "stm32", "ipc", "api", "sql", "ssd",
    "nvme", "tcp", "udp", "rdma", "mmu", "tlb",
}


def slug_to_title(name: str) -> str:
    name = name.replace(".md", "")
    parts = name.replace("_", " ").split()
    out = []
    for p in parts:
        if p.lower() in ACRONYMS:
            out.append(p.upper())
        else:
            out.append(p.capitalize())
    return " ".join(out)


# matches ](path/file.md#anchor) where path can be ../category, ./, or bare
CROSS_CAT_RE = re.compile(r"\]\(\.\.?/([a-zA-Z0-9_\-]+)/([a-zA-Z0-9_\-]+)\.md(#[^)]*)?\)")
SAME_DIR_RE = re.compile(r"\]\((?:\./)?([a-zA-Z0-9_\-]+)\.md(#[^)]*)?\)")


def rewrite_links(body: str, current_cat: str) -> str:
    # cross-category: ../category/file.md -> @/notes/category/file.md
    def cross_repl(m):
        return f"](@/notes/{m.group(1)}/{m.group(2)}.md{m.group(3) or ''})"
    body = CROSS_CAT_RE.sub(cross_repl, body)

    # same-dir: file.md or ./file.md -> @/notes/<current_cat>/file.md
    def same_repl(m):
        return f"](@/notes/{current_cat}/{m.group(1)}.md{m.group(2) or ''})"
    body = SAME_DIR_RE.sub(same_repl, body)

    body = linkify_toc(body)
    return body


def slugify(text: str) -> str:
    # mirror Zola's heading slug logic: strip markdown, lowercase, replace non-alnum with '-'
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


TOC_HEADER_RE = re.compile(r"^(#+)\s*table of contents", re.IGNORECASE)
LIST_ITEM_RE = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.+)$")
LINK_RE_INLINE = re.compile(r"\[([^\]]+)\]\(#[^)]+\)")


def collect_headings(body: str) -> list:
    """Return list of (full_slug, normalized_text) tuples for every heading in body."""
    headings = []
    for line in body.split("\n"):
        m = re.match(r"^(#+)\s+(.+?)\s*$", line)
        if not m:
            continue
        raw = m.group(2)
        # strip trailing anchor markers like {#anchor}
        raw = re.sub(r"\s*\{#[^}]+\}\s*$", "", raw)
        full_slug = slugify(raw)
        # normalized: drop leading numbering like "1." "1.1" "Section 3:"
        norm_text = re.sub(r"^[\d\.\:\)\s]+", "", raw).strip()
        norm_slug = slugify(norm_text)
        headings.append((full_slug, norm_slug, raw))
    return headings


def find_anchor(text: str, headings: list) -> str:
    """Find the best matching heading anchor for given TOC text."""
    target = slugify(text)
    if not target:
        return ""
    # exact full-slug match
    for full, norm, _ in headings:
        if full == target:
            return full
    # match against normalized (numbering-stripped) slug
    for full, norm, _ in headings:
        if norm == target:
            return full
    # suffix match: heading endswith target
    for full, norm, _ in headings:
        if full.endswith("-" + target) or norm.endswith("-" + target):
            return full
    # substring match
    for full, norm, _ in headings:
        if target in full or target in norm:
            return full
    return ""


def linkify_toc(body: str) -> str:
    """Find a 'Table of Contents' section and turn bare bullet text into anchor links."""
    headings = collect_headings(body)
    if not headings:
        return body

    lines = body.split("\n")
    out = []
    in_toc = False
    toc_header_level = 0

    for line in lines:
        m = TOC_HEADER_RE.match(line.lstrip("> "))
        if m:
            in_toc = True
            toc_header_level = len(m.group(1))
            out.append(line)
            continue

        if in_toc:
            stripped = line.strip()
            if stripped.startswith("#"):
                heading_match = re.match(r"^(#+)\s", stripped)
                if heading_match and len(heading_match.group(1)) <= toc_header_level:
                    in_toc = False
                    out.append(line)
                    continue
            if stripped in ("---", "***", "___"):
                in_toc = False
                out.append(line)
                continue

            li = LIST_ITEM_RE.match(line)
            if li:
                indent, marker, text = li.group(1), li.group(2), li.group(3)
                # if already linked, rewrite the anchor to match a real heading
                inline = LINK_RE_INLINE.search(text)
                if inline:
                    def fix_anchor(mm):
                        link_text = mm.group(1)
                        new_anchor = find_anchor(link_text, headings)
                        if new_anchor:
                            return f"[{link_text}](#{new_anchor})"
                        return mm.group(0)
                    new_text = LINK_RE_INLINE.sub(fix_anchor, text)
                    out.append(f"{indent}{marker} {new_text}")
                    continue
                bare = text.strip()
                if not bare:
                    out.append(line)
                    continue
                anchor = find_anchor(bare, headings)
                if anchor:
                    out.append(f"{indent}{marker} [{bare}](#{anchor})")
                    continue
            out.append(line)
            continue

        out.append(line)

    return "\n".join(out)


def convert_frontmatter(content: str, title: str, current_cat: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    body = content
    date = None
    tags = []
    if m:
        yaml_block = m.group(1)
        body = content[m.end():]
        for line in yaml_block.split("\n"):
            line = line.strip()
            if line.startswith("created:"):
                date = line.split(":", 1)[1].strip()
            elif line.startswith("tags:"):
                tag_str = line.split(":", 1)[1].strip()
                tag_str = tag_str.strip("[]")
                tags = [t.strip().strip('"').strip("'") for t in tag_str.split(",") if t.strip()]

    fm = f'+++\ntitle = "{title}"\n'
    if date:
        fm += f"date = {date}\n"
    if tags:
        tags_quoted = ", ".join(f'"{t}"' for t in tags)
        fm += f"[taxonomies]\ntags = [{tags_quoted}]\n"
    fm += "+++\n\n"
    return fm + rewrite_links(body, current_cat)


def main() -> int:
    if not SRC.exists():
        print(f"ERROR: source dir not found: {SRC}", file=sys.stderr)
        return 1

    # wipe and recreate destination to avoid stale files
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)

    # top-level index
    (DST / "_index.md").write_text(
        '+++\n'
        'title = "Notes & References"\n'
        'sort_by = "weight"\n'
        'template = "section.html"\n'
        'page_template = "note.html"\n'
        '+++\n'
    )

    total = 0
    for slug, meta in CATEGORIES.items():
        src_dir = SRC / slug
        if not src_dir.exists():
            continue
        title = meta["title"]
        weight = meta["weight"]
        description = meta["description"]
        dst_dir = DST / slug
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / "_index.md").write_text(
            f'+++\n'
            f'title = "{title}"\n'
            f'description = "{description}"\n'
            f'weight = {weight}\n'
            f'sort_by = "title"\n'
            f'template = "section.html"\n'
            f'page_template = "note.html"\n'
            f'+++\n'
        )
        for src_file in sorted(src_dir.glob("*.md")):
            if src_file.name == "open_questions.md":
                continue
            page_title = slug_to_title(src_file.name)
            converted = convert_frontmatter(src_file.read_text(), page_title, slug)
            (dst_dir / src_file.name).write_text(converted)
            total += 1

    print(f"synced {total} notes across {len(CATEGORIES)} categories from {SRC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
