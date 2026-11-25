"""Review root configuration read from ``diffdesk.toml``."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

CONFIG_NAME = "diffdesk.toml"
DEFAULT_PATHS = {
    "subjects": "subjects",
    "reviews": "reviews.json",
    "edits": "edits",
    "cache": ".diffdesk-cache",
}
DEFAULT_UI: dict[str, Any] = {"theme": "system", "editor": "codemirror"}


class ConfigError(ValueError):
    """``diffdesk.toml`` holds a value that diffdesk cannot use."""


@dataclass(frozen=True)
class Config:
    """Settings of one review root.

    Attributes
    ----------
    root : Path
        Absolute path of the review root.
    title : str
        Title shown in the browser. Default: the name of the root folder.
    subjects, reviews, edits, cache : Path
        Absolute paths inside `root`.
    packages : dict
        Package name to the list of package names it depends on.
    ui : dict
        Default values for the settings panel of the browser.
    """

    root: Path
    title: str
    subjects: Path
    reviews: Path
    edits: Path
    cache: Path
    packages: dict[str, list[str]]
    ui: dict[str, Any]


def _rel_path(key: str, value: Any) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[paths] {key} must be a non-empty string: {value!r}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("~") or "\\" in value or ".." in pure.parts:
        raise ConfigError(f"[paths] {key} must be relative to the root, without '..': {value!r}")
    return pure


def _str_list(where: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where} must be a list of strings: {value!r}")
    return list(value)


def load_config(root: Path) -> Config:
    """Read ``<root>/diffdesk.toml``, or use the defaults if it is absent.

    Parameters
    ----------
    root : Path
        The review root folder.

    Returns
    -------
    Config
        The settings, with every path made absolute.

    Raises
    ------
    ConfigError
        If the file is not valid TOML, a value has the wrong type, or a path
        is absolute or contains ``..``.
    """
    root = root.resolve()
    data: dict[str, Any] = {}
    path = root / CONFIG_NAME
    if path.is_file():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc

    title = data.get("title", root.name)
    if not isinstance(title, str):
        raise ConfigError(f"title must be a string: {title!r}")

    paths_table = data.get("paths", {})
    if not isinstance(paths_table, dict):
        raise ConfigError("[paths] must be a table")
    paths = {
        key: root / _rel_path(key, paths_table.get(key, default))
        for key, default in DEFAULT_PATHS.items()
    }

    packages_table = data.get("packages", {})
    if not isinstance(packages_table, dict):
        raise ConfigError("[packages] must be a table")
    packages: dict[str, list[str]] = {}
    for name, table in packages_table.items():
        if not isinstance(table, dict):
            raise ConfigError(f"[packages.{name}] must be a table")
        packages[name] = _str_list(f"[packages.{name}] depends_on", table.get("depends_on", []))

    ui_table = data.get("ui", {})
    if not isinstance(ui_table, dict):
        raise ConfigError("[ui] must be a table")

    return Config(
        root=root,
        title=title,
        subjects=paths["subjects"],
        reviews=paths["reviews"],
        edits=paths["edits"],
        cache=paths["cache"],
        packages=packages,
        ui={**DEFAULT_UI, **ui_table},
    )
