# 무신사 JSON API 기반 크롤링 전환 검토 문서

작성일: 2026-05-03  
대상 레포: `kxxholee/MusinsaCrawling`  
목적: 현재 Selenium/HTML 기반 상세·목록 수집이 누락되거나 느려지는 문제를 줄이기 위해, 무신사 내부 JSON API 활용 가능성을 검토한다.

---

## 1. 요약

현재 레포는 목록 수집에서 이미 일부 JSON API를 사용하고 있지만, 상세 페이지와 옵션 수집은 여전히 브라우저 렌더링과 HTML 파싱에 많이 의존한다. 이 방식은 다음 문제가 생길 수 있다.

- 스크롤이 끝까지 돌지 않아 상품 수가 부족하게 잡힘
- 상세 페이지의 동적 데이터가 HTML에 바로 없어서 누락됨
- 브라우저 드라이버(WebDriver)와 엑셀 저장 단계가 겹치면 메모리 사용량이 커짐
- 상품 수가 많아질수록 실행 시간이 길어짐

공개 사례를 찾아본 결과, 무신사에는 실제로 여러 JSON API가 존재하며, 다른 개발자들도 스크롤/렌더링 방식에서 JSON API 방식으로 전환해 성능과 정확도를 개선한 사례가 있다.

결론적으로, 이 레포도 다음 방향이 적합하다.

```text
1. 목록은 JSON API로 상품번호(goodsNo)를 안정적으로 수집
2. 상세 정보는 HTML 화면 파싱보다 JSON API 또는 페이지 내 상태 데이터에서 추출
3. 옵션/재고는 별도 API를 우선 탐색
4. 실패 시에만 Selenium fallback 사용
5. 중간 결과는 CSV/JSONL로 저장
```

---

## 2. 확인된 공개 사례

### 2.1 리뷰 수집 API 사례

한 Velog 글에서는 무신사 리뷰 페이지가 단일 페이지 앱(SPA, Single Page Application)과 가상 스크롤(virtual scroll) 구조라서, 처음에는 리뷰 약 10개만 렌더링되고 스크롤할수록 데이터가 추가된다고 설명한다.

기존에는 Puppeteer로 스크롤을 반복했지만, 리뷰가 1,000개 이상인 상품에서는 속도가 크게 떨어졌고 일부 누락도 발생했다. 이후 개발자 도구의 네트워크 탭(Network tab)에서 리뷰 API를 찾아 직접 호출하는 방식으로 변경했다.

확인된 API 형태:

```text
GET https://goods.musinsa.com/api2/review/v1/view/list
```

주요 파라미터:

```text
goodsNo={상품번호}
page={페이지}
pageSize={페이지당개수}
```

공개 글에서는 페이지가 `0`부터 시작하고, 응답의 `data.page.totalPages`를 이용해 종료 조건을 잡았다. 작성자는 API 방식으로 바꾼 뒤 리뷰 데이터 정확도와 속도가 크게 개선되었다고 설명한다.

이 사례가 중요한 이유는, 현재 레포의 문제와 구조가 비슷하기 때문이다.

```text
기존 방식: 브라우저 스크롤 → 화면에 렌더링된 데이터 파싱
개선 방식: 실제 데이터 API 직접 호출 → JSON 응답 파싱
```

### 2.2 상품 태그 API 사례

다른 Velog 글에서는 상품 상세 페이지에서 태그 데이터가 HTML 파싱으로는 비어 있는 문제를 다뤘다.

원인은 서버 사이드 렌더링(SSR, Server Side Rendering)과 클라이언트 사이드 렌더링(CSR, Client Side Rendering)이 섞인 구조였다. 쉽게 말하면, 처음 받은 HTML에는 회색 로딩 박스 같은 뼈대만 있고, 실제 태그 데이터는 브라우저가 나중에 API로 가져오는 방식이었다.

확인된 API 형태:

```text
GET https://goods-detail.musinsa.com/api2/goods/{goods_no}/tags
```

