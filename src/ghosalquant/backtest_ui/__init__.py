from pathlib import Path


def project_root() -> Path:
    """The source checkout when run from one, otherwise the directory the UI was started in."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "pyproject.toml").exists() else Path.cwd()
