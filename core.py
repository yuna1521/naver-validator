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
# 원본 양식의 컬럼명이 길어 내부 표준 이름으로 매핑.
# 양식이 또 바뀌면 이 부분만 수정하면 됨.
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

# 검수 대상으로 인식할 첫 시트 이름 (정확히 일치할 필요는 없고 keyword 포함)
SHEET_NAME_KEYWORD = "상품"


# ============================================================
# 헬퍼: 텍스트 처리
# ============================================================
def clean_text(text):
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def extract_model_code(product_name):
    """상품명에서 모델 코드 추출 (예: AQ-75, ICE-43T, AGC-5000W).
    
    규칙: 대문자/숫자/하이픈이 섞인 3자 이상 토큰.
    숫자만이거나 한글이 섞이면 제외.
    """
    name = str(product_name)
    # 괄호 안 내용 제거 (색상 옵션 등): [브랜드]는 살리고 (베이지, 블랙)은 제거
    name_clean = re.sub(r"\([^)]*\)", "", name)
    
    # 영문 대문자/숫자/하이픈 3자 이상
    pattern = r"\b([A-Z][A-Z0-9\-]{2,})\b"
    candidates = re.findall(pattern, name_clean)
    
    # 너무 짧거나 숫자만인 것 제외
    codes = [c for c in candidates if len(c) >= 3 and not c.isdigit()]
    return codes


def extract_keywords(product_name):
    """매칭에 쓸 일반 키워드 추출.
    
    - [브랜드] 접두는 제거
    - 괄호 안 옵션 정보 제거
    - 2글자 이상 토큰만
    """
    name = str(product_name)
    # [브랜드] 접두 제거
    name = re.sub(r"^\[[^\]]+\]", "", name)
    # 괄호 안 옵션 제거
    name = re.sub(r"\([^)]*\)", "", name)
    
    tokens = name.split()
    return [t for t in tokens if len(t) >= 2]


def extract_units(product_name):
    """상품명에서 '숫자+단위' 패턴 추출."""
    pattern = r"(\d+(?:\.\d+)?)\s*(ml|l|g|kg|포|개|정|캡슐|환|매|입|병|봉|팩|세트)"
    matches = re.findall(pattern, str(product_name), flags=re.IGNORECASE)
    return [f"{num}{unit.lower()}" for num, unit in matches]


def match_score(product_name, item_title):
    """원본 상품명과 검색결과 제목의 일치도.
    
    우선순위:
    1. 모델 코드가 양쪽에 있고 일치 → 1.0
    2. 모델 코드가 원본에 있는데 결과에 없음 → 0.0
    3. 모델 코드 없는 경우: 단위 + 키워드 매칭
    """
    title_clean = clean_text(item_title)
    
    # 1단계: 모델 코드 매칭 (최우선)
    원본_코드들 = extract_model_code(product_name)
    if 원본_코드들:
        for code in 원본_코드들:
            if code in title_clean.upper():
                return 1.0
        return 0.0  # 모델 코드가 있는데 결과에 없으면 다른 상품
    
    # 2단계: 단위 엄격 검사
    원본_단위들 = extract_units(product_name)
    제목_단위들 = extract_units(title_clean)
    for unit in 원본_단위들:
        if unit not in 제목_단위들:
            return 0.0
    
    # 3단계: 키워드 매칭
    keywords = extract_keywords(product_name)
    if not keywords:
        return 0.0
    matched = sum(1 for kw in keywords if kw in title_clean)
    return matched / len(keywords)


