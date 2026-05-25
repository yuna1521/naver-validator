import pandas as pd

# 엑셀 파일 읽기
df = pd.read_excel("테스트_정관장.xlsx")

# 상품명이 비어있는 행 제거 (실제 데이터만 남김)
df = df.dropna(subset=["상품명"])

print(f"=== 검수 대상: {len(df)}개 상품 ===\n")


def validate_formula(원가, 수수료, 판매가):
    """수수료와 판매가의 산식을 검증한다.
    
    규칙:
    - 수수료 = 원가 × 0.15
    - 판매가 = 원가 + 수수료
    
    반환값: (산식검증 결과, 검수 메모)
    """
    # 빈 값(NaN)이 하나라도 있으면 검증 불가
    if pd.isna(원가) or pd.isna(수수료) or pd.isna(판매가):
        return "데이터 누락", "원가/수수료/판매가 중 빈 값 있음"
    
    # 1원 이내의 오차는 허용 (반올림 차이 대응)
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
        if 차액 > 0:
            메모_조각.append(f"판매가가 원가+수수료보다 높음 ({int(차액):,}원)")
        else:
            메모_조각.append(f"판매가가 원가+수수료보다 낮음 ({int(abs(차액)):,}원)")
    
    # 결과 라벨 결정
    if not 수수료_정확함:
        결과 = "수수료율 불일치"
    else:
        결과 = "판매가 불일치"
    
    return 결과, " / ".join(메모_조각)


# 각 행 검수
for index, row in df.iterrows():
    상품명 = row["상품명"]
    원가 = row["원가"]
    수수료 = row["플랫폼 수수료 (15%)"]
    판매가 = row["판매가"]
    
    결과, 메모 = validate_formula(원가, 수수료, 판매가)
    
    print(f"[{상품명}]")
    
    # 빈 값이 있을 수 있으므로 안전하게 출력
    원가_표시 = f"{int(원가):,}" if not pd.isna(원가) else "(빈 값)"
    수수료_표시 = f"{int(수수료):,}" if not pd.isna(수수료) else "(빈 값)"
    판매가_표시 = f"{int(판매가):,}" if not pd.isna(판매가) else "(빈 값)"
    
    print(f"  원가: {원가_표시} / 수수료: {수수료_표시} / 판매가: {판매가_표시}")
    print(f"  산식검증: {결과}")
    if 메모:
        print(f"  검수메모: {메모}")
    print()