작성자는 이 API를 직접 호출하니 HTML 파싱 없이 JSON 데이터가 반환되었다고 설명한다.

중요한 점은 도메인이다.

```text
goods.musinsa.com
```

이 아니라:

```text
goods-detail.musinsa.com
```

계열의 상세 API가 실제로 쓰이고 있었다.

### 2.3 상품 통계 API 사례

2026년 4월 Tistory 글에서는 상품별 누적 판매량과 조회수를 가져오기 위해 다음 API를 사용한 코드가 공개되어 있다.

```text
GET https://goods-detail.musinsa.com/api2/goods/{goods_no}/stat
```

응답의 `data` 안에서 다음 값을 읽는다.

```text
purchaseTotal
pageViewTotal
```

이 사례는 상품 상세 쪽에도 JSON API가 따로 있다는 점을 뒷받침한다.

### 2.4 상세 페이지 내부 상태값 사례

다른 Tistory 글에서는 상품 상세 페이지 HTML 안의 스크립트에서 다음 패턴을 찾아 데이터를 추출했다.

```text
window.__MSS__.product.state = {...};
```

이 상태값에서 브랜드 정보, 회사 정보, 연락처, 이메일, 주소 등을 꺼내는 방식이다.

이 방식은 API 직접 호출은 아니지만, 상세 페이지에 필요한 데이터가 단순 HTML 태그가 아니라 페이지 내부의 JSON 상태 데이터(state data)에 들어 있을 수 있음을 보여준다.

### 2.5 옵션/재고 관련 구형 사례

옵션과 재고 쪽은 최신 JSON API 사례가 확실히 확인되지는 않았다.

다만 2021~2022년 구형 무신사 구조에서는 다음 페이지를 직접 요청한 뒤:

```text
https://store.musinsa.com/app/goods/{itemNum}
```

HTML의 옵션 태그를 읽는 방식이 있었다.

```text
.option1 option
```

그리고 각 옵션의 `jaego_yn` 값을 보고 재고 여부를 판단했다.

```text
Y = 재고 있음
N = 재고 없음
```

현재 무신사 페이지 구조는 많이 바뀌었기 때문에 이 코드를 그대로 쓰기는 어렵다. 하지만 옵션/재고 데이터가 별도 구조로 존재했다는 점은 참고할 만하다.

---

## 3. 사용자가 제안한 후보 엔드포인트 검토

사용자 제안:

```text
https://goods.musinsa.com/api2/goods/{goods_no}
https://goods.musinsa.com/api2/goods/{goods_no}/options
https://api.musinsa.com/api2/dp/v2/pdp/{goods_no}
```

공개 웹 검색 기준으로는 위 세 주소를 직접 사용한 사례는 확실히 확인하지 못했다.

현재까지 확인된 쪽은 다음과 같다.

| 목적 | 확인된 엔드포인트 | 확인 수준 |
|---|---|---|
| 리뷰 | `goods.musinsa.com/api2/review/v1/view/list` | 높음 |
| 상품 태그 | `goods-detail.musinsa.com/api2/goods/{goods_no}/tags` | 높음 |
| 상품 통계 | `goods-detail.musinsa.com/api2/goods/{goods_no}/stat` | 높음 |
| 상세 상태 데이터 | `window.__MSS__.product.state` | 중간 |
| 옵션/재고 | 구형 `.option1 option[jaego_yn]` | 낮음 |
| 상품 메타 | `goods.musinsa.com/api2/goods/{goods_no}` | 미확인 |
| 옵션 API | `goods.musinsa.com/api2/goods/{goods_no}/options` | 미확인 |
| PDP API | `api.musinsa.com/api2/dp/v2/pdp/{goods_no}` | 미확인 |

따라서 바로 개발에 넣을 때는 다음 순서가 좋다.

```text
1. 확실히 확인된 API부터 모듈화
2. 후보 API는 디버그 모드에서 상태 코드와 응답 구조만 확인
3. 성공 응답이 확인되면 정식 파서 추가
4. 실패하면 기존 HTML/Selenium 방식으로 fallback
```

---

