"""
검수 로직 핵심 모듈. Streamlit, CLI 등 어떤 UI에서도 호출 가능.
"""
import os
import re
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")


# ============================================================
# 텍스트 처리 헬퍼
# ============================================================
def clean_text(text):
    return re.sub(r"<[^>]+>", "", str(text)).strip()


def extract_keywords(product_name):
    return [t for t in product_name.split() if len(t) >= 2]


def extract_units(product_name):
    pattern = r"(\d+(?:\.\d+)?)\s*(ml|l|g|kg|포|개|정|캡슐|환|매|입|병|봉|팩)"
    matches = re.findall(pattern, product_name, flags=re.IGNORECASE)
    return [f"{num}{unit.lower()}" for num, unit in matches]


def match_score(product_name, item_title):
    title_clean = clean_text(item_title)
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
# 검수 1: 산식 검증
# ============================================================
def validate_formula(원가, 수수료, 판매가):
    if pd.isna(원가) or pd.isna(수수료) or pd.isna(판매가):
        return "데이터 누락", "원가/수수료/판매가 중 빈 값 있음"
    
    expected_수수료 = 원가 * 0.15
    expected_판매가 = 원가 + 수수료
    
    수수료_정확함 = abs(수수료 - expected_수수료) <= 1
    판매가_정확함 = abs(판매가 - expected_판매가) <= 1
    
    if 수수료_정확함 and 판매가_정확함:
        return "PASS", ""
    
    메모_조각 = []
    if not 수수료_정확함:
        실제_수수료율 = (수수료 / 원가) * 100
        메모_조각.append(f"수수료 {실제_수수료율:.1f}%")
    if not 판매가_정확함:
        차액 = 판매가 - expected_판매가
        방향 = "높음" if 차액 > 0 else "낮음"
        메모_조각.append(f"판매가가 원가+수수료보다 {방향} ({int(abs(차액)):,}원)")
    
    결과 = "수수료율 불일치" if not 수수료_정확함 else "판매가 불일치"
    return 결과, " / ".join(메모_조각)


