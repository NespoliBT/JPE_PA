"""Memory tools: save, recall, search, and list durable notes.

Tiers: ``remember`` is tier 1 (reversible by the assistant — the file is
git-tracked and ``forget`` will exist in Phase 1b). Reads are tier 0.

Memory bodies are the user's own notes, but they can contain text copied from
elsewhere, so ``recall`` and ``search_memory`` mark their output untrusted. The
memory *index* in the system prompt is not wrapped: it is titles and slugs only,
and it is the assistant's own structure.
"""

from __future__ import annotations

from typing import Any, ClassVar

from jpe.memory.retrieval import search as search_memory_store
from jpe.memory.store import TreeNode, get_store
from jpe.tiers import Tier
from jpe.tools.base import Tool, ToolResult
from jpe.tools.registry import register


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


class RememberTool(Tool):
    """Save a durable note about the user or their projects."""

    name = "remember"
    description = (
        "Save a durable memory: a preference, decision, project fact, or anything "
        "worth recalling in future sessions. Use a short descriptive title. "
        "Set `parent` to nest this note under an existing memory (e.g. a project), "
        "and `links` to cross-reference related notes."
    )
    tier = Tier.REVERSIBLE
    icon = "🧠"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short descriptive title."},
            "content": {"type": "string", "description": "Markdown body of the memory."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for grouping, e.g. ['preference', 'nix'].",
            },
            "parent": {
                "type": "string",
                "description": "Slug of the parent memory, for hierarchical notes.",
            },
            "links": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Slugs of related memories to cross-reference.",
            },
        },
        "required": ["title", "content"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        title = str(kwargs["title"]).strip()
        content = str(kwargs["content"])
        if not title:
            return ToolResult(content="title must not be empty", is_error=True)

        tags = _string_list(kwargs.get("tags"))
        links = _string_list(kwargs.get("links"))
        parent = str(kwargs["parent"]).strip() if kwargs.get("parent") else None

        store = get_store()
        # Surface near-duplicates before writing, so the model can link instead
        # of creating a second note about the same thing.
        conflicts = store.conflicts(title, tags)

        try:
            memory = store.save(title, content, tags, parent=parent, links=links)
        except ValueError as exc:
            return ToolResult(content=str(exc), is_error=True)

        detail = f"{len(tags)} tags"
        if memory.parent:
            detail += f", parent {memory.parent!r}"
        if memory.links:
            detail += f", {len(memory.links)} links"

        message = f"Saved memory {memory.slug!r} ({detail})"
        if conflicts:
            listed = ", ".join(f"{item.slug!r} ({item.title})" for item in conflicts[:5])
            message += (
                f"\n\nPossible overlap with existing memories: {listed}. "
                "If one of these covers the same ground, consider linking them "
                "with `links` or updating that memory instead of keeping both."
            )

        return ToolResult(
            content=message,
            metadata={
                "slug": memory.slug,
                "path": memory.path,
                "tags": tags,
                "parent": memory.parent,
                "links": memory.links,
                "conflicts": [item.slug for item in conflicts],
            },
        )


class RecallTool(Tool):
    """Read a memory by slug."""

    name = "recall"
    description = "Read the full content of a stored memory by its slug."
    tier = Tier.READ
    icon = "🧠"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Memory slug, e.g. 'user-preferences'."},
        },
        "required": ["slug"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        slug = str(kwargs["slug"]).strip()
        store = get_store()
        body = store.read(slug)
        memory = store.get(slug)
        if body is None or memory is None:
            return ToolResult(content=f"No memory with slug {slug!r}", is_error=True)

        context: list[str] = []
        ancestors = store.ancestors(memory.slug)
        if ancestors:
            context.append(f"Path: {' > '.join([*ancestors, memory.slug])}")
        children = store.children(memory.slug)
        if children:
            context.append("Children: " + ", ".join(child.slug for child in children))
        if memory.links:
            context.append("Links: " + ", ".join(memory.links))
        backlinks = store.backlinks(memory.slug)
        if backlinks:
            context.append("Linked from: " + ", ".join(item.slug for item in backlinks))

        header = f"# {memory.title}\n"
        if context:
            header += "\n" + "\n".join(context) + "\n"
        return ToolResult(
            content=header + "\n" + body,
            metadata={
                "slug": memory.slug,
                "parent": memory.parent,
                "children": [child.slug for child in children],
                "links": memory.links,
            },
            untrusted_source=f"memory:{memory.slug}",
        )


class SearchMemoryTool(Tool):
    """Search memories by text or tag."""

    name = "search_memory"
    description = (
        "Search stored memories by text (matches titles, tags, and content) or "
        "list them by tag. Use this before asking the user to repeat themselves."
    )
    tier = Tier.READ
    icon = "🧠"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for."},
            "tag": {"type": "string", "description": "Only return memories with this tag."},
        },
        "required": [],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        store = get_store()
        query = str(kwargs.get("query") or "").strip()
        tag = str(kwargs.get("tag") or "").strip() or None

        if not query:
            memories = store.list_all(tag)
            if not memories:
                return ToolResult(
                    content="(no matching memories)",
                    metadata={"count": 0, "provenance": f"searched {store.count()} memories"},
                )
            lines = [_format_memory(m) for m in memories]
            return ToolResult(
                content="\n".join(lines),
                metadata={
                    "count": len(memories),
                    "provenance": f"searched {store.count()} memories",
                },
                untrusted_source="memory:search",
            )

        result = search_memory_store(store, query)
        hits = result.hits
        if tag:
            hits = [hit for hit in hits if tag in hit.memory.tags]

        provenance = result.provenance.describe()
        if not hits:
            return ToolResult(
                content=f"(no matching memories — {provenance})",
                metadata={"count": 0, "provenance": provenance},
            )

        lines = [_format_hit(hit) for hit in hits]
        return ToolResult(
            content="\n".join(lines) + f"\n\n({provenance})",
            metadata={
                "count": len(hits),
                "provenance": provenance,
                "slugs": [hit.memory.slug for hit in hits],
            },
            untrusted_source="memory:search",
        )


