from contextlib import suppress
from pathlib import Path
from os import walk


def file_switch(path: Path) -> None:
    if path.exists():
        path.unlink()
    else:
        path.touch()


def remove_empty_directories(path: Path) -> None:
    exclude = {
        "\\.",
        "\\_",
        "\\__",
    }
    walker = (
        path.walk(top_down=False)
        if hasattr(path, "walk")
        else (
            (Path(dir_path), dir_names, file_names)
            for dir_path, dir_names, file_names in walk(path, topdown=False)
        )
    )
    for dir_path, dir_names, file_names in walker:
        if any(i in str(dir_path) for i in exclude):
            continue
        if not dir_names and not file_names:
            with suppress(OSError):
                dir_path.rmdir()