# ============================================================
# 네이버 가격 조회
# ============================================================
def search_naver_price(product_name, brand_name):
    """네이버 검색 후 매칭/필터링하여 최저가와 가격 분포 반환.
    
    반환:
    {
        "status": "OK" | "NO_MATCH" | "API_LIMIT" | "ERROR",
        "price": int | None,         # 매칭된 최저가
        "total_count": int | None,   # 검색 결과 총 개수
        "all_prices": [int],         # 매칭된 후보들의 가격 리스트 (시장 풍부도/가격 변동성용)
        "message": str,
    }
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        return {"status": "ERROR", "price": None, "total_count": None, "all_prices": [], "message": "API 키 누락"}
    
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET}
    params = {"query": str(product_name), "display": 10, "sort": "asc"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return {"status": "ERROR", "price": None, "total_count": None, "all_prices": [], "message": f"네트워크 오류: {e}"}
    
    if response.status_code == 429:
        return {"status": "API_LIMIT", "price": None, "total_count": None, "all_prices": [], "message": "API 일일 한도 도달"}
    if response.status_code != 200:
        return {"status": "ERROR", "price": None, "total_count": None, "all_prices": [], "message": f"API 오류 {response.status_code}"}
    
    data = response.json()
    items = data.get("items", [])
    total_count = data.get("total", 0)
    
    if not items:
        return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [], "message": "네이버 검색 결과 0건"}
    
    # 1차 필터: productType 1(가격비교 묶음) 제외 + 브랜드 불일치 제외
    brand_clean = str(brand_name).strip() if brand_name else ""
    candidates = []
    for item in items:
        if item.get("productType") == "1":
            continue
        item_brand = clean_text(item.get("brand", ""))
        if item_brand and brand_clean and item_brand != brand_clean:
            continue
        candidates.append(item)
    
    if not candidates:
        return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [],
                "message": "조건 맞는 상품 없음 (네이버 묶음 또는 다른 브랜드)"}
    
    # 2차: 매칭 점수 계산
    scored = [{"item": i, "score": match_score(product_name, i["title"]), "price": int(i["lprice"])} for i in candidates]
    matched = [s for s in scored if s["score"] >= 0.7]
    
    if not matched:
        best = max(scored, key=lambda s: s["score"])
        # 모델 코드가 있었다면 더 친절한 메시지
        codes = extract_model_code(product_name)
        if codes:
            msg = f"모델명 {'/'.join(codes)} 일치 상품 없음. 수동 확인 필요."
        else:
            msg = f"매칭 신뢰도 낮음 (최고 {best['score']:.0%}). 수동 확인 필요."
        return {"status": "NO_MATCH", "price": None, "total_count": total_count, "all_prices": [], "message": msg}
    
    matched_prices = [s["price"] for s in matched]
    best_match = min(matched, key=lambda s: s["price"])
    
    return {
        "status": "OK",
        "price": best_match["price"],
        "total_count": total_count,
        "all_prices": sorted(matched_prices),
        "message": "정상",
    }


# ============================================================
# 가격 변동성 감지 (1위만 비정상적 저가인지)
# ============================================================
def detect_price_anomaly(prices):
    """매칭된 가격 리스트에서 1위만 비정상적으로 저렴한지 감지.
    
    규칙: 1위 가격 × 1.5 < 2위 가격 이면 1위가 이상치.
    가격이 2개 미만이면 판정 불가능 (None 반환).
    
    반환: (is_anomaly, msg)
    """
    if len(prices) < 2:
        return False, None
    
    first = prices[0]
    second = prices[1]
    
    if first * 1.5 < second:
        # 2위 이후 평균 계산
        rest_avg = int(statistics.mean(prices[1:]))
        msg = f"검색 1위만 {first:,}원으로 비정상적 저가, 2~{len(prices)}위 평균 {rest_avg:,}원. 일회성 특가 가능성, MD 확인 필요."
        return True, msg
    
    return False, None


# ============================================================
# 한 행 검수
# ============================================================
def validate_row(row):
    """새 양식 한 행을 검수.
    
    반환:
    {
        "_api_limit": bool,
        "결과": "승인 가능" | "확인 필요",
        "네이버 조회가": int | "—",
        "차이": str,
        "시장 풍부도": str,
        "검수 메모": str,
    }
    """
    상품명 = row.get(COL_MAP["name"])
    브랜드명 = row.get(COL_MAP["brand"])
    공급가 = row.get(COL_MAP["supply_price"])
    수수료율 = row.get(COL_MAP["commission_rate"])
    기재최저가 = row.get(COL_MAP["stated_lowest"])
    
    # 판매가는 수식이라 빈 값일 수 있음 → 직접 계산
    if pd.notna(공급가) and pd.notna(수수료율) and 수수료율 < 1:
        판매가 = round(공급가 / (1 - 수수료율), -1)  # 10원 단위 반올림
    else:
        판매가 = None
    
    # 상품명이 없으면 검수 불가
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
    naver = search_naver_price(상품명, 브랜드명)
    
    if naver["status"] == "API_LIMIT":
        return {"_api_limit": True}
    
    조회가 = naver["price"]
    total_count = naver["total_count"]
    all_prices = naver["all_prices"]
    
    # 시장 풍부도 표현 (옵션 A: 숫자만)
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
    
    # 가격 변동성 감지
    if naver["status"] == "OK" and all_prices:
        is_anomaly, anomaly_msg = detect_price_anomaly(all_prices)
        if is_anomaly:
            메모조각.append(anomaly_msg)
    
    # 기재가-조회가 정합성 (5% 초과 차이 시 메모)
    if 조회가 and pd.notna(기재최저가):
        차액 = int(기재최저가) - 조회가
        차이율_abs = abs(차액 / 조회가) * 100 if 조회가 else 0
        if 차이율_abs > 5:
            if 차액 > 0:
                메모조각.append(f"기재 최저가가 조회가보다 {차이율_abs:.1f}% 높음. 브랜드사에 정정 요청 필요.")
            else:
                메모조각.append(f"기재 최저가가 조회가보다 {차이율_abs:.1f}% 낮음. 브랜드사에 정정 요청 필요.")
    
    # 가격 경쟁력 (우리 판매가 vs 조회가)
    if 판매가 and 조회가 and 판매가 > 조회가:
        경쟁률 = ((판매가 - 조회가) / 조회가) * 100
        # 역산 공급가: 조회가를 이기려면 공급가가 얼마여야 하나
        if 수수료율 and 수수료율 < 1:
            목표공급가 = int(조회가 * (1 - 수수료율))
            메모조각.append(f"판매가 {int(판매가):,}원이 네이버 조회가 {조회가:,}원보다 {경쟁률:.1f}% 비쌈. 공급가 {목표공급가:,}원 이하로 협상 시 경쟁력 확보.")
    
    검수메모 = " / ".join(메모조각) if 메모조각 else "—"
    
    # 종합 결과 판정
    # 승인 가능 조건:
    #   - 매칭 성공 (status == OK)
    #   - AND 우리 판매가 ≤ 조회가 (가격 경쟁력)
    #   - AND 기재가-조회가 차이 ±5% 이내
    #   - AND 가격 변동성 신호 없음
    is_match_ok = naver["status"] == "OK"
    is_price_competitive = not (판매가 and 조회가 and 판매가 > 조회가)
    is_stated_close = True
    if 조회가 and pd.notna(기재최저가):
        is_stated_close = abs(int(기재최저가) - 조회가) / 조회가 * 100 <= 5
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
def validate_excel_file(file_path_or_bytes, progress_callback=None):
    """엑셀 파일 경로 또는 파일 객체를 받아 첫 시트를 검수.
    
    새 양식(애드웰)의 첫 시트('애드웰 상품견적서')를 처리한다.
    원본 헤더는 2행에 있으므로 header=1로 읽는다.
    
    progress_callback(idx, total, product_name) 형태로 진행률 알림.
    
    반환: (검수결과 리스트, 원본 DataFrame)
    각 원소는 dict (validate_row 반환값).
    """
    # 새 양식: 헤더가 2행에 있음 (1행은 제목, 2행이 실제 헤더)
    df = pd.read_excel(file_path_or_bytes, sheet_name=0, header=1)
    
    # 상품명이 있는 행만 검수 대상
    name_col = COL_MAP["name"]
    df_valid = df.dropna(subset=[name_col]).reset_index(drop=True)
    
    results = []
    for index, row in df_valid.iterrows():
        if progress_callback:
            progress_callback(index, len(df_valid), str(row[name_col]))
        
        result = validate_row(row)
        
        if result.get("_api_limit"):
            break
        
        result.pop("_api_limit", None)
        results.append(result)
    
    return results, df_valid