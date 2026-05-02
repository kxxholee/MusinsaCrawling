from __future__ import annotations

import argparse

from .config import DEFAULT_OUTPUT
from .pipeline import main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="무신사 스탠다드 여성 상품 SKU 계산용 엑셀 생성기 데모"
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"저장할 엑셀 파일명. 기본값: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=10,
        help="카테고리별 최대 상품 수. 전체 수집은 0 입력. 기본값: 10",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=80,
        help="상품 목록 페이지에서 스크롤하며 누적 수집할 최대 횟수. 기본값: 80",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="상세 페이지 사이 대기 시간, 초 단위. 기본값: 0.8",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="브라우저 화면을 띄우지 않고 실행합니다.",
    )
    parser.add_argument(
        "--skip-options",
        action="store_true",
        help="상세 페이지 옵션 수집을 건너뛰고 상품 목록만 저장합니다.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="병렬 Selenium 드라이버 개수. 기본값: 4 (Firefox 인스턴스당 ~300MB 메모리).",
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "firefox", "chrome"],
        default="auto",
        help="사용할 브라우저. auto는 환경에서 자동 감지. 기본값: auto",
    )

    return parser.parse_args()


def entrypoint() -> None:
    args = parse_args()
    main(**vars(args))