class MemoryTreeTool(Tool):
    """Show the memory hierarchy as a tree."""

    name = "memory_tree"
    description = (
        "Show stored memories as a tree, optionally rooted at one slug. Use this "
        "to see how project notes are organised before adding new ones."
    )
    tier = Tier.READ
    icon = "🧠"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "Root memory slug. Omit for the whole forest.",
            },
        },
        "required": [],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        slug = str(kwargs.get("slug") or "").strip() or None
        nodes = get_store().tree(slug)
        if not nodes:
            return ToolResult(content="(no memories)", metadata={"count": 0})

        lines = _render(nodes)
        return ToolResult(
            content="\n".join(lines),
            metadata={"count": len(lines)},
            untrusted_source="memory:tree",
        )


class ForgetTool(Tool):
    """Soft-delete a memory, keeping it recoverable."""

    name = "forget"
    description = (
        "Soft-delete a memory: it stops appearing in search and in the prompt, "
        "but the file is moved to memory/.trash/ and the record is kept, so it "
        "can be restored. Use this instead of editing a memory into nothing."
    )
    tier = Tier.REVERSIBLE
    icon = "🧠"
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Slug of the memory to forget."},
        },
        "required": ["slug"],
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        slug = str(kwargs["slug"]).strip()
        store = get_store()
        if not store.forget(slug):
            return ToolResult(content=f"No memory with slug {slug!r}", is_error=True)
        return ToolResult(
            content=(
                f"Forgot {slug!r}. The file is in memory/.trash/ and the record is "
                "kept, so it can be restored."
            ),
            metadata={"slug": slug, "soft_deleted": True},
        )


def _format_memory(memory: Any) -> str:
    tags = f" [{', '.join(memory.tags)}]" if memory.tags else ""
    return f"- {memory.slug}: {memory.title}{tags}"


def _format_hit(hit: Any) -> str:
    tags = f" [{', '.join(hit.memory.tags)}]" if hit.memory.tags else ""
    line = f"- {hit.memory.slug}: {hit.memory.title}{tags}"
    if hit.via:
        line += f" (via {hit.via})"
    return line


def _render(nodes: list[TreeNode], depth: int = 0) -> list[str]:
    lines: list[str] = []
    for node in nodes:
        tags = f" [{', '.join(node.memory.tags)}]" if node.memory.tags else ""
        lines.append(f"{'  ' * depth}- {node.memory.slug}: {node.memory.title}{tags}")
        lines.extend(_render(node.children, depth + 1))
    return lines


register(RememberTool())
register(RecallTool())
register(SearchMemoryTool())
register(MemoryTreeTool())
register(ForgetTool())
