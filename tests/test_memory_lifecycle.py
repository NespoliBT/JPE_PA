"""Tests for soft delete, restore, and conflict detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from jpe.config import get_settings
from jpe.memory.store import MemoryStore, reset_store


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setenv("JPE_MEMORY_DIR", str(tmp_path / "memory"))
    get_settings.cache_clear()
    reset_store()
    yield MemoryStore()
    reset_store()
    get_settings.cache_clear()


# -- forget / restore ---------------------------------------------------


def test_forget_moves_file_to_trash(store: MemoryStore) -> None:
    memory = store.save("Note", "body")
    original = Path(memory.path)

    assert store.forget("note")
    assert not original.exists()
    assert list(store.trash.glob("note.*.md"))


def test_forget_keeps_the_row(store: MemoryStore) -> None:
    store.save("Note", "body")
    store.forget("note")

    assert store.get("note") is None
    assert store.get("note", include_deleted=True) is not None
    assert store.count() == 0
    assert store.count(include_deleted=True) == 1


def test_forgotten_memory_is_not_listed_or_searched(store: MemoryStore) -> None:
    store.save("Note", "unique-token")
    store.forget("note")

    assert store.list_all() == []
    assert store.search("unique") == []
    assert store.tree() == []


def test_forget_missing_returns_false(store: MemoryStore) -> None:
    assert store.forget("nope") is False


def test_forget_twice_returns_false(store: MemoryStore) -> None:
    store.save("Note", "body")
    assert store.forget("note")
    assert store.forget("note") is False


def test_restore_brings_it_back(store: MemoryStore) -> None:
    store.save("Note", "the body")
    store.forget("note")

    assert store.restore("note")
    assert store.get("note") is not None
    assert store.read("note") == "the body\n"
    assert store.search("body") != []


def test_restore_missing_returns_false(store: MemoryStore) -> None:
    assert store.restore("nope") is False


def test_restore_live_memory_returns_false(store: MemoryStore) -> None:
    store.save("Note", "body")
    assert store.restore("note") is False


def test_saving_a_forgotten_slug_revives_it(store: MemoryStore) -> None:
    store.save("Note", "old")
    store.forget("note")
    store.save("Note", "new")

    assert store.get("note") is not None
    assert store.read("note") == "new\n"
    assert store.count() == 1


def test_forget_keeps_links_intact(store: MemoryStore) -> None:
    store.save("Target", "t")
    store.save("Source", "s", links=["target"])
    store.forget("target")

    assert store.get("source").links == ["target"]
    assert store.restore("target")
    assert store.get("target") is not None


def test_forget_does_not_orphan_children(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Child", "c", parent="root")
    store.forget("root")

    child = store.get("child")
    assert child is not None
    assert child.parent == "root"
    assert store.get("root") is None


# -- conflict detection -------------------------------------------------


def test_conflicts_by_shared_tag(store: MemoryStore) -> None:
    store.save("Existing", "body", ["nix"])
    conflicts = store.conflicts("New note", ["nix"])
    assert [memory.slug for memory in conflicts] == ["existing"]


def test_conflicts_by_shared_title_word(store: MemoryStore) -> None:
    store.save("NixOS setup", "body")
    conflicts = store.conflicts("NixOS preferences", [])
    assert [memory.slug for memory in conflicts] == ["nixos-setup"]


def test_conflicts_ignores_short_words(store: MemoryStore) -> None:
    store.save("The cat", "body")
    assert store.conflicts("The dog", []) == []


def test_conflicts_excludes_self(store: MemoryStore) -> None:
    store.save("NixOS setup", "body", ["nix"])
    assert store.conflicts("NixOS setup", ["nix"]) == []


def test_conflicts_ignores_deleted(store: MemoryStore) -> None:
    store.save("Existing", "body", ["nix"])
    store.forget("existing")
    assert store.conflicts("New note", ["nix"]) == []


def test_conflicts_empty_when_nothing_overlaps(store: MemoryStore) -> None:
    store.save("Cooking", "body", ["food"])
    assert store.conflicts("Astronomy", ["space"]) == []


# -- FTS consistency ----------------------------------------------------


def test_fts_rebuild_repairs_a_stale_index(store: MemoryStore) -> None:
    store.save("Note", "searchable body")
    with store._connect() as connection:
        connection.execute("DELETE FROM memories_fts")

    assert store.search_fts("searchable") == []
    assert store.rebuild_fts() == 1
    assert store.search_fts("searchable") == ["note"]


def test_fts_index_survives_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JPE_MEMORY_DIR", str(tmp_path / "memory"))
    get_settings.cache_clear()
    reset_store()
    try:
        first = MemoryStore()
        first.save("Note", "persisted body")
        second = MemoryStore()
        assert second.search_fts("persisted") == ["note"]
    finally:
        reset_store()
        get_settings.cache_clear()
