from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

from .pipeline import main


CONFIG_PATH = Path("commands.yaml")
MAIN_KEYS = {
    "output",
    "max_products",
    "max_scrolls",
    "delay",
    "headless",
    "skip_options",
    "workers",
    "browser",
}


def _load_yaml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"{path} 파일을 찾지 못했습니다. 로컬 실행 옵션은 터미널 플래그 대신 "
            f"{path}에서 설정하세요."
        )

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, Mapping):
        raise SystemExit(f"{path} 최상위 값은 YAML mapping이어야 합니다.")
    return data


def _select_command(data: Mapping[str, Any], path: Path) -> dict[str, Any]:
    if "commands" not in data:
        command = dict(data)
        command_name = None
    else:
        commands = data.get("commands")
        if not isinstance(commands, Mapping):
            raise SystemExit(f"{path}의 commands 값은 YAML mapping이어야 합니다.")

        command_name = str(data.get("default", "default"))
        if command_name not in commands:
            available = ", ".join(str(name) for name in commands)
            raise SystemExit(
                f"{path}에서 default={command_name!r} 명령을 찾지 못했습니다. "
                f"사용 가능: {available}"
            )

        command_value = commands[command_name]
        if not isinstance(command_value, Mapping):
            raise SystemExit(f"{path}의 commands.{command_name} 값은 YAML mapping이어야 합니다.")
        command = dict(command_value)

    unknown_keys = sorted(set(command) - MAIN_KEYS)
    if unknown_keys:
        prefix = f"commands.{command_name}" if command_name else "최상위"
        raise SystemExit(
            f"{path}의 {prefix}에 알 수 없는 옵션이 있습니다: {', '.join(unknown_keys)}"
        )

    return command


def load_command_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """commands.yaml에서 실행 옵션을 읽어 pipeline.main kwargs로 반환합니다."""
    data = _load_yaml(path)
    return _select_command(data, path)


def entrypoint() -> None:
    if len(sys.argv) > 1:
        received = " ".join(sys.argv[1:])
        raise SystemExit(
            "터미널 플래그는 더 이상 사용하지 않습니다. "
            f"{CONFIG_PATH}를 수정한 뒤 `uv run python -m musinsa`로 실행하세요. "
            f"받은 인자: {received}"
        )

    main(**load_command_config())
