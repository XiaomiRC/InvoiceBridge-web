from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile, BadZipFile
import re
import shutil


class UnsafeZipMember(ValueError):
    pass


def is_unsafe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    return normalized.startswith("/") or bool(re.match(r"^[A-Za-z]:", normalized)) or ".." in path.parts


def safe_extract_zip(zip_path: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    try:
        with ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if is_unsafe_member(info.filename):
                    raise UnsafeZipMember(info.filename)
                target = (destination / info.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise UnsafeZipMember(info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(target)
    except BadZipFile as exc:
        raise ValueError(f"Invalid ZIP file: {zip_path}") from exc
    return extracted

