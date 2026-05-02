from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
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
CHROME_PROVIDER_ENV = "MUSINSA_CHROME_PROVIDER"
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


def _is_colab() -> bool:
    try:
        import google.colab  # type: ignore[import-not-found]  # noqa: F401
        return True
    except Exception:
        return False


def _chrome_binary_candidates() -> list[str]:
    return _existing_executables(
        _env_executable(CHROME_BINARY_ENV),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        *CHROME_BINARY_CANDIDATES,
    )


def _chromedriver_candidates() -> list[str]:
    return _existing_executables(
        _env_executable(CHROMEDRIVER_ENV),
        shutil.which("chromedriver"),
        *CHROMEDRIVER_CANDIDATES,
    )


def _version_output(path: str | None) -> str:
    if not path:
        return "not found"
    try:
        result = subprocess.run(
            [path, "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or f"exit={result.returncode}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _read_tail(path: Path, max_chars: int = 2000) -> str:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def _build_chrome_options(chrome_binary: str | None, headless_arg: str | None) -> Any:
    options = ChromeOptions()
    if chrome_binary:
        options.binary_location = chrome_binary
    if headless_arg:
        options.add_argument(headless_arg)

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=ko-KR")
    options.add_argument(f"--user-data-dir={tempfile.mkdtemp(prefix='musinsa-chrome-')}")
    options.set_capability("pageLoadStrategy", "eager")
    return options


def _create_colab_selenium_driver(headless: bool) -> Any:
    try:
        import google_colab_selenium as gs  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Colab 드라이버 provider를 쓰려면 `%pip install google-colab-selenium`이 필요합니다."
        ) from exc

    chrome_binary = _chrome_binary_candidates()[0] if _chrome_binary_candidates() else None
    headless_arg = "--headless=new" if headless else None
    options = _build_chrome_options(chrome_binary, headless_arg)

    try:
        driver = gs.Chrome(options=options)
    except TypeError:
        driver = gs.Chrome()
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def _resolve_browser(browser: str) -> str:
    """`auto` 라면 사용 가능한 드라이버를 골라 'firefox' 또는 'chrome'을 반환."""
    browser = (browser or "auto").lower()
    if browser in ("firefox", "chrome"):
        return browser
    if browser != "auto":
        raise RuntimeError(f"지원하지 않는 브라우저: {browser}")

    if SNAP_GECKODRIVER.exists() or shutil.which("geckodriver"):
        return "firefox"
    chrome_binary = _chrome_binary_candidates()[0] if _chrome_binary_candidates() else None
    chromedriver = _chromedriver_candidates()[0] if _chromedriver_candidates() else None
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

    provider = os.environ.get(CHROME_PROVIDER_ENV, "auto").strip().lower()
    if provider in ("colab", "google-colab-selenium"):
        return _create_colab_selenium_driver(headless)
    if provider not in ("auto", "selenium", "webdriver"):
        raise RuntimeError(
            f"{CHROME_PROVIDER_ENV}={provider} 는 지원하지 않습니다. "
            "auto, selenium, colab 중 하나를 사용하세요."
        )
    if provider == "auto" and _is_colab():
        try:
            return _create_colab_selenium_driver(headless)
        except RuntimeError as exc:
            log_warn(f"google-colab-selenium provider 사용 실패, Selenium 직접 실행으로 전환: {exc}")

    chrome_binary = _chrome_binary_candidates()[0] if _chrome_binary_candidates() else None

    if not chrome_binary:
        raise RuntimeError(
            "Chrome/Chromium 실행 파일을 찾지 못했습니다. "
            f"Colab에서 찾은 경로를 os.environ['{CHROME_BINARY_ENV}']에 넣거나, "
            "Colab에서 chromium 또는 chromium-browser를 설치하세요."
        )

    chromedriver_paths = _chromedriver_candidates()

    if not chromedriver_paths:
        raise RuntimeError(
            "chromedriver를 찾지 못했습니다. "
            f"Colab에서 찾은 경로를 os.environ['{CHROMEDRIVER_ENV}']에 넣거나, "
            "Colab에서 chromium-driver 또는 chromium-chromedriver를 설치하세요."
        )

    errors: list[str] = []
    headless_args = ["--headless=new", "--headless"] if headless else [None]
    for headless_arg in headless_args:
        for chromedriver_path in chromedriver_paths:
            log_path = Path(tempfile.gettempdir()) / f"musinsa-chromedriver-{os.getpid()}.log"
            try:
                service = ChromeService(
                    executable_path=chromedriver_path,
                    log_output=str(log_path),
                )
                driver = webdriver.Chrome(
                    service=service,
                    options=_build_chrome_options(chrome_binary, headless_arg),
                )
                driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
                return driver
            except WebDriverException as exc:
                errors.append(
                    "\n".join(
                        [
                            f"driver={chromedriver_path}",
                            f"driver_version={_version_output(chromedriver_path)}",
                            f"chrome={chrome_binary}",
                            f"chrome_version={_version_output(chrome_binary)}",
                            f"headless={headless_arg or 'off'}",
                            f"error={exc}",
                            f"log={_read_tail(log_path)}",
                        ]
                    )
                )

    detail = "\n\n---\n\n".join(errors[-3:])
    raise RuntimeError(
        "Chrome WebDriver 시작에 실패했습니다. Colab이면 "
        "`%pip install google-colab-selenium` 후 "
        "`os.environ['MUSINSA_CHROME_PROVIDER']='colab'`을 설정해서 실행하세요.\n\n"
        f"{detail}"
    )


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
