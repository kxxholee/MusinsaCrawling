# TODO

## Phase B: Selenium 의존성 정리 (Phase A 안정화 후)

옵션 API 전환으로 detail 경로가 Selenium 비의존이 됐다. 남은 `pool.borrow` 사이트는 두 곳뿐.

- [ ] `discover_category_tree` ([categories.py](src/musinsa/categories.py)) 도 가능하면 HTTP 직접 호출로 (현재는 `driver.page_source` 의존). 카테고리 인덱스 페이지가 SSR 인지 SPA 인지 확인 필요. SSR 이면 `aiohttp` GET 한 번으로 끝.
- [ ] [listing.py](src/musinsa/listing.py) 의 Selenium 폴백 (`scroll_and_collect_products`, `wait_for_product_items`, `parse_product_items_from_html`) 제거 검토. PLP API 가 안정적이면 (직전 6개월 운영에서 한 번도 폴백이 트리거되지 않으면) 제거 OK.
- [ ] [detail.py](src/musinsa/detail.py) 의 Selenium 폴백 (`collect_detail_and_options` 외) 도 같은 기준으로 제거 검토.
- [ ] 위 세 사이트 모두 제거되면 [browser.py](src/musinsa/browser.py), [driver_pool.py](src/musinsa/driver_pool.py) 삭제, [pyproject.toml](pyproject.toml) 의 `selenium` 의존성 제거. README/CLAUDE.md 의 "Firefox/Chrome" 관련 설명 정리.

## Phase C: tags / stat 보강 (선택)

옵션 수집과 같은 `aiohttp` gather 안에 추가 호출 가능. 한 상품당 호출 3개로 늘어도 동시성 32 면 시간 영향 미미.

- [ ] [schemas.py](src/musinsa/schemas.py) `ProductRow` 에 `tags: str` (콤마 join), `purchase_total: Optional[int]`, `view_total: Optional[int]` 추가
- [ ] [detail_api.py](src/musinsa/detail_api.py) 에 `fetch_goods_tags_payload`, `fetch_goods_stat_payload` 추가 (확정된 엔드포인트: `goods-detail.musinsa.com/api2/goods/{pid}/tags`, `.../stat`)
- [ ] `gather_details_async` 에서 옵션 + tags + stat 을 `asyncio.gather` 로 동시 호출, 모이면 ProductRow 갱신
- [ ] [excel.py](src/musinsa/excel.py) products_raw 컬럼은 `__annotations__` 기반이라 자동 반영. `SKU_요약` 시트 양식은 건드리지 않음.

## 의도적으로 안 건드리는 것

- `TARGET_CATEGORIES`, `GENDER_FILTER="F"`, `CATEGORY_MASTER`, `COLOR_ALIASES`, 사이즈/가격 정규식 — CLAUDE.md에 명시된 데모 스코프.
- 엑셀 시트 순서 및 `SKU_요약` 병합 규칙.
- dataclass 스키마 (`ProductRow`/`OptionRow`/`SkuRow`) — Phase C 확장 시에만 필드 추가 (기존 필드 보존).
