from __future__ import annotations

import contextlib
import queue
from typing import Any, List

from .browser import create_driver


class DriverPool:
    """헤드리스 드라이버 N개를 큐로 빌려주는 단순 풀.

    Selenium WebDriver는 thread-safe가 아니므로 borrow() 컨텍스트로
    "한 시점에 한 스레드만 한 드라이버" 규칙을 강제한다.
    """

    def __init__(self, size: int, headless: bool, browser: str = "auto"):
        self._drivers: List[Any] = []
        self._queue: "queue.Queue[Any]" = queue.Queue()
        for _ in range(size):
            d = create_driver(headless=headless, browser=browser)
            self._drivers.append(d)
            self._queue.put(d)

    @contextlib.contextmanager
    def borrow(self):
        d = self._queue.get()
        try:
            yield d
        finally:
            self._queue.put(d)

    def close_all(self) -> None:
        for d in self._drivers:
            try:
                d.quit()
            except Exception:
                pass