## 4. 현재 레포에 적용할 때의 방향

### 4.1 목록 수집

현재 레포는 이미 목록 쪽에서 다음 API를 사용하고 있다.

```text
https://api.musinsa.com/api2/dp/v2/plp/goods
```

다만 현재 문제는 “상의가 206개만 잡힌다”처럼 특정 카테고리의 상품 수가 지나치게 적게 보인다는 점이다. 이 경우 먼저 확인할 것은 스크롤이 아니라 API 페이지 넘김이다.

점검할 항목:

```text
page가 실제로 1, 2, 3... 증가하는가?
응답에서 list 길이가 계속 30개로 오는가?
totalPages / totalPage / pageTotal 같은 페이지 수 키가 바뀌지 않았는가?
hasNext 값이 있는가?
빈 페이지가 나오기 전까지 요청을 계속하는가?
```

현재처럼 `totalPages`만 믿는 구조라면, 응답 구조가 조금 바뀌었을 때 1페이지만 받고 멈출 수 있다.

### 4.2 상세 수집

현재 상세 수집은 다음 순서로 바꾸는 것이 좋다.

```text
1. goodsNo 기준으로 상세 JSON API 후보 호출
2. 실패하면 www.musinsa.com/products/{goodsNo} HTML 요청
3. HTML 안의 window.__MSS__.product.state 또는 __NEXT_DATA__ 탐색
4. 그래도 실패하면 Selenium 사용
```

즉, Selenium은 기본값이 아니라 마지막 fallback으로 두는 구조가 좋다.

### 4.3 옵션/재고 수집

옵션/재고는 가장 불확실한 영역이다. 따라서 바로 기존 코드를 대체하지 말고, 먼저 디버그 탐색 함수를 만드는 것이 좋다.

탐색 후보:

```text
https://goods-detail.musinsa.com/api2/goods/{goods_no}/options
https://goods.musinsa.com/api2/goods/{goods_no}/options
https://api.musinsa.com/api2/dp/v2/pdp/{goods_no}
https://www.musinsa.com/products/{goods_no}
```

탐색 함수는 다음만 저장하면 된다.

```text
goods_no
url
status_code
content_type
response_length
top_level_keys
data_keys
error_message
```

절대 처음부터 전체 상품에 대해 옵션 API 탐색을 돌리면 안 된다. 먼저 상품 3~5개만 테스트해야 한다.

---

## 5. 추천 파일 구조

현재 레포에 다음 파일을 추가하는 구조를 추천한다.

```text
src/musinsa/api_client.py
src/musinsa/detail_api.py
src/musinsa/api_probe.py
```

### 5.1 `api_client.py`

역할:

```text
공통 HTTP 세션 관리
기본 헤더 관리
요청 간 sleep
재시도 제한
상태 코드 기록
```

초보자 기준으로는 `requests.Session()` 하나를 만들고, 요청 간 0.3~1.0초 정도 쉬게 하는 것부터 시작하면 된다.

### 5.2 `detail_api.py`

역할:

```text
fetch_goods_tags(goods_no)
fetch_goods_stat(goods_no)
fetch_reviews(goods_no)
fetch_detail_state(goods_no)
```

확인된 API부터 함수로 나누면 된다.

### 5.3 `api_probe.py`

역할:

```text
후보 API가 실제로 살아 있는지 테스트
응답 구조를 CSV/JSON으로 저장
```

이 파일은 정식 크롤링용이 아니라 조사용이다.

---

## 6. 요청 속도와 차단 주의

공개 사례 중 하나는 API를 너무 빠르게 호출하다가 429 Too Many Requests와 403 Forbidden을 만났다고 설명한다.

따라서 다음 원칙이 필요하다.

```text
workers는 처음에 1로 시작
요청 간 delay는 최소 0.3초 이상
실패한 요청은 무한 재시도하지 않기
429/403이 나오면 즉시 속도 낮추기
중간 결과는 CSV/JSONL로 저장
한 번에 전체 상품을 때리지 말고 smoke 모드로 먼저 검증
```