# ============================================================
# 검수 2: 네이버 가격 조회
# ============================================================
def search_naver_price(product_name, brand_name):
    if not CLIENT_ID or not CLIENT_SECRET:
        return {"status": "ERROR", "price": None, "message": "API 키 누락"}
    
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {"X-Naver-Client-Id": CLIENT_ID, "X-Naver-Client-Secret": CLIENT_SECRET}
    params = {"query": product_name, "display": 10, "sort": "asc"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return {"status": "ERROR", "price": None, "message": f"네트워크 오류: {e}"}
    
    if response.status_code == 429:
        return {"status": "API_LIMIT", "price": None, "message": "API 일일 한도 도달"}
    if response.status_code != 200:
        return {"status": "ERROR", "price": None, "message": f"API 오류 {response.status_code}"}
    
    items = response.json().get("items", [])
    if not items:
        return {"status": "NO_MATCH", "price": None, "message": "네이버 검색 결과 0건"}
    
    candidates = []
    for item in items:
        if item.get("productType") == "1":
            continue
        item_brand = clean_text(item.get("brand", ""))
        if item_brand and brand_name and item_brand != brand_name:
            continue
        candidates.append(item)
    
    if not candidates:
        return {"status": "NO_MATCH", "price": None, "message": "조건 맞는 상품 없음"}
    
    scored = [{"item": i, "score": match_score(product_name, i["title"]), "price": int(i["lprice"])} for i in candidates]
    matched = [s for s in scored if s["score"] >= 0.7]
    
    if not matched:
        best = max(scored, key=lambda s: s["score"])
        return {"status": "NO_MATCH", "price": None, "message": f"매칭 신뢰도 낮음 ({best['score']:.0%})"}
    
    best_match = min(matched, key=lambda s: s["price"])
    return {"status": "OK", "price": best_match["price"], "message": "정상"}


# ============================================================
# 한 행 검수
# ============================================================
def validate_row(row):
    """한 상품(row)을 검수해서 결과 dict를 반환.
    
    반환:
    {
        "종합결과": "승인 가능" | "확인 필요",
        "산식검증": "PASS" | "수수료율 불일치" | ...,
        "네이버조회가": int | None,
        "차이": str,
        "검수메모": str,
        "_api_limit": bool  # API 한도 도달 여부
    }
    """
    원가 = row["원가"]
    수수료 = row["플랫폼 수수료 (15%)"]
    판매가 = row["판매가"]
    브랜드명 = str(row.get("브랜드명", "")).strip()
    상품명 = str(row["상품명"])
    
    # 검수 1: 산식
    산식결과, 산식메모 = validate_formula(원가, 수수료, 판매가)
    
    # 검수 2: 네이버 가격
    naver_result = search_naver_price(상품명, 브랜드명)
    
    if naver_result["status"] == "API_LIMIT":
        return {"_api_limit": True}
    
    네이버조회가 = naver_result["price"]
    
    # 차이 계산
    기재_네이버최저가 = row.get("네이버최저가")
    if 네이버조회가 and not pd.isna(기재_네이버최저가):
        차액 = int(기재_네이버최저가) - 네이버조회가
        차이율 = (차액 / 네이버조회가) * 100 if 네이버조회가 else 0
        차이표시 = f"{차액:+,} ({차이율:+.1f}%)"
    else:
        차이표시 = "—"
    
    # 종합 메모
    메모조각 = []
    if 산식메모:
        메모조각.append(산식메모)
    if naver_result["status"] != "OK":
        메모조각.append(naver_result["message"])
    if naver_result["status"] == "OK" and 네이버조회가 and not pd.isna(판매가):
        if 판매가 > 네이버조회가:
            차이율 = ((판매가 - 네이버조회가) / 네이버조회가) * 100
            역산원가 = int(네이버조회가 / 1.15)
            메모조각.append(
                f"판매가 {int(판매가):,}원이 네이버 조회가 {네이버조회가:,}원보다 {차이율:.1f}% 비쌈. 원가 {역산원가:,}원 이하로 협상 필요."
            )
    
    검수메모 = " / ".join(메모조각) if 메모조각 else ""
    
    # 종합 결과
    if (산식결과 == "PASS" and naver_result["status"] == "OK"
            and (pd.isna(판매가) or not 네이버조회가 or 판매가 <= 네이버조회가)):
        종합결과 = "승인 가능"
    else:
        종합결과 = "확인 필요"
    
    return {
        "_api_limit": False,
        "종합결과": 종합결과,
        "산식검증": 산식결과,
        "네이버조회가": 네이버조회가 if 네이버조회가 else "—",
        "차이": 차이표시,
        "검수메모": 검수메모 if 검수메모 else "—",
    }


# ============================================================
# 전체 검수
# ============================================================
def validate_dataframe(df, progress_callback=None):
    """전체 DataFrame 검수.
    
    progress_callback: 진행률 표시용 콜백 함수 (Streamlit에서 사용).
                       callback(현재_인덱스, 전체, 상품명) 형태로 호출됨.
    
    반환: 검수 결과가 추가된 DataFrame
    """
    df = df.dropna(subset=["상품명"]).reset_index(drop=True)
    results = []
    
    for index, row in df.iterrows():
        if progress_callback:
            progress_callback(index, len(df), row["상품명"])
        
        result = validate_row(row)
        
        if result.get("_api_limit"):
            # 한도 도달: 여기까지의 결과만 반환
            break
        
        # _api_limit 키 제거하고 결과에 추가
        result.pop("_api_limit", None)
        results.append(result)
    
    # 처리된 만큼만 자르기
    df = df.head(len(results)).copy()
    
    # 결과 컬럼 추가
    for col in ["종합결과", "산식검증", "네이버조회가", "차이", "검수메모"]:
        df[col] = [r[col] for r in results]
    
    return df