"""JSON file-based Profile storage."""

import json
import pathlib

from profile.profile_schema import Profile


class ProfileStore:
    """Store and retrieve Profiles as JSON files.

    Args:
        base_dir: Directory where profile JSON files are stored.
    """

    def __init__(self, base_dir: str | pathlib.Path) -> None:
        self.base_dir = pathlib.Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, profile: Profile, name: str) -> pathlib.Path:
        """Save a Profile to a JSON file."""
        filepath = self.base_dir / f"{name}.json"
        with open(filepath, "w") as f:
            f.write(profile.model_dump_json(indent=2))
        return filepath

    def load(self, name: str) -> Profile:
        """Load a Profile from a JSON file.

        Raises:
            FileNotFoundError: If the file doesn't exist.
        """
        filepath = self.base_dir / f"{name}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Profile not found: {filepath}")
        with open(filepath) as f:
            data = json.load(f)
        return Profile.model_validate(data)

    def list(self) -> list[str]:
        """List all stored profile names."""
        names = [f.stem for f in self.base_dir.glob("*.json")]
        return sorted(names)