이건 우회 목적이 아니라, 크롤러가 너무 많은 요청을 보내서 자기 자신도 불안정해지고 상대 서버에도 부담을 주는 것을 막기 위한 기본 안전장치다.

---

## 7. 구현 우선순위

### 1단계: 목록 API 페이지 넘김 검증

목표:

```text
전체 탭 2652개에 가까운 상품번호를 안정적으로 확보
```

해야 할 일:

```text
PLP API 응답의 pagination 구조를 로그 또는 CSV로 저장
page별 list 개수 확인
상위 3자리 카테고리별 후보 수 확인
```

### 2단계: 상세 API 조사

목표:

```text
Selenium 없이 얻을 수 있는 상세 데이터 범위 파악
```

해야 할 일:

```text
tags API 확인
stat API 확인
HTML 내부 window.__MSS__.product.state 확인
__NEXT_DATA__ 존재 여부 확인
```

### 3단계: 옵션/재고 API 조사

목표:

```text
옵션명, 색상, 사이즈, 품절 여부를 JSON으로 얻을 수 있는지 확인
```

해야 할 일:

```text
후보 endpoint 3~5개 상품에 대해 probe
응답 status와 top-level key 저장
성공한 구조가 있으면 파서 작성
```

### 4단계: 기존 상세 수집과 병합

목표:

```text
API 우선, 실패 시 기존 Selenium 방식으로 fallback
```

수집 순서:

```text
JSON API
→ HTML 내부 상태값
→ 기존 Selenium 파싱
```

### 5단계: 저장 구조 개선

목표:

```text
막바지에 VSCode가 튕기는 문제 완화
```

추천:

```text
수집 중 CSV/JSONL로 즉시 저장
최종 엑셀에는 요약만 저장
raw 데이터는 CSV로 유지
```

---

## 8. 결론

사용자가 제안한 “상세도 리스트처럼 JSON API가 따로 있을 가능성”은 충분히 타당하다. 공개 사례에서도 무신사 리뷰, 태그, 통계 데이터를 JSON API로 직접 가져온 사례가 확인된다.

다만 옵션/재고 API는 아직 확정된 공개 사례를 찾지 못했다. 따라서 바로 전체 코드를 바꾸기보다는, 먼저 `api_probe.py` 같은 탐색 모듈을 만들어 후보 API 응답을 확인하고, 성공한 것만 정식 수집 경로에 넣는 방식이 안전하다.

최종 목표 구조는 다음과 같다.

```text
목록 JSON API
→ 상품번호 수집
→ 상세 JSON API / 상태 데이터 추출
→ 옵션 API 탐색 성공 시 적용
→ 실패 시 Selenium fallback
→ CSV 중간 저장
→ 가벼운 엑셀 요약 생성
```

---

## 참고 자료

1. SeongJae Kim, “무신사 리뷰 데이터 크롤링하기(Musinsa Web-Site review Crawling)”, Velog, 2025-08-02.  
   https://velog.io/@developer_beaver/무신사-리뷰-데이터-크롤링하기Musinsa-Web-Site-review-Crawling

2. siu, “[Python] 무신사 크롤링 트러블 슈팅: 빈 태그 문제부터 봇 탐지 우회까지”, Velog.  
   https://velog.io/@goodtosiu/Python-무신사-크롤링-트러블-슈팅-빈-태그-문제부터-봇-탐지-우회까지

3. msoo5880, “주제선정의 가닥과 간단 크롤링”, Tistory, 2026-04-13.  
   https://msoo5880.tistory.com/55

4. taehyuck, “무신사 크롤링”, Tistory, 2025-03-23.  
   https://taehyuck.tistory.com/9

5. K-Junyyy, “MUSINSA-CRWALING”, GitHub.  
   https://github.com/K-Junyyy/MUSINSA-CRWALING

6. cocoon1787, “[Node.js] Node + Kakao API로 상품 재입고 알림 만들기 (ver 1.0)”, Tistory, 2021-11-14.  
   https://cocoon1787.tistory.com/741
