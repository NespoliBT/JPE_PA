"""Tests for ranked retrieval: FTS5, RRF fusion, tree expansion, provenance."""

from __future__ import annotations

from pathlib import Path

import pytest

from jpe.config import get_settings
from jpe.memory.retrieval import RRF_K, Provenance, search
from jpe.memory.store import MemoryStore, reset_store


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryStore:
    monkeypatch.setenv("JPE_MEMORY_DIR", str(tmp_path / "memory"))
    get_settings.cache_clear()
    reset_store()
    yield MemoryStore()
    reset_store()
    get_settings.cache_clear()


def test_fts_is_available(store: MemoryStore) -> None:
    assert store.fts_available is True


def test_fts_finds_body_text(store: MemoryStore) -> None:
    store.save("Editor", "I use Neovim with a hand-rolled Lua config.")
    assert store.search_fts("neovim") == ["editor"]


def test_fts_is_stemmed(store: MemoryStore) -> None:
    """The porter tokenizer means 'running' should match 'run'."""
    store.save("Exercise", "I run every morning.")
    assert store.search_fts("running") == ["exercise"]


def test_fts_ignores_deleted(store: MemoryStore) -> None:
    store.save("Gone", "unique-token-xyz")
    store.forget("gone")
    assert store.search_fts("unique") == []


def test_fts_query_is_sanitised(store: MemoryStore) -> None:
    """FTS5 syntax in user input must not raise."""
    store.save("Note", "body")
    for hostile in ['"', "AND OR NOT", "a*", "(unbalanced", "NEAR("]:
        assert isinstance(store.search_fts(hostile), list)


def test_search_ranks_title_match_highly(store: MemoryStore) -> None:
    store.save("Nix flake setup", "unrelated body")
    store.save("Random note", "mentions nix once")
    result = search(store, "nix")
    assert result.hits[0].memory.slug == "nix-flake-setup"


def test_search_fuses_signals(store: MemoryStore) -> None:
    """A memory matching on both title and body should outrank one matching once."""
    store.save("Nix", "nix nix nix")
    store.save("Other", "nix")
    result = search(store, "nix")
    assert result.hits[0].memory.slug == "nix"
    assert len(result.hits[0].signals) >= 2


def test_search_matches_tags(store: MemoryStore) -> None:
    store.save("Something", "body with no keyword", ["nixos"])
    result = search(store, "nixos")
    assert any(hit.memory.slug == "something" for hit in result.hits)


def test_search_expands_to_parent(store: MemoryStore) -> None:
    store.save("Project pa", "the parent")
    store.save("Pa roadmap", "child note about scheduling", parent="project-pa")
    result = search(store, "scheduling")
    slugs = [hit.memory.slug for hit in result.hits]
    assert "pa-roadmap" in slugs
    assert "project-pa" in slugs
    parent_hit = next(hit for hit in result.hits if hit.memory.slug == "project-pa")
    assert parent_hit.via == "parent of pa-roadmap"


def test_search_expands_to_children(store: MemoryStore) -> None:
    store.save("Project pa", "the parent")
    store.save("Pa roadmap", "child", parent="project-pa")
    result = search(store, "parent")
    slugs = [hit.memory.slug for hit in result.hits]
    assert "pa-roadmap" in slugs


def test_search_expands_to_links(store: MemoryStore) -> None:
    store.save("Target", "linked target")
    store.save("Source", "source note", links=["target"])
    result = search(store, "source")
    slugs = [hit.memory.slug for hit in result.hits]
    assert "target" in slugs


def test_search_expansion_can_be_disabled(store: MemoryStore) -> None:
    store.save("Project pa", "the parent")
    store.save("Pa roadmap", "child", parent="project-pa")
    result = search(store, "parent", expand=False)
    assert [hit.memory.slug for hit in result.hits] == ["project-pa"]


def test_search_does_not_duplicate_expanded_hits(store: MemoryStore) -> None:
    store.save("Project pa", "parent")
    store.save("Pa roadmap", "child", parent="project-pa")
    result = search(store, "pa")
    slugs = [hit.memory.slug for hit in result.hits]
    assert len(slugs) == len(set(slugs))


def test_provenance_counts(store: MemoryStore) -> None:
    store.save("A", "a", ["x"])
    store.save("B", "b", ["y"], links=["a"])
    result = search(store, "a")
    assert result.provenance.memories_searched == 2
    assert result.provenance.tags_searched == 2
    assert result.provenance.links_searched == 1


def test_provenance_describes_itself(store: MemoryStore) -> None:
    store.save("A", "a")
    description = search(store, "a").provenance.describe()
    assert "searched 1 memories" in description
    assert "tags" in description
    assert "links" in description


def test_empty_query_returns_nothing(store: MemoryStore) -> None:
    store.save("A", "a")
    result = search(store, "   ")
    assert len(result) == 0
    assert result.provenance.memories_searched == 1


def test_no_match_still_reports_provenance(store: MemoryStore) -> None:
    store.save("A", "a")
    result = search(store, "zzzznotfound")
    assert len(result) == 0
    assert "searched 1 memories" in result.provenance.describe()


def test_rrf_prefers_agreement() -> None:
    """A slug in two lists must beat a slug that is first in only one."""
    from jpe.memory.retrieval import _rrf

    fused = _rrf({"fts": ["a", "b"], "tag": ["b"]})
    assert fused["b"][0] > fused["a"][0]
    assert set(fused["b"][1]) == {"fts", "tag"}


def test_rrf_constant_is_used() -> None:
    from jpe.memory.retrieval import _rrf

    fused = _rrf({"fts": ["a"]})
    assert fused["a"][0] == pytest.approx(1.0 / (RRF_K + 1))


def test_provenance_defaults() -> None:
    provenance = Provenance()
    assert provenance.memories_searched == 0
    assert provenance.fts_available is True
