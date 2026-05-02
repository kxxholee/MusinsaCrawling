# 무신사 스탠다드 여성 SKU 생성기

무신사 스탠다드 여성 상품(브랜드 `musinsastandardwoman`, 필터 `gf=F`)을 Selenium + BeautifulSoup으로 동적 수집해 보고서형 `SKU_요약` 시트와 검증용 raw 시트를 함께 담은 엑셀을 생성합니다. Python 3.11, 패키지는 `uv` 관리.

## 설치

```bash
uv sync
```

## 실행

로컬 실행 옵션은 터미널 플래그 대신 `commands.yaml`에서 관리합니다.

```yaml
# commands.yaml
default: smoke

commands:
  smoke:
    output: musinsa_standard_female_sku.xlsx
    max_products: 10
    max_scrolls: 80
    delay: 0.8
    headless: true
    skip_options: false
    workers: 2
    browser: auto
```

`default` 값을 실행할 preset 이름으로 바꾼 뒤 실행합니다.

```bash
uv run python -m musinsa
```

기존 `mgen.py` shim도 그대로 작동합니다:

```bash
uv run python mgen.py
```

### commands.yaml 옵션

- `output` 저장할 엑셀 파일명
- `max_products` 카테고리당 최대 상품 수 (0이면 전체)
- `max_scrolls` 목록 페이지 스크롤 누적 최대 횟수
- `delay` 상세 페이지 사이 대기 시간(초)
- `headless` 브라우저 화면 숨김
- `skip_options` 옵션 수집 생략
- `workers` 병렬 드라이버 개수
- `browser` `auto`, `firefox`, `chrome` 중 하나

## Python에서 직접 호출 (Jupyter / Colab)

```python
from musinsa.pipeline import main

out_path = main(
    output="musinsa_standard_female_sku.xlsx",
    max_products=20,
    max_scrolls=80,
    delay=0.8,
    headless=True,
    skip_options=False,
    workers=2,
    browser="chrome",
)
print(out_path)
```

### Google Colab

```python
REPO_URL = "https://github.com/<user>/<repo>.git"
REPO_DIR = "MusinsaCrawling"

import os

if os.path.isdir(REPO_DIR):
    %cd {REPO_DIR}
    !git pull
else:
    !git clone {REPO_URL} {REPO_DIR}
    %cd {REPO_DIR}

%pip install -q -e . google-colab-selenium

import os
import sys

os.environ["MUSINSA_CHROME_PROVIDER"] = "colab"

print(sys.executable)
print("chrome provider:", os.environ["MUSINSA_CHROME_PROVIDER"])

from musinsa.browser import create_driver
driver = create_driver(headless=True, browser="chrome")
driver.quit()

from musinsa.pipeline import main

out_path = main(
    max_products=20,
    max_scrolls=80,
    delay=0.8,
    headless=True,
    skip_options=False,
    workers=1,
    browser="chrome",
)

from google.colab import files
files.download(str(out_path))
```

Colab은 메모리/네트워크 제약 때문에 `workers=1` 또는 `2`를 권장합니다.

## 프로젝트 구조

```
src/musinsa/
  config.py          # 브랜드/필터/정규식/카테고리 시드 등 고정 설정
  schemas.py         # ProductRow / OptionRow / SkuRow dataclass
  utils.py           # URL/가격/날짜 등 공통 유틸
  text_parse.py      # 상품명·색상·사이즈 추정
  browser.py         # Firefox/Chrome 드라이버 생성 + safe_goto + 팝업 닫기
  listing.py         # 카테고리 목록 스크롤 수집
  categories.py      # 카테고리 트리 동적 발견
  detail.py          # 상세/옵션 텍스트 수집·파싱
  excel.py           # SKU_요약 + raw 시트 엑셀 생성
  driver_pool.py     # ThreadPool용 드라이버 풀
  logger.py          # rich 기반 단일 Console + Progress
  pipeline.py        # main(**kwargs) 오케스트레이션
  cli.py             # argparse + entrypoint
mgen.py              # 역호환 shim (entrypoint 호출)
```
