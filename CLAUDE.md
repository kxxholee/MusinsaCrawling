# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

무신사 스탠다드 여성 상품(브랜드 `musinsastandardwoman`, 필터 `gf=F`)을 Selenium + BeautifulSoup으로 동적 수집해 보고서형 `SKU_요약`과 검증용 raw 시트들을 포함한 엑셀을 만드는 데모입니다. Python 3.11, 패키지는 `uv` 관리.

실제 진입점은 `python -m musinsa` (또는 `from musinsa.pipeline import main`) 입니다. [mgen.py](mgen.py)는 역호환 shim이고, [main.py](main.py)는 `uv init` 의 잔재이므로 사용하지 않습니다.

## 자주 쓰는 명령어

```bash
# 최초 셋업
uv sync

# 스모크 테스트 (카테고리당 10개, 헤드리스)
uv run python -m musinsa --max-products 10 --headless

# 전체 수집
uv run python -m musinsa --max-products 0 --headless

# 상세 옵션 수집 건너뛰기 (목록만, 훨씬 빠름)
uv run python -m musinsa --max-products 10 --headless --skip-options

# Chrome 강제 사용 (Colab 등)
uv run python -m musinsa --max-products 10 --headless --browser chrome
```

CLI 플래그: `--output`, `--max-products` (0이면 전체), `--max-scrolls`, `--delay` (상세 페이지 사이 대기 시간, 초 단위 — 너무 낮추지 말 것), `--headless`, `--skip-options`, `--workers`, `--browser` (`auto`/`firefox`/`chrome`).

Jupyter/Colab은 `from musinsa.pipeline import main; out_path = main(max_products=20, headless=True, browser="chrome")` 형태로 직접 호출하고 반환된 `Path` 로 결과 파일에 접근합니다.

테스트, 린터, 포매터는 설정되어 있지 않습니다.

## 아키텍처

`src/musinsa/` 패키지의 선형 파이프라인입니다. 데이터 흐름은 다음과 같습니다.

```
discover_category_tree (categories.py) → 동적 카테고리 트리
   ↓
collect_products_from_category (listing.py) → ProductRow[]
   ↓
collect_detail_and_options (detail.py) → OptionRow[], SkuRow[]
   ↓
save_excel (excel.py) → SKU_요약 + raw 시트 엑셀
```

오케스트레이션은 [pipeline.py](src/musinsa/pipeline.py) `main(**kwargs)` 가, CLI 진입점은 [cli.py](src/musinsa/cli.py) `entrypoint()` 가, 병렬 드라이버 풀은 [driver_pool.py](src/musinsa/driver_pool.py) 의 `DriverPool` 이, 진행바·로그는 [logger.py](src/musinsa/logger.py) 의 단일 rich `Console` + `Progress` 가 담당합니다.

스키마와 엑셀 컬럼 순서는 [schemas.py](src/musinsa/schemas.py) 의 세 dataclass — `ProductRow`, `OptionRow`, `SkuRow` 가 정의하며 컬럼 리스트는 `__annotations__` 에서 그대로 끌어옵니다.

### 수집 전략: 일부러 넓게 긁고 파이썬에서 거른다

무신사 DOM은 자주 바뀌기 때문에 JS `evaluate` 블록은 의도적으로 후보를 과수집합니다.

- `collect_products_from_category` — Selenium으로 목록 페이지를 스크롤하면서 매 DOM 스냅샷을 BeautifulSoup으로 파싱하고, product-id 패턴 (`/products/\d+`, `/goods/\d+`, `goods_no=`, `goodsNo=`) 에 매치되는 모든 `<a>` 를 누적합니다. 최종 DOM만 보지 않기 때문에 가상 스크롤에서 이전 카드가 사라져도 덜 놓칩니다.
- `collect_option_text_candidates` — 상세 페이지에서 Selenium으로 렌더링된 DOM을 받은 뒤 BeautifulSoup으로 `button`, `option`, `li`, `[role=option]`, 클래스/데이터 속성에 `option` 이 들어간 모든 노드를 쓸어담고 80자 이하로 제한.

정리·색상/사이즈 추정·중복 제거는 파이썬 단계에서 합니다 ([text_parse.py](src/musinsa/text_parse.py) 의 `guess_product_name`, `extract_size_from_text`, `find_color_words`, [detail.py](src/musinsa/detail.py) 의 `parse_options_from_candidates`). 사이트 구조가 깨지면 **dataclass 나 엑셀 레이어가 아니라 위 함수들과 셀렉터부터 손봐야 합니다.**

