import os
import re
import requests
from dotenv import load_dotenv

# .env 파일에서 API 키 불러오기
load_dotenv()
CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# API 키가 제대로 불러와졌는지 확인
if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: .env 파일에서 API 키를 불러오지 못했습니다.")
    print(".env 파일이 작업 폴더에 있는지, 내용이 정확한지 확인하세요.")
    exit()

print(f"API 키 불러오기 성공 (Client ID: {CLIENT_ID[:4]}...)\n")


def clean_text(text):
    """HTML 태그(<b>, </b> 등)를 제거하고 양 끝 공백 제거."""
    text = re.sub(r"<[^>]+>", "", str(text))
    return text.strip()


def extract_keywords(product_name):
    """상품명에서 매칭에 쓸 핵심 키워드를 추출한다.
    
    규칙:
    - 공백 기준으로 나눈다
    - 너무 짧은 단어(1글자)는 제외
    - 단위 정보(70ml, 30포, 200g 등)는 살린다
    """
    tokens = product_name.split()
    keywords = [t for t in tokens if len(t) >= 2]
    return keywords


def extract_units(product_name):
    """상품명에서 '숫자+단위' 패턴을 모두 추출.
    
    예: '정관장 화애락 70ml 30포' → ['70ml', '30포']
    
    인식하는 단위: ml, l, g, kg, 포, 개, 정, 캡슐, 환, 매, 입, 병, 봉, 팩
    """
    pattern = r"(\d+(?:\.\d+)?)\s*(ml|l|g|kg|포|개|정|캡슐|환|매|입|병|봉|팩)"
    matches = re.findall(pattern, product_name, flags=re.IGNORECASE)
    # 공백 제거 + 소문자 통일
    return [f"{num}{unit.lower()}" for num, unit in matches]


def match_score(product_name, item_title):
    """원본 상품명과 검색결과 제목의 일치도를 계산한다.
    
    두 단계 검증:
    1. 단위 정보(수량/용량)는 반드시 모두 일치해야 함 (엄격)
    2. 일치하면 키워드 매칭 비율 계산
    
    반환값: 0.0 ~ 1.0
    단, 단위 불일치 시 무조건 0.0
    """
    title_clean = clean_text(item_title)
    
    # 1단계: 단위 정보 엄격 검사
    원본_단위들 = extract_units(product_name)
    제목_단위들 = extract_units(title_clean)
    
    for unit in 원본_단위들:
        if unit not in 제목_단위들:
            return 0.0  # 단위 하나라도 빠지면 즉시 비매칭
    
    # 2단계: 일반 키워드 매칭
    keywords = extract_keywords(product_name)
    if not keywords:
        return 0.0
    
    matched = sum(1 for kw in keywords if kw in title_clean)
    return matched / len(keywords)


def search_naver_price(product_name, brand_name):
    """네이버 쇼핑 검색 API로 상품의 최저가를 찾는다.
    
    반환값: dict
    {
        "status": "OK" | "NO_MATCH" | "API_LIMIT" | "ERROR",
        "price": 가격 (int) 또는 None,
        "title": 매칭된 상품 제목 또는 None,
        "match_score": 매칭 점수 (0.0~1.0),
        "message": 사람이 읽을 메시지
    }
    """
    url = "https://openapi.naver.com/v1/search/shop.json"
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET,
    }
    params = {
        "query": product_name,
        "display": 10,        # 상위 10개 가져오기
        "sort": "asc",        # 가격 오름차순
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return {
            "status": "ERROR",
            "price": None,
            "title": None,
            "match_score": 0.0,
            "message": f"네트워크 오류: {e}",
        }
    
    # API 한도 초과 또는 인증 오류 처리
    if response.status_code == 429:
        return {
            "status": "API_LIMIT",
            "price": None,
            "title": None,
            "match_score": 0.0,
            "message": "API 일일 한도 도달. 내일 자정 이후 재시도하세요.",
        }
    
    if response.status_code != 200:
        return {
            "status": "ERROR",
            "price": None,
            "title": None,
            "match_score": 0.0,
            "message": f"API 오류 (코드 {response.status_code}): {response.text[:200]}",
        }
    
    data = response.json()
    items = data.get("items", [])
    
    if not items:
        return {
            "status": "NO_MATCH",
            "price": None,
            "title": None,
            "match_score": 0.0,
            "message": "네이버 검색 결과 0건. 수동 확인 필요.",
        }
    
    # 필터링: 1차로 명백한 비매칭 제외
    candidates = []
    for item in items:
        # productType 1 (네이버 가격비교 묶음) 제외
        if item.get("productType") == "1":
            continue
        # 브랜드가 명시되어 있고 우리 브랜드와 다르면 제외
        item_brand = clean_text(item.get("brand", ""))
        if item_brand and brand_name and item_brand != brand_name:
            continue
        candidates.append(item)
    
    if not candidates:
        return {
            "status": "NO_MATCH",
            "price": None,
            "title": None,
            "match_score": 0.0,
            "message": "조건에 맞는 상품 없음 (네이버 묶음 또는 다른 브랜드만 검색됨). 수동 확인 필요.",
        }
    
    # 2차: 키워드 매칭 점수 계산
    scored = []
    for item in candidates:
        score = match_score(product_name, item["title"])
        scored.append({
            "item": item,
            "score": score,
            "price": int(item["lprice"]),
        })
    
    # 매칭 임계값: 70% 이상 키워드가 일치해야 동일 상품으로 간주
    MATCH_THRESHOLD = 0.7
    matched = [s for s in scored if s["score"] >= MATCH_THRESHOLD]
    
    if not matched:
        # 임계값 미달이라도 가장 매칭 점수 높은 것은 참고용으로 반환
        best = max(scored, key=lambda s: s["score"])
        return {
            "status": "NO_MATCH",
            "price": None,
            "title": clean_text(best["item"]["title"]),
            "match_score": best["score"],
            "message": f"매칭 신뢰도 낮음 (최고 점수 {best['score']:.0%}). 수동 확인 필요.",
        }
    
    # 매칭된 것 중 가격 최저값 선택
    best_match = min(matched, key=lambda s: s["price"])
    
    return {
        "status": "OK",
        "price": best_match["price"],
        "title": clean_text(best_match["item"]["title"]),
        "match_score": best_match["score"],
        "message": "정상 매칭",
    }


# ============================================================
# 테스트 실행
# ============================================================
if __name__ == "__main__":
    print("=== 네이버 검색 테스트 ===\n")
    
    # 우리 엑셀의 1번 상품으로 테스트
    test_cases = [
        {
            "brand": "정관장",
            "product": "정관장 화애락 후 활력포커스 70ml 30포",
            "expected": 162580,  # 브랜드사 기재가
        },
        {
            "brand": "정관장",
            "product": "정관장 홍삼정 밸런스 200g",
            "expected": 75000,
        },
    ]
    
    for case in test_cases:
        print(f"[검색] {case['product']}")
        result = search_naver_price(case["product"], case["brand"])
        
        print(f"  상태: {result['status']}")
        print(f"  메시지: {result['message']}")
        if result["price"]:
            print(f"  매칭된 상품: {result['title']}")
            print(f"  가격: {result['price']:,}원")
            print(f"  매칭 점수: {result['match_score']:.0%}")
            print(f"  브랜드사 기재가와 차이: {result['price'] - case['expected']:+,}원")
        print()