"""
검수 로직 핵심 모듈.
새 양식(애드웰 상품제안서)을 처리하며, 네이버 최저가 정합성 검증에 집중.
"""
import os
import re
import requests
import pandas as pd
import statistics
from dotenv import load_dotenv

load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")


def set_credentials(client_id, client_secret):
    """외부(app.py 등)에서 API 자격 정보를 주입할 때 사용."""
    global CLIENT_ID, CLIENT_SECRET
    if client_id:
        CLIENT_ID = client_id
    if client_secret:
        CLIENT_SECRET = client_secret


# ============================================================
# 새 양식 컬럼 매핑 (애드웰 상품제안서)
# ============================================================
COL_MAP = {
    "no": "NO",
    "category": "카테고리",
    "brand": "브랜드명",
    "code": "상품코드",
    "name": "상품명",
    "list_price": "정가\n(A)",
    "supply_price": "공급가\n(B)",
    "commission_rate": "플랫폼수수료율\n(C)",
    "sale_price": "판매가\n(D = B / (1-C))",
    "stated_lowest": "네이버 최저가\n(E)",
    "discount_rate": "최저가 대비 할인율\n(1 - D/E)",
    "catalog_id": "네이버 카탈로그 ID\n(가격비교 고유번호)",
    "note": "비고",
    "url": "네이버 최저가 URL\n(모니터링 연동용)",
    "stock": "가용재고",
    "show_price": "가격 노출여부",
}

# 가격 합리성 필터: 기재 최저가의 30%~300% 범위 안만 후보로 인정
PRICE_RANGE_MIN_RATIO = 0.3
PRICE_RANGE_MAX_RATIO = 3.0

# 모델명 미일치 시 fallback 점수 (다른 키워드가 모두 맞을 때 부여)
MODEL_FALLBACK_SCORE = 0.7

# 매칭 임계값
MATCH_THRESHOLD = 0.7


# ============================================================
# 헬퍼: 텍스트 처리
# ============================================================
def clean_text(text):
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def extract_model_code(product_name):
    """상품명에서 모델 코드 추출 (예: AQ-75, ICE-43T, AGC-5000W)."""
    name = str(product_name)
    name_clean = re.sub(r"\([^)]*\)", "", name)
    pattern = r"\b([A-Z][A-Z0-9\-]{2,})\b"
    candidates = re.findall(pattern, name_clean)
    codes = [c for c in candidates if len(c) >= 3 and not c.isdigit()]
    return codes


def extract_keywords(product_name):
    """매칭에 쓸 일반 키워드 추출."""
    name = str(product_name)
    name = re.sub(r"^\[[^\]]+\]", "", name)
    name = re.sub(r"\([^)]*\)", "", name)
    tokens = name.split()
    return [t for t in tokens if len(t) >= 2]


def extract_units(product_name):
    """상품명에서 '숫자+단위' 패턴 추출."""
    pattern = r"(\d+(?:\.\d+)?)\s*(ml|l|g|kg|포|개입|개|정|캡슐|환|매|입|병|봉|팩|세트|박스)"
    matches = re.findall(pattern, str(product_name), flags=re.IGNORECASE)
    return [f"{num}{unit.lower()}" for num, unit in matches]


def match_score(product_name, item_title):
    """원본 상품명과 검색결과 제목의 일치도.
    
    우선순위:
    1. 모델 코드 양쪽 일치 → 1.0
    2. 결과 제목에 다른 모델 코드가 있으면 → 0.0 (다른 모델 차단)
    3. 결과 제목에 모델 코드가 없는 경우 → 키워드+단위 fallback
    """
    title_clean = clean_text(item_title)
    
    원본_코드들 = extract_model_code(product_name)
    if 원본_코드들:
        # 1. 코드 일치 확인 (가장 강력한 신호)
        for code in 원본_코드들:
            if code in title_clean.upper():
                return 1.0
        
        # 2. 결과 제목에 다른 모델 코드가 있는지 확인
        # 같은 브랜드의 다른 모델 차단 (AQ-101 검색에 AQ-120 매칭되는 문제 방지)
        제목_코드들 = extract_model_code(title_clean)
        if 제목_코드들:
            return 0.0
        
        # 3. 제목에 모델 코드가 아예 없는 경우만 fallback 시도
        원본_단위들 = extract_units(product_name)
        제목_단위들 = extract_units(title_clean)
        for unit in 원본_단위들:
            if unit not in 제목_단위들:
                return 0.0
        
        keywords = extract_keywords(product_name)
        if not keywords:
            return 0.0
        keywords = [kw for kw in keywords if not any(code in kw.upper() for code in 원본_코드들)]
        if not keywords:
            return 0.0
        
        matched = sum(1 for kw in keywords if kw in title_clean)
        if matched == len(keywords):
            return MODEL_FALLBACK_SCORE
        return 0.0
    
    # 모델 코드 없는 경우: 단위 + 키워드 일반 매칭
    원본_단위들 = extract_units(product_name)
    제목_단위들 = extract_units(title_clean)
    for unit in 원본_단위들:
        if unit not in 제목_단위들:
            return 0.0
    
    keywords = extract_keywords(product_name)
    if not keywords:
        return 0.0
    matched = sum(1 for kw in keywords if kw in title_clean)
    return matched / len(keywords)


