# nmbr7.github.io — Zola site

Personal site built with Zola static generator.

## Notes sync

The `/notes` section embeds markdown files from the local RESEARCH folder
(default `/Users/nmbr7/.NMBR7/Projects/RESEARCH`, override with `$RESEARCH_DIR`).

**To pull the latest changes from RESEARCH into the site, run:**

```sh
python3 scripts/sync_notes.py
```

What the script does:
- wipes `content/notes/` and rebuilds it from scratch
- converts YAML frontmatter (`---`) in source `.md` files to TOML (`+++`)
- generates `_index.md` for each category and the top-level `Notes` section
- preserves YAML `tags` and `created` date when present

After syncing, build or serve as usual:

```sh
zola build
# or
zola serve
```

Convenience wrappers that do both:

```sh
./scripts/build.sh
./scripts/serve.sh
```

## Deploy

Site is hosted on GitHub Pages from the `master` branch. The `source` branch holds the Zola source; `master` holds only the built `public/` output.

**To deploy:**

```sh
./scripts/deploy.sh "Deploy: <description>"
```

Syncs notes, builds, copies `public/` to `master` branch via worktree, commits, pushes.

Branch layout:
- `source` — Zola source (templates, content, sass, scripts)
- `master` — built HTML/CSS/assets only (GitHub Pages root)

## Categories

Categories synced from RESEARCH are defined in `scripts/sync_notes.py`:
`database`, `distributed`, `hardware`, `os`, `programming`, `reference`.

Add new categories by editing the `CATEGORIES` dict in that script.

## Templates

- `templates/section.html` — renders category pages (subsection cards + notes list)
- `templates/static.html` — renders individual note pages and other static pages
- `templates/taxonomy_list.html` / `taxonomy_single.html` — tag pages

## Theme toggle

`templates/snippets/theme-toggle.html` (full toolbar) and
`templates/snippets/theme-toggle-only.html` (toggle button only, used on home).
Theme state stored in `localStorage`, defaults to OS `prefers-color-scheme`.
