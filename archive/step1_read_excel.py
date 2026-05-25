import pandas as pd

# 엑셀 파일 읽기
df = pd.read_excel("테스트_정관장.xlsx")

# 컬럼명 확인
print("=== 컬럼명 ===")
print(df.columns.tolist())
print()

# 전체 행 수 확인
print(f"=== 전체 행 수: {len(df)}개 ===")
print()

# 각 행을 하나씩 출력
print("=== 데이터 ===")
for index, row in df.iterrows():
    print(f"--- 행 {index + 1} ---")
    print(f"  상품명: {row['상품명']}")
    print(f"  원가: {row['원가']}")
    print(f"  수수료: {row['플랫폼 수수료 (15%)']}")
    print(f"  판매가: {row['판매가']}")
    print(f"  네이버최저가: {row['네이버최저가']}")
    print()