# ============================================================
# 검색 쿼리 정제
# ============================================================
def _strip_parens(product_name):
    """검색 쿼리 정제: 괄호 안 내용 제거. 단, 맨 앞 [브랜드]는 유지.
    
    예: "[아이리버] 핸디형 선풍기 ICE-43T(베이지, 블랙)"
        → "[아이리버] 핸디형 선풍기 ICE-43T"
    """
    name = str(product_name)
    
    # 맨 앞 [브랜드] 보존
    m = re.match(r"^(\[[^\]]+\])", name)
    brand_prefix = m.group(1) if m else ""
    rest = name[len(brand_prefix):] if brand_prefix else name
    
    # 나머지에서 () 와 [] 안 내용 제거
    rest = re.sub(r"\([^)]*\)", "", rest)
    rest = re.sub(r"\[[^\]]*\]", "", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    
    if brand_prefix:
        return f"{brand_prefix} {rest}".strip()
    return rest


# ============================================================
# 네이버 가격 조회
# ============================================================
def _do_naver_search(query, product_name, brand_name, stated_lowest):
    """실제 API 호출 + 매칭. search_naver_price의 내부 헬퍼.
    
    - query: API에 보낼 검색어 (1차/2차에 따라 다를 수 있음)
    - product_name: 원본 상품명 (매칭 점수 계산용, 항상 원본)
    
    429 응답 시 잠시 대기 후 재시도. 재시도도 실패하면 진짜 한도 초과로 판단.
    매 호출 전 짧은 딜레이로 초당 호출 제한 회피.
    """
    import time
    
    if not CLIENT_ID or not CLIENT_SECRET:
        return {"status": "ERROR", "price": None, "total_count": None, "all_prices": [],
                "message": "API 키 누락"}
    
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET}
    params = {"query": query, "display": 10, "sort": "sim"}
    
    # 매 호출 전 100ms 딜레이 (초당 호출 제한 회피)
    time.sleep(0.1)
    
    response = None
    for attempt in range(2):  # 최초 1회 + 429일 때 1회 재시도
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
        except requests.exceptions.RequestException as e:
            return {"status": "ERROR", "price": None, "total_count": None, "all_prices": [],
                    "message": f"네트워크 오류: {e}"}
        
        if response.status_code == 429:
            # 첫 429면 2초 대기 후 재시도
            if attempt == 0:
                time.sleep(2.0)
                continue
            # 재시도도 429면 진짜 한도 초과로 판단
            return {"status": "API_LIMIT", "price": None, "total_count": None, "all_prices": [],
                    "message": "API 호출 제한 (일일 한도 초과 가능성). 잠시 후 다시 시도하세요."}
        
        # 200이든 다른 오류든 루프 탈출
        break
    
    if response.status_code != 200:
        return {"status": "ERROR", "price": None, "total_count": None, "all_prices": [],
                "message": f"API 오류 {response.status_code}"}
    
    data = response.json()
    items = data.get("items", [])
    total_count = data.get("total", 0)
    
    if not items:
        return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [],
                "message": "네이버 검색 결과 0건"}
    
    # 1차 필터: productType + 브랜드 + 중고/리퍼 + 가격 합리성
    brand_clean = str(brand_name).strip() if brand_name else ""
    EXCLUDE_PRODUCT_TYPES = {"5", "6"}  # 보류 결정에 따라 이전 상태 유지
    EXCLUDE_KEYWORDS = ["중고", "리퍼", "B급", "반품", "전시품", "미사용", "개봉"]
    
    price_min, price_max = None, None
    if stated_lowest and not pd.isna(stated_lowest):
        try:
            stated = float(stated_lowest)
            price_min = stated * PRICE_RANGE_MIN_RATIO
            price_max = stated * PRICE_RANGE_MAX_RATIO
        except (TypeError, ValueError):
            pass
    
    candidates = []
    filtered_out_by_price = 0
    
    for item in items:
        if item.get("productType") in EXCLUDE_PRODUCT_TYPES:
            continue
        item_brand = clean_text(item.get("brand", ""))
        if item_brand and brand_clean and item_brand != brand_clean:
            continue
        title_clean = clean_text(item.get("title", ""))
        if any(kw in title_clean for kw in EXCLUDE_KEYWORDS):
            continue
        if price_min is not None and price_max is not None:
            try:
                item_price = int(item["lprice"])
                if item_price < price_min or item_price > price_max:
                    filtered_out_by_price += 1
                    continue
            except (TypeError, ValueError, KeyError):
                pass
        candidates.append(item)
    
    if not candidates:
        if filtered_out_by_price > 0:
            return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [],
                    "message": "가격대 범위(기재가의 30%~300%) 밖 상품만 검색됨. 부속품/세트 가능성. 수동 확인 필요."}
        return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [],
                "message": "조건 맞는 상품 없음 (네이버 묶음/타브랜드/중고만 검색됨)"}
    
    # 2차: 매칭 점수 (매칭은 항상 원본 상품명 기준)
    scored = [{"item": i, "score": match_score(product_name, i["title"]), "price": int(i["lprice"])} for i in candidates]
    matched = [s for s in scored if s["score"] >= MATCH_THRESHOLD]
    
    if not matched:
        best = max(scored, key=lambda s: s["score"])
        codes = extract_model_code(product_name)
        if codes:
            msg = f"모델명 {'/'.join(codes)} 일치 상품 없음. 수동 확인 필요."
        else:
            msg = f"매칭 신뢰도 낮음 (최고 {best['score']:.0%}). 수동 확인 필요."
        return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [],
                "message": msg}
    
    matched_prices = [s["price"] for s in matched]
    best_match = min(matched, key=lambda s: s["price"])
    
    return {
        "status": "OK",
        "price": best_match["price"],
        "total_count": total_count,
        "all_prices": sorted(matched_prices),
        "message": "정상",
    }