### 옵션 파싱 전략

`parse_options_from_candidates` 는 한 라벨 안에서 실제 "색상+사이즈" 조합이 같이 확인되는 후보만 SKU로 계산합니다. 구매 옵션 영역 후보를 우선하고, Q&A/리뷰/후기/착용 예시/날짜 텍스트는 노이즈로 제외합니다. 색상 후보와 사이즈 후보를 따로 모아 카르테시안 곱으로 SKU를 만들지 않습니다. 조합을 확정하지 못한 후보는 `options_raw` 검증용 행으로만 남기며 `sku_result`와 `model_summary.sku_count`에는 반영하지 않습니다.

### 고정 설정값 ([config.py](src/musinsa/config.py))

- `GENDER_FILTER = "F"` 는 `ensure_gender_filter_url` / `build_category_url` 을 통해 카테고리 URL, 상품 상세 URL, raw 시트 URL에 항상 들어갑니다. 데모 자체가 여성 한정이라는 점을 이해하지 못한 채 매개변수화하지 마세요.
- 카테고리 트리는 [categories.py](src/musinsa/categories.py) 의 `discover_category_tree` 가 무신사 네비게이션을 매번 긁어 동적으로 발견합니다. `MAJOR_CODE_SEEDS` 는 menu_tab 발견에 실패했을 때만 쓰는 폴백입니다. 상품의 raw 카테고리는 목록 수집 시점에 서브카테고리 라벨로 그대로 부여됩니다.
- 색상은 `COLOR_ALIASES` (표준키 → alias 리스트) 사전으로 정규화됩니다. `find_color_words` 는 매치된 alias를 표준키로 변환하고 같은 위치를 짧은 alias가 다시 잡지 않도록 긴 alias부터 매칭합니다. 색상 어휘를 늘리려면 이 사전에 항목을 추가하세요.
- 사이즈는 `SIZE_WORD_RE` + `NUMERIC_SIZE_WHITELIST` (인치/한국 사이즈만) + `SIZE_NOISE_WORDS` (원/만/개/리뷰 등 노이즈 차단) 3단으로 거릅니다. "리뷰 106만 개" 같은 본문 텍스트가 사이즈 후보로 통과되지 않도록 하기 위함입니다.
- 가격은 `PRICE_RE` 가 "원" 을 반드시 요구하고, `PRICE_MIN/MAX` 범위 (1,000 ~ 5,000,000 원) 만 통과합니다. 카드 본문의 모든 가격 후보 중 **최댓값** 을 본가 후보로 채택합니다 (쿠폰가가 본가보다 먼저 노출되는 경우가 많기 때문).

### 브라우저 / 타임아웃

[browser.py](src/musinsa/browser.py) 의 `create_driver(headless, browser="auto")` 가 Firefox/Chrome 양쪽을 지원합니다. `auto`는 geckodriver → chromedriver 순서로 환경을 탐지하고, snap firefox 경로(`/snap/firefox/...`)를 우선 봅니다. 두 분기 모두 `pageLoadStrategy="eager"` 와 30초 타임아웃을 씁니다. `safe_goto` 는 `TimeoutException` 발생 시 `window.stop()` 으로 잔여 다운로드를 끊고 `document.readyState` 가 살아 있으면 부분 로드 상태로 진행, 그래도 실패면 한 번 재시도합니다.

### 진행바·로그

[logger.py](src/musinsa/logger.py) 가 모듈 전역 `rich.Console` 하나와 `make_progress()` 컨텍스트를 노출합니다. `tqdm`, `print`, `safe_print` 는 모두 제거됐고, 모든 로그는 `log_info` / `log_ok` / `log_warn` / `log_error` / `rule` 로 통일됐습니다. `Progress` 는 메인 스레드에서만 `advance` 하고 워커 스레드는 결과만 반환합니다 (rich Console 자체는 스레드 안전).

### 엑셀 출력

[excel.py](src/musinsa/excel.py) 의 `save_excel` 은 시트 순서를 `SKU_요약`, `model_summary`, `products_raw`, `options_raw`, `sku_result`, `category_master` 로 고정합니다. `SKU_요약`은 A열 대분류, B/F/G/H/I열 중분류 단위로 병합하고, raw 시트들은 `adjust_excel_format` 이 첫 행 고정·자동 필터·헤더 스타일·컬럼 폭을 적용합니다. legacy `summary` 시트는 만들지 않습니다.
