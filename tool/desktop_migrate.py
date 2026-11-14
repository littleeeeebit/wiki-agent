"""Copy a stopped source installation before converting its local settings."""

import hashlib
import json
from pathlib import Path
import shutil
import threading
import time

from common import is_junction

CONTROL = {"server.lock", "main.log", "main.err.log", "errors.jsonl"}


def resume_import(data: Path) -> Path | None:
    """Finish an interrupted copy before workflow recovery can read a partial import."""
    marker = data / "import-pending.json"
    if not marker.exists():
        return None
    pending = json.loads(marker.read_text(encoding="utf-8"))
    backup = (data / pending["backup"]).resolve()
    if (data / "migrations").resolve() not in backup.parents:
        raise ValueError("가져오기 복구 사본 경로가 올바르지 않다")
    hashes = json.loads((backup.parent / "sha256.json").read_text(encoding="utf-8"))
    source = Path(json.loads((backup.parent / "complete.json").read_text(encoding="utf-8"))["source"])
    # Validate every byte and the settings conversion before the first destination write.
    converted = None
    for name, expected in hashes.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("가져오기 파일 경로가 올바르지 않다")
        if hashlib.sha256((backup / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"복구 사본이 바뀌었다: {name}")
        if name == ".chat-local.json":
            saved = json.loads((backup / name).read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or any(not isinstance(v, str) for v in saved.values()):
                raise ValueError("기존 .chat-local.json 형식을 확인하세요")
            workspace = Path(saved.get("workspace", "..")).expanduser()
            saved["workspace"] = str((source / workspace).resolve())
            converted = json.dumps(saved, ensure_ascii=False) + "\n"
    for name in hashes:
        # The running server owns these files. Previous logs remain in the recoverable copy.
        if name.startswith(".wiki/") or Path(name).parent == Path("raw") and Path(name).name in CONTROL:
            continue
        target = data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".importing")
        if name == ".chat-local.json":
            temporary.write_text(converted, encoding="utf-8")
        else:
            shutil.copyfile(backup / name, temporary)
        temporary.replace(target)
    marker.unlink()
    return backup


def import_source(source: Path, data: Path, halt: threading.Event, output) -> Path:
    from main.runtime import server_owner

    source, data = source.resolve(), data.resolve()
    if (data / "import-pending.json").exists():
        return resume_import(data)
    if not (source / "tool/main/__main__.py").is_file() or source == data or source in data.parents or data in source.parents:
        raise ValueError("별도의 기존 wiki-agent 소스 설치 폴더를 선택하세요")
    # An import never merges two histories or replaces records from an installed app.
    busy = [p for p in (data / "raw").rglob("*") if p.is_file()
            and p.name not in CONTROL]
    if busy or (data / ".chat-local.json").exists() or (data / ".env").exists():
        raise ValueError("새 앱에 이미 설정 또는 기록이 있다. 별도의 빈 사용자 데이터 폴더에서 가져오세요")
    backup = data / "migrations" / str(time.time_ns()) / "original"
    backup.mkdir(parents=True)
    names = [Path("raw"), Path(".chat-local.json"), Path(".env"), Path(".wiki")]
    hashes = {}
    with server_owner(source / "raw"):
        for relative in names:
            origin = source / relative
            paths = [origin, *origin.rglob("*")] if origin.is_dir() else [origin]
            for path in paths:
                if halt.is_set():
                    raise InterruptedError("가져오기를 취소했다. 원본은 변경하지 않았다")
                if path.is_symlink() or is_junction(path):
                    raise ValueError(f"연결된 폴더/파일은 가져오지 않는다: {path}")
                if not path.is_file() or path.name == "server.lock":
                    continue
                name = path.relative_to(source)
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                hashes[name.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
                if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[name.as_posix()]:
                    raise ValueError(f"복사 중 원본이 바뀌었다: {name}. 기존 앱과 hook 세션을 닫고 다시 시도하세요")
                output(f"복사 중: {name}")
        (backup.parent / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
        # The complete recoverable copy exists before any conversion or destination write.
        (backup.parent / "complete.json").write_text(json.dumps({"source": str(source)}) + "\n", encoding="utf-8")
        if halt.is_set():
            raise InterruptedError("가져오기를 취소했다. 복구 사본을 보존했다")
        marker = data / "import-pending.json"
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps({"backup": str(backup.relative_to(data))}) + "\n", encoding="utf-8")
        temporary.replace(marker)
        resume_import(data)
    return backup
