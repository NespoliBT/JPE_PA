"""Tests for the memory store."""

from __future__ import annotations

from pathlib import Path

import pytest

from jpe.config import get_settings
from jpe.memory.store import MemoryStore, reset_store, slugify


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setenv("JPE_MEMORY_DIR", str(tmp_path / "memory"))
    get_settings.cache_clear()
    reset_store()
    yield MemoryStore()
    reset_store()
    get_settings.cache_clear()


def test_slugify() -> None:
    assert slugify("User likes Nix!") == "user-likes-nix"
    assert slugify("   ") == "memory"
    assert slugify("a/b/c") == "a-b-c"


def test_save_writes_file_and_row(store: MemoryStore) -> None:
    memory = store.save("First note", "body text")
    assert memory.slug == "first-note"
    assert Path(memory.path).is_file()
    assert Path(memory.path).read_text() == "body text\n"
    assert store.get("first-note") is not None


def test_save_is_idempotent_and_preserves_created_at(store: MemoryStore) -> None:
    first = store.save("Note", "one")
    second = store.save("Note", "two")
    assert first.created_at == second.created_at
    assert store.read("note") == "two\n"


def test_save_with_tags(store: MemoryStore) -> None:
    store.save("Tagged", "body", ["preference", "nix"])
    memory = store.get("tagged")
    assert memory is not None
    assert memory.tags == ["preference", "nix"]


def test_parent_must_exist(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        store.save("Child", "body", parent="missing")


def test_parent_cannot_be_self(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="own parent"):
        store.save("Self", "body", parent="self")


def test_parent_cycle_is_rejected(store: MemoryStore) -> None:
    store.save("A", "a")
    store.save("B", "b", parent="a")
    with pytest.raises(ValueError, match="cycle"):
        store.save("A", "a", parent="b")


def test_links_must_exist(store: MemoryStore) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        store.save("Note", "body", links=["missing"])


def test_links_are_stored_and_reversible(store: MemoryStore) -> None:
    store.save("Target", "t")
    store.save("Source", "s", links=["target"])
    assert store.get("source").links == ["target"]
    store.save("Source", "s", links=[])
    assert store.get("source").links == []


def test_self_link_is_ignored(store: MemoryStore) -> None:
    store.save("Note", "body", links=["note"])
    assert store.get("note").links == []


def test_ancestors_root_first(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Mid", "m", parent="root")
    store.save("Leaf", "l", parent="mid")
    assert store.ancestors("leaf") == ["root", "mid"]


def test_children_and_roots(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Child", "c", parent="root")
    assert [m.slug for m in store.roots()] == ["root"]
    assert [m.slug for m in store.children("root")] == ["child"]


def test_backlinks(store: MemoryStore) -> None:
    store.save("Target", "t")
    store.save("Source", "s", links=["target"])
    assert [m.slug for m in store.backlinks("target")] == ["source"]


def test_tree_shape(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Child", "c", parent="root")
    nodes = store.tree()
    assert len(nodes) == 1
    assert nodes[0].memory.slug == "root"
    assert nodes[0].children[0].memory.slug == "child"


def test_tree_rooted_at_slug(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Child", "c", parent="root")
    nodes = store.tree("child")
    assert len(nodes) == 1
    assert nodes[0].memory.slug == "child"


def test_search_matches_title_tags_and_body(store: MemoryStore) -> None:
    store.save("Nix setup", "uses flakes", ["nixos"])
    store.save("Other", "unrelated")
    assert [m.slug for m in store.search("nix")] == ["nix-setup"]
    assert [m.slug for m in store.search("flakes")] == ["nix-setup"]
    assert [m.slug for m in store.search("nixos")] == ["nix-setup"]
    assert store.search("nothing here") == []


def test_list_all_by_tag(store: MemoryStore) -> None:
    store.save("A", "a", ["x"])
    store.save("B", "b", ["y"])
    assert [m.slug for m in store.list_all("x")] == ["a"]


def test_delete_removes_file_and_reparents_children(store: MemoryStore) -> None:
    store.save("Root", "r")
    store.save("Mid", "m", parent="root")
    store.save("Leaf", "l", parent="mid")
    path = Path(store.get("mid").path)

    assert store.delete("mid")
    assert not path.exists()
    assert store.get("mid") is None
    assert store.get("leaf").parent == "root"


def test_delete_missing_returns_false(store: MemoryStore) -> None:
    assert store.delete("nope") is False


def test_read_missing_returns_none(store: MemoryStore) -> None:
    assert store.read("nope") is None


def test_relative_memory_dir_resolves_against_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative memory_dir must not depend on the process's cwd."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("JPE_WORKSPACE", str(workspace))
    monkeypatch.setenv("JPE_MEMORY_DIR", "memory")
    get_settings.cache_clear()
    reset_store()
    try:
        store = MemoryStore()
        assert store.root == (workspace / "memory").resolve()
    finally:
        reset_store()
        get_settings.cache_clear()


def test_absolute_memory_dir_is_used_as_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "elsewhere"
    monkeypatch.setenv("JPE_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("JPE_MEMORY_DIR", str(target))
    get_settings.cache_clear()
    reset_store()
    try:
        store = MemoryStore()
        assert store.root == target.resolve()
    finally:
        reset_store()
        get_settings.cache_clear()
