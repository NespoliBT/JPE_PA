# jpe

A personal assistant with explicit authority boundaries.

Named after Johann Peter Eckermann, Goethe's amanuensis: a good interlocutor and
a faithful record, not the author.

This is a from-scratch rebuild. Phases 0–2 (MCP host) of the roadmap in
`plan.md` are implemented; the Google integrations are not.

## What Phase 0 gives you

**Untrusted data is inert.** Every tool result that carries content the user did
not author — fetched pages, file bodies, directory listings, search hits, shell
output — is wrapped in an `<untrusted source="...">` region before the model
sees it. The system prompt states that such content is data, never instructions.
A closing tag inside the payload is escaped, so a page cannot end the region
early.

**Authority is layered.** `POLICY.md` is emitted as system authority and cannot
be overridden by conversation. `SOUL.md` is emitted as guideline authority and
can. The untrusted-content rule is emitted unconditionally, so a missing or
malformed policy file cannot remove it.

**Tools have tiers, not a boolean.**

| Tier | Meaning                            | Policy                   |
| ---- | ---------------------------------- | ------------------------ |
| 0    | Read-only                          | Always allowed           |
| 1    | Reversible by the assistant        | Allowed                  |
| 2    | Reversible by the user with effort | Allowed                  |
| 3    | Irreversible or externally visible | Requires an explicit yes |

**Autonomy is scoped.** `ScopeOfAutonomy` declares which tools may run, until
when, at what cost, and with what per-tool constraints. An expired scope refuses
everything.

**Tier 3 is confirmed without a prompt.** The tool refuses, the loop issues a
single-use token bound to the exact arguments, the model asks the user in prose,
and the user's next message must contain that token. No terminal prompt, no
blanket approval.

## What Phase 1 gives you

**Memory that persists.** Notes are markdown files under `memory/`, indexed in
SQLite. They form a forest: each note may have a parent and directed links to
other notes, with cycles rejected.

**Retrieval that ranks.** Three signals are fused with reciprocal rank fusion:
FTS5 BM25 over title/tags/body, tag match, and title match. BM25 handles exact
names and identifiers; the tag and title signals catch notes that are _about_ a
thing without naming it in the body.

**Retrieval returns a neighbourhood.** After the top hits, parents, children,
linked memories, and backlinks one hop out are added, each marked with how it
was reached (`via parent of X`). A memory store is a graph; returning a bare hit
throws that away.

**Every search reports provenance.** "searched 412 memories, 3 tags, 0 links" —
so "I couldn't find it" never sounds like "it isn't there".

**Conflicts are surfaced, not resolved.** `remember` reports memories that share
a tag or a significant title word, and lets the model decide whether to link,
update, or keep both.

**Forgetting is reversible.** `forget` moves the file to `memory/.trash/` and
keeps the row; `restore` brings it back.

**Tools.** `remember` and `forget` (tier 1); `recall`, `search_memory`,
`memory_tree` (tier 0). Memory bodies are marked untrusted — a note can contain
text copied from a web page. The prompt index carries slugs, titles, and tags
only, flattened to one line each.

## What Phase 2 gives you

**jpe is an MCP host.** Configure servers in `JPE_MCP_SERVERS` and their tools
appear alongside the builtins, namespaced as `server.tool` (MCP only guarantees
per-server uniqueness).

```console
$ export JPE_MCP_SERVERS='[{"name":"time","command":"uvx","args":["mcp-server-time"]}]'
$ jpe ask "What time is it in Tokyo?"
🔌 time.get_current_time(timezone='Asia/Tokyo')
```

**Tiers come from the server's annotations.** A tool advertising
`read_only_hint` is tier 0; `destructive_hint` makes it tier 3; anything else
falls back to the server's `default_tier`, which defaults to tier 2 — a server
that says nothing is assumed able to change things.

**MCP output is untrusted.** Every result is wrapped as
`<untrusted source="mcp:server/tool">`, because a remote server is exactly as
untrusted as a web page.

**Both error channels are handled.** A tool execution error (`is_error=True`)
goes back to the model so it can self-correct; a protocol error (unknown tool,
transport failure) is reported as an error result rather than crashing the loop.

**A broken server does not kill the session.** It is reported and skipped.

## Usage

```console
$ jpe ask "what's in this repo?"
$ jpe chat
$ jpe tools          # list tools and their tiers
$ jpe policy         # show POLICY.md and SOUL.md
$ jpe memory --tree  # browse the memory forest
$ jpe memory --show user-preferences
$ jpe memory --forget stale-note
$ jpe memory --restore stale-note
```

Configuration is environment-driven with a `JPE_` prefix:

```console
$ export OPENROUTER_API_KEY=...
$ export JPE_WORKSPACE=.
$ export JPE_ALLOW_SHELL=false
```

## Development

```console
$ uv sync
$ uv run pytest
$ uv run ruff check .
$ uv run mypy
```

## Layout

```
src/jpe/
├── authority.py     # authority levels, <untrusted> wrapping
├── tiers.py         # the reversibility ladder
├── scope.py         # ScopeOfAutonomy, ConfirmationBroker
├── agent.py         # the loop, where both gates are enforced
├── soul.py          # prompt composition by authority
├── config.py        # settings
├── llm/             # OpenRouter client and message models
├── memory/          # store (markdown + SQLite + FTS5), retrieval (RRF)
├── mcp.py           # MCP host: connections, tool wrapping, tier inference
└── tools/           # base, registry, sandbox, builtins
```
