from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait

try:
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    _CHROME_AVAILABLE = True
except ImportError:
    ChromeOptions = None  # type: ignore[assignment]
    ChromeService = None  # type: ignore[assignment]
    _CHROME_AVAILABLE = False

from .logger import log_warn
from .utils import ensure_gender_filter_url


PAGE_LOAD_TIMEOUT = 30
CHROME_BINARY_ENV = "MUSINSA_CHROME_BINARY"
CHROMEDRIVER_ENV = "MUSINSA_CHROMEDRIVER"
SNAP_FIREFOX = Path("/snap/firefox/current/usr/lib/firefox/firefox")
SNAP_GECKODRIVER = Path("/snap/firefox/current/usr/lib/firefox/geckodriver")
CHROME_BINARY_CANDIDATES = (
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/opt/google/chrome/chrome",
)
CHROMEDRIVER_CANDIDATES = (
    "/usr/bin/chromedriver",
    "/usr/lib/chromium/chromedriver",
    "/usr/lib/chromium-browser/chromedriver",
)


def _first_existing_executable(*paths: str) -> str | None:
    for path in paths:
        if path and Path(path).exists():
            return path
    return None


def _env_executable(name: str) -> str | None:
    path = os.environ.get(name)
    if not path:
        return None
    if Path(path).exists():
        return path
    raise RuntimeError(f"{name}={path} 경로가 존재하지 않습니다.")


def _existing_executables(*paths: str | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not path or path in seen or not Path(path).exists():
            continue
        seen.add(path)
        result.append(path)
    return result


def _resolve_browser(browser: str) -> str:
    """`auto` 라면 사용 가능한 드라이버를 골라 'firefox' 또는 'chrome'을 반환."""
    browser = (browser or "auto").lower()
    if browser in ("firefox", "chrome"):
        return browser
    if browser != "auto":
        raise RuntimeError(f"지원하지 않는 브라우저: {browser}")

    if SNAP_GECKODRIVER.exists() or shutil.which("geckodriver"):
        return "firefox"
    chrome_binary = _env_executable(CHROME_BINARY_ENV)
    chrome_binary = chrome_binary or shutil.which("google-chrome") or shutil.which("chromium")
    chrome_binary = chrome_binary or _first_existing_executable(*CHROME_BINARY_CANDIDATES)
    chromedriver = _env_executable(CHROMEDRIVER_ENV)
    chromedriver = chromedriver or shutil.which("chromedriver") or _first_existing_executable(*CHROMEDRIVER_CANDIDATES)
    if _CHROME_AVAILABLE and (chromedriver or chrome_binary):
        return "chrome"
    return "firefox"


def _create_firefox_driver(headless: bool) -> Any:
    options = FirefoxOptions()
    if headless:
        options.add_argument("--headless")
    options.set_preference("intl.accept_languages", "ko-KR,ko,en-US,en")
    options.set_preference(
        "general.useragent.override",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
    )
    options.set_capability("pageLoadStrategy", "eager")

    if SNAP_FIREFOX.exists():
        options.binary_location = str(SNAP_FIREFOX)

    geckodriver_path = (
        str(SNAP_GECKODRIVER) if SNAP_GECKODRIVER.exists() else shutil.which("geckodriver")
    )
    service = FirefoxService(executable_path=geckodriver_path) if geckodriver_path else None
    driver = webdriver.Firefox(service=service, options=options)
    driver.set_window_size(1440, 1200)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def _create_chrome_driver(headless: bool) -> Any:
    if not _CHROME_AVAILABLE:
        raise RuntimeError("selenium.webdriver.chrome 가 import 되지 않습니다.")

    options = ChromeOptions()

    chrome_binary = _env_executable(CHROME_BINARY_ENV)
    chrome_binary = chrome_binary or (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or _first_existing_executable(*CHROME_BINARY_CANDIDATES)
    )

    if chrome_binary:
        options.binary_location = chrome_binary
    else:
        raise RuntimeError(
            "Chrome/Chromium 실행 파일을 찾지 못했습니다. "
            f"Colab에서 찾은 경로를 os.environ['{CHROME_BINARY_ENV}']에 넣거나, "
            "Colab에서 chromium 또는 chromium-browser를 설치하세요."
        )

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-software-rasterizer")

    options.set_capability("pageLoadStrategy", "eager")

    chromedriver_path = _env_executable(CHROMEDRIVER_ENV)
    chromedriver_path = chromedriver_path or shutil.which("chromedriver")
    chromedriver_path = chromedriver_path or _first_existing_executable(*CHROMEDRIVER_CANDIDATES)

    if not chromedriver_path:
        raise RuntimeError(
            "chromedriver를 찾지 못했습니다. "
            f"Colab에서 찾은 경로를 os.environ['{CHROMEDRIVER_ENV}']에 넣거나, "
            "Colab에서 chromium-driver 또는 chromium-chromedriver를 설치하세요."
        )

    service = ChromeService(executable_path=chromedriver_path)

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def create_driver(headless: bool, browser: str = "auto") -> Any:
    """Selenium 드라이버를 생성합니다. browser=`auto`/`firefox`/`chrome`."""
    resolved = _resolve_browser(browser)
    if resolved == "firefox":
        return _create_firefox_driver(headless)
    if resolved == "chrome":
        return _create_chrome_driver(headless)
    raise RuntimeError(f"알 수 없는 브라우저 선택값: {resolved}")


def safe_goto(driver: Any, url: str, timeout_seconds: int = PAGE_LOAD_TIMEOUT) -> bool:
    """페이지 이동. load 타임아웃이면 window.stop() 으로 부분 로드 상태에서 진행하거나 한 번 재시도."""
    url = ensure_gender_filter_url(url)
    for attempt in (1, 2):
        try:
            driver.get(url)
            WebDriverWait(driver, timeout_seconds).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
            time.sleep(1.0)
            return True
        except TimeoutException:
            try:
                driver.execute_script("window.stop();")
                state = driver.execute_script("return document.readyState")
                if state in ("interactive", "complete"):
                    log_warn(f"load 타임아웃 — 부분 로드 상태로 진행: {url}")
                    return True
            except WebDriverException:
                pass
            if attempt == 1:
                log_warn(f"페이지 로딩 시간 초과, 재시도: {url}")
                continue
            log_warn(f"페이지 로딩 시간 초과 (재시도 실패): {url}")
            return False
        except WebDriverException as exc:
            log_warn(f"페이지 이동 실패: {url} / {exc}")
            return False
    return False


def close_common_popups(driver: Any) -> None:
    """자주 보이는 팝업을 닫으려는 보조 함수. 실패해도 크롤링을 계속한다."""
    xpaths = [
        "//button[contains(normalize-space(.), '닫기')]",
        "//button[contains(normalize-space(.), '확인')]",
        "//button[contains(normalize-space(.), '오늘 하루 보지 않기')]",
        "//*[@aria-label='닫기']",
        "//*[@aria-label='close']",
    ]

    for xpath in xpaths:
        try:
            for element in driver.find_elements(By.XPATH, xpath)[:3]:
                if element.is_displayed() and element.is_enabled():
                    element.click()
                    time.sleep(0.3)
                    break
        except Exception:
            pass
