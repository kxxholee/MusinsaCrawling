"""rich 기반 단일 Console + Progress. 로그가 진행바를 깨뜨리지 않도록 모듈 전역 인스턴스 공유."""
from __future__ import annotations

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


def _is_jupyter() -> bool:
    try:
        from IPython import get_ipython  # type: ignore[import-not-found]
        return get_ipython() is not None
    except Exception:
        return False


console: Console = Console(force_jupyter=True) if _is_jupyter() else Console()


def make_progress() -> Progress:
    """카테고리/상품/스크롤 task를 한 컨텍스트에 동시에 띄우는 Progress."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


def log_info(msg: str) -> None:
    console.log(msg)


def log_ok(msg: str) -> None:
    console.log(f"[green]{msg}[/]")


def log_warn(msg: str) -> None:
    console.log(f"[yellow][경고][/] {msg}")


def log_error(msg: str) -> None:
    console.log(f"[bold red][오류][/] {msg}")


def rule(msg: str = "") -> None:
    console.rule(msg)
