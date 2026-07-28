"""Tests for ProfileStore."""

import tempfile

import pytest

from profile.profile_schema import Profile, ProfileMetadata, TopdownL1
from profile.profile_store import ProfileStore


def test_save_and_load_profile() -> None:
    store = ProfileStore(base_dir=tempfile.mkdtemp())
    profile = Profile(
        metadata=ProfileMetadata(customer="acme", date="2026-07-27"),
        topdown=TopdownL1(
            frontend_bound=0.25, backend_bound=0.40, bad_speculation=0.10, retiring=0.25
        ),
    )
    path = store.save(profile, name="test_profile")
    assert path.exists()
    loaded = store.load(name="test_profile")
    assert loaded.metadata.customer == "acme"
    assert loaded.topdown is not None
    assert loaded.topdown.frontend_bound == 0.25


def test_list_profiles() -> None:
    store = ProfileStore(base_dir=tempfile.mkdtemp())
    p1 = Profile(metadata=ProfileMetadata(customer="a", date="2026-07-27"))
    p2 = Profile(metadata=ProfileMetadata(customer="b", date="2026-07-28"))
    store.save(p1, name="profile_a")
    store.save(p2, name="profile_b")
    names = store.list()
    assert "profile_a" in names
    assert "profile_b" in names


def test_load_nonexistent_raises() -> None:
    store = ProfileStore(base_dir=tempfile.mkdtemp())
    with pytest.raises(FileNotFoundError):
        store.load(name="nonexistent")