def search_naver_price(product_name, brand_name, stated_lowest=None):
    """네이버 검색 후 매칭/필터링하여 최저가와 가격 분포 반환.
    
    1차: 원본 상품명 그대로 검색
    2차 (1차가 NO_MATCH일 때만): 괄호 안 옵션(색상/사이즈 등) 제거하고 재검색
    """
    # 1차 검색: 원본 상품명 그대로
    result = _do_naver_search(str(product_name), product_name, brand_name, stated_lowest)
    
    # 1차가 성공이거나 API 오류면 그대로 반환
    if result["status"] != "NO_MATCH":
        return result
    
    # 1차가 NO_MATCH → 괄호 제거하고 재검색
    relaxed_query = _strip_parens(product_name)
    
    # 정제 결과가 원본과 같으면 (괄호가 없던 경우) 재검색 의미 없음
    if relaxed_query == str(product_name).strip():
        return result
    
    result_2 = _do_naver_search(relaxed_query, product_name, brand_name, stated_lowest)
    
    # 재검색이 성공하면 그 결과 사용 + 메모에 표시
    if result_2["status"] == "OK":
        result_2["message"] = "완화 검색으로 매칭 (괄호 제거 쿼리)"
        return result_2
    
    # 재검색도 실패면 원래 결과 메시지 유지
    return result


# ============================================================
# 가격 변동성 감지
# ============================================================
def detect_price_anomaly(prices):
    """매칭된 가격 리스트에서 1위만 비정상적으로 저렴한지 감지.
    
    규칙: 1위 가격 × 1.5 < 2위 가격 이면 1위가 이상치.
    """
    if len(prices) < 2:
        return False, None
    first = prices[0]
    second = prices[1]
    if first * 1.5 < second:
        rest_avg = int(statistics.mean(prices[1:]))
        msg = f"검색 1위만 {first:,}원으로 비정상적 저가, 2~{len(prices)}위 평균 {rest_avg:,}원. 일회성 특가 가능성, MD 확인 필요."
        return True, msg
    return False, None


