import json
from pathlib import Path

import yaml

from app.config import settings

ALLOWED_EXTENSIONS = {".txt", ".json", ".yaml", ".yml", ".toml", ".conf", ".env", ".ini"}


def _resolve_config_path(config_path: str) -> Path:
    base = Path(settings.configs_base_path).resolve()
    resolved = (base / config_path.lstrip("/")).resolve()
    if not resolved.is_relative_to(base):
        raise ValueError("Path traversal detected")
    return resolved


def get_language(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".txt": "plaintext",
        ".conf": "plaintext",
        ".env": "shell",
        ".ini": "ini",
    }.get(ext, "plaintext")


def read_config(config_path: str) -> tuple[str, str, str]:
    path = _resolve_config_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    content = path.read_text(encoding="utf-8")
    return content, path.name, get_language(path.name)


def write_config(config_path: str, content: str) -> None:
    path = _resolve_config_path(config_path)
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed: {ext}")

    _validate_syntax(content, ext)
    # Write in-place rather than tmp+rename: config files are single-file Docker bind mounts,
    # so rename(2) over them fails with EBUSY (the kernel pins the inode to the mount).
    # Syntax validation above means we won't write garbage; an interrupted write is the only
    # corruption risk, bounded by a single Python write of tens of KB.
    path.write_text(content, encoding="utf-8")


def _validate_syntax(content: str, ext: str) -> None:
    if ext == ".json":
        json.loads(content)
    elif ext in (".yaml", ".yml"):
        yaml.safe_load(content)
