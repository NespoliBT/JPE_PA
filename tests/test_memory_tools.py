"""Tests for the memory tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from jpe.config import get_settings
from jpe.memory.store import MemoryStore, reset_store
from jpe.tiers import Tier
from jpe.tools.builtin.memory import (
    ForgetTool,
    MemoryTreeTool,
    RecallTool,
    RememberTool,
    SearchMemoryTool,
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setenv("JPE_MEMORY_DIR", str(tmp_path / "memory"))
    get_settings.cache_clear()
    reset_store()
    yield MemoryStore()
    reset_store()
    get_settings.cache_clear()


def test_memory_tool_tiers() -> None:
    assert RememberTool().tier is Tier.REVERSIBLE
    assert RecallTool().tier is Tier.READ
    assert SearchMemoryTool().tier is Tier.READ
    assert MemoryTreeTool().tier is Tier.READ


async def test_remember_saves(store: MemoryStore) -> None:
    result = await RememberTool().run(title="Likes Nix", content="Uses flakes.", tags=["pref"])
    assert result.is_error is False
    assert "likes-nix" in result.content
    assert store.get("likes-nix") is not None


async def test_remember_rejects_empty_title(store: MemoryStore) -> None:
    result = await RememberTool().run(title="  ", content="x")
    assert result.is_error is True


async def test_remember_reports_bad_parent(store: MemoryStore) -> None:
    result = await RememberTool().run(title="Child", content="x", parent="missing")
    assert result.is_error is True
    assert "does not exist" in result.content


async def test_recall_returns_body_and_marks_untrusted(store: MemoryStore) -> None:
    store.save("Note", "the body")
    result = await RecallTool().run(slug="note")
    assert "the body" in result.content
    assert result.untrusted_source == "memory:note"
    assert "<untrusted" in result.for_model()


async def test_recall_includes_tree_context(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Child", "c", parent="root")
    result = await RecallTool().run(slug="child")
    assert "Path: root > child" in result.content


async def test_recall_missing_is_error(store: MemoryStore) -> None:
    result = await RecallTool().run(slug="nope")
    assert result.is_error is True


async def test_search_memory_finds(store: MemoryStore) -> None:
    store.save("Nix setup", "flakes")
    result = await SearchMemoryTool().run(query="nix")
    assert "nix-setup" in result.content
    assert result.metadata["count"] == 1


async def test_search_memory_empty(store: MemoryStore) -> None:
    result = await SearchMemoryTool().run(query="nothing")
    assert result.metadata["count"] == 0


async def test_search_memory_by_tag(store: MemoryStore) -> None:
    store.save("A", "a", ["x"])
    store.save("B", "b", ["y"])
    result = await SearchMemoryTool().run(tag="x")
    assert "a" in result.content
    assert result.metadata["count"] == 1


async def test_memory_tree_renders(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Child", "c", parent="root")
    result = await MemoryTreeTool().run()
    assert "root" in result.content
    assert "child" in result.content


async def test_memory_tree_empty(store: MemoryStore) -> None:
    result = await MemoryTreeTool().run()
    assert "(no memories)" in result.content


async def test_search_reports_provenance(store: MemoryStore) -> None:
    store.save("Nix setup", "flakes")
    result = await SearchMemoryTool().run(query="nix")
    assert "searched 1 memories" in result.content
    assert result.metadata["provenance"]


async def test_search_no_match_still_reports_provenance(store: MemoryStore) -> None:
    store.save("Nix setup", "flakes")
    result = await SearchMemoryTool().run(query="zzzznothing")
    assert "no matching memories" in result.content
    assert "searched 1 memories" in result.content


async def test_search_includes_tree_neighbours(store: MemoryStore) -> None:
    store.save("Project pa", "the parent")
    store.save("Pa roadmap", "child about scheduling", parent="project-pa")
    result = await SearchMemoryTool().run(query="scheduling")
    assert "pa-roadmap" in result.content
    assert "project-pa" in result.content
    assert "via parent of" in result.content


async def test_remember_warns_about_overlap(store: MemoryStore) -> None:
    store.save("NixOS setup", "existing", ["nix"])
    result = await RememberTool().run(title="NixOS preferences", content="new", tags=["nix"])
    assert "Possible overlap" in result.content
    assert "nixos-setup" in result.content
    assert result.metadata["conflicts"] == ["nixos-setup"]


async def test_remember_without_overlap_has_no_warning(store: MemoryStore) -> None:
    result = await RememberTool().run(title="Astronomy", content="stars", tags=["space"])
    assert "Possible overlap" not in result.content
    assert result.metadata["conflicts"] == []


async def test_forget_tool_soft_deletes(store: MemoryStore) -> None:
    store.save("Note", "body")
    result = await ForgetTool().run(slug="note")
    assert result.is_error is False
    assert store.get("note") is None
    assert store.get("note", include_deleted=True) is not None


async def test_forget_tool_missing_is_error(store: MemoryStore) -> None:
    result = await ForgetTool().run(slug="nope")
    assert result.is_error is True


def test_forget_tool_tier() -> None:
    assert ForgetTool().tier is Tier.REVERSIBLE