# ============================================================
# 한 행 검수
# ============================================================
def validate_row(row, tolerance_pct=5.0):
    """새 양식 한 행을 검수.
    
    tolerance_pct: 기재가-조회가 정합성 허용 오차 (%, 기본 5.0)
    """
    상품명 = row.get(COL_MAP["name"])
    브랜드명 = row.get(COL_MAP["brand"])
    공급가 = row.get(COL_MAP["supply_price"])
    수수료율 = row.get(COL_MAP["commission_rate"])
    기재최저가 = row.get(COL_MAP["stated_lowest"])
    
    # 판매가 직접 계산
    if pd.notna(공급가) and pd.notna(수수료율) and 수수료율 < 1:
        판매가 = round(공급가 / (1 - 수수료율), -1)
    else:
        판매가 = None
    
    if pd.isna(상품명) or not str(상품명).strip():
        return {
            "_api_limit": False,
            "결과": "확인 필요",
            "네이버 조회가": "—",
            "차이": "—",
            "시장 풍부도": "—",
            "검수 메모": "상품명 누락. 브랜드사에 확인 요청.",
        }
    
    # 네이버 가격 조회
    naver = search_naver_price(상품명, 브랜드명, stated_lowest=기재최저가)
    
    if naver["status"] == "API_LIMIT":
        return {"_api_limit": True}
    
    조회가 = naver["price"]
    total_count = naver["total_count"]
    all_prices = naver["all_prices"]
    
    # 시장 풍부도
    if total_count is None or total_count == 0:
        풍부도 = "0건"
    elif all_prices:
        min_p = min(all_prices)
        max_p = max(all_prices)
        if min_p == max_p:
            풍부도 = f"{total_count:,}건\n{min_p:,}원"
        else:
            풍부도 = f"{total_count:,}건\n{min_p:,}~{max_p:,}원"
    else:
        풍부도 = f"{total_count:,}건"
    
    # 차이 계산 (기재최저가 A - 조회가 B)
    if 조회가 and pd.notna(기재최저가):
        차액 = int(기재최저가) - 조회가
        차이율 = (차액 / 조회가) * 100 if 조회가 else 0
        if 차액 == 0:
            차이표시 = "0\n(0.0%)"
        else:
            차이표시 = f"{차액:+,}\n({차이율:+.1f}%)"
    else:
        차이표시 = "—"
    
    # 메모 조각 모으기
    메모조각 = []
    if naver["status"] != "OK":
        메모조각.append(naver["message"])
    elif naver.get("message") and naver["message"] != "정상":
        # 완화 검색 등 OK이지만 메시지가 있는 경우 (예: "완화 검색으로 매칭")
        메모조각.append(naver["message"])
    
    if naver["status"] == "OK" and all_prices:
        is_anomaly, anomaly_msg = detect_price_anomaly(all_prices)
        if is_anomaly:
            메모조각.append(anomaly_msg)
    
    if 조회가 and pd.notna(기재최저가):
        차액 = int(기재최저가) - 조회가
        차이율_abs = abs(차액 / 조회가) * 100 if 조회가 else 0
        if 차이율_abs > tolerance_pct:
            if 차액 > 0:
                메모조각.append(f"기재 최저가가 조회가보다 {차이율_abs:.1f}% 높음. 브랜드사에 정정 요청 필요.")
            else:
                메모조각.append(f"기재 최저가가 조회가보다 {차이율_abs:.1f}% 낮음. 브랜드사에 정정 요청 필요.")
    
    if 판매가 and 조회가 and 판매가 > 조회가:
        경쟁률 = ((판매가 - 조회가) / 조회가) * 100
        if 수수료율 and 수수료율 < 1:
            목표공급가 = int(조회가 * (1 - 수수료율))
            메모조각.append(f"판매가 {int(판매가):,}원이 네이버 조회가 {조회가:,}원보다 {경쟁률:.1f}% 비쌈. 공급가 {목표공급가:,}원 이하로 협상 시 경쟁력 확보.")
    
    검수메모 = " / ".join(메모조각) if 메모조각 else "—"
    
    # 종합 결과 판정
    is_match_ok = naver["status"] == "OK"
    is_price_competitive = not (판매가 and 조회가 and 판매가 > 조회가)
    is_stated_close = True
    if 조회가 and pd.notna(기재최저가):
        is_stated_close = abs(int(기재최저가) - 조회가) / 조회가 * 100 <= tolerance_pct
    is_no_anomaly = True
    if naver["status"] == "OK" and all_prices:
        is_anomaly, _ = detect_price_anomaly(all_prices)
        is_no_anomaly = not is_anomaly
    
    if is_match_ok and is_price_competitive and is_stated_close and is_no_anomaly:
        결과 = "승인 가능"
    else:
        결과 = "확인 필요"
    
    return {
        "_api_limit": False,
        "결과": 결과,
        "네이버 조회가": 조회가 if 조회가 else "—",
        "차이": 차이표시,
        "시장 풍부도": 풍부도,
        "검수 메모": 검수메모,
    }


# ============================================================
# 전체 검수
# ============================================================
def validate_excel_file(file_path_or_bytes, progress_callback=None, tolerance_pct=5.0):
    """엑셀 파일 경로 또는 파일 객체를 받아 첫 시트를 검수."""
    df = pd.read_excel(file_path_or_bytes, sheet_name=0, header=1)
    name_col = COL_MAP["name"]
    df_valid = df.dropna(subset=[name_col]).reset_index(drop=True)
    
    results = []
    for index, row in df_valid.iterrows():
        if progress_callback:
            progress_callback(index, len(df_valid), str(row[name_col]))
        
        result = validate_row(row, tolerance_pct=tolerance_pct)
        if result.get("_api_limit"):
            break
        result.pop("_api_limit", None)
        results.append(result)
    
    return results, df_valid