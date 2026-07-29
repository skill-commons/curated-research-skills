---
name: rss-feed-monitor
description: Track public RSS or Atom feeds in an isolated local database, scan for new articles, and manage read state with explicit mutation safeguards.
version: 2.0.0
author: AIP AstroAgent team and Skill Commons contributors
license: MIT
metadata:
  hermes:
    category: general
    tags:
      - rss
      - atom
      - monitoring
      - blogs
      - sqlite
      - opml
---

# RSS Feed Monitor

## When to Use

Use this skill to maintain a local reading monitor for public RSS or Atom feeds, import an
OPML subscription list, discover feeds from public pages, or track read and unread
articles. The curated command contract is exercised with
[`blogwatcher-cli` v0.2.1](https://github.com/JulienTant/blogwatcher-cli/releases/tag/v0.2.1).

Do not use it to crawl private networks, bypass access controls, or monitor authenticated
feeds without explicit authorization and a credential-handling plan.

## Install or Select a Runtime

First run:

```bash
blogwatcher-cli --version
blogwatcher-cli --help
```

If the tool is absent, report the prerequisite and obtain permission before installing
software. Prefer one of these reproducible approaches:

- install the Go module at the explicit tag
  `github.com/JulienTant/blogwatcher-cli/cmd/blogwatcher-cli@v0.2.1`;
- download the matching v0.2.1 release archive, verify it against the release checksum
  file, inspect the archive, and extract it into a user-controlled tools directory;
- use a container image pinned by digest, with an explicit persistent data volume.

Never use `@latest`, an untagged container, a `curl | tar` pipeline, or extraction directly
into a system binary directory. If another version is already required, inspect that
version's subcommand help and disclose behavioral differences.

The v0.2.1 release binary prints `0.2.1`, but a binary built by tagged `go install` may
print `dev`. For a Go-built binary, run `go version -m <path-to-binary>` and confirm that
the module version is `v0.2.1`; do not rely on the display version alone.

## Database and Mutation Safety

The default database is `~/.blogwatcher-cli/blogwatcher-cli.db`. Prefer an explicit
`--db` path so the target is visible in every command. For testing, always use a new
temporary database.

| Operation | Effect |
|---|---|
| `blogs`, `articles` | Read local state only |
| `add`, `import`, `scan`, `read`, `unread` | Mutate the selected SQLite database |
| `read-all`, `remove` | Broad or destructive mutation; review target and confirm |

Before opening an existing database with a newer CLI, stop other writers and make a
verified SQLite backup because startup may apply schema migrations. For migration, copy
the database, open and inspect the copy, and remove the old file only after verification;
do not blindly move the only copy.

## Workflow

### 1. Select the exact target

Choose a database path and review the user-approved public feed or page URLs:

```bash
blogwatcher-cli --db "./state/research-feeds.db" blogs
```

Do not use `--unsafe-client` in normal operation. It disables the client's protection
against private and loopback destinations.

### 2. Add or import subscriptions

Use an explicit feed URL when known:

```bash
blogwatcher-cli --db "./state/research-feeds.db" add \
  "Example Research Blog" "https://example.org/blog" \
  --feed-url "https://example.org/feed.xml"

blogwatcher-cli --db "./state/research-feeds.db" import "./subscriptions.opml"
```

Without `--feed-url`, the tool attempts feed discovery. Use
`--scrape-selector` only for a public site that lacks a feed and after inspecting a stable
CSS selector. `add` records the subscription but does not prove the URL is reachable or
safe to fetch; network and SSRF validation occurs during `scan`. Review the resulting blog
list before scanning.

### 3. Scan and inspect

```bash
blogwatcher-cli --db "./state/research-feeds.db" scan
blogwatcher-cli --db "./state/research-feeds.db" articles
blogwatcher-cli --db "./state/research-feeds.db" articles \
  --since 2026-01-01 --before 2026-02-01
```

`--since` is inclusive and `--before` is exclusive in v0.2.1. A scan performs external
reads and writes newly discovered articles to the selected database. Report failed or
partially scanned feeds rather than treating the result as complete.

### 4. Update read state deliberately

```bash
blogwatcher-cli --db "./state/research-feeds.db" read 42
blogwatcher-cli --db "./state/research-feeds.db" unread 42
blogwatcher-cli --db "./state/research-feeds.db" read-all --blog "Example Research Blog"
blogwatcher-cli --db "./state/research-feeds.db" remove "Example Research Blog"
```

Allow the normal confirmation prompt for `read-all` and `remove`. Do not add `--yes` or
set an automatic-yes environment variable unless the user explicitly approved the exact
bulk target and non-interactive execution. A declined prompt in v0.2.1 can exit successfully
without an explicit cancellation message, so exit status alone does not prove a mutation
occurred.

### 5. Verify

- List subscriptions and complete article inventory from the same explicit database,
  using `articles --all` when read entries must remain visible.
- Confirm expected feed names, date boundaries, and read counts.
- Re-list state after `read-all` or `remove`; check that a remove operation affected only
  the named blog and its articles.
- Preserve the backup until the upgraded or migrated database has been reopened
  successfully.
- Record CLI version, database path, subscription changes, scan time, and errors.

## Recurring Monitoring

A recurring monitor adds a scheduler, retained state, and often an external delivery
channel. Run the exact scan manually first. Obtain approval before creating or changing a
cron job, CI schedule, automation, notification, or email. Avoid overlapping database
writers and keep delivery credentials outside the SQLite file and command logs.
