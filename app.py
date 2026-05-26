"""
입점 상품 최저가 검수 — Streamlit 웹 화면.
새 양식(애드웰 상품제안서) 전용.
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from io import BytesIO
from core import validate_excel_file, set_credentials, COL_MAP
from excel_writer import append_results_to_excel

# 1) 페이지 설정 — 반드시 가장 먼저
st.set_page_config(
    page_title="입점 상품 최저가 검수",
    page_icon="📋",
    layout="wide",
)

# 2) Streamlit Cloud의 Secrets를 core에 주입 (있을 때만)
try:
    set_credentials(
        st.secrets.get("NAVER_CLIENT_ID"),
        st.secrets.get("NAVER_CLIENT_SECRET"),
    )
except Exception:
    pass


st.title("📋 입점 상품 최저가 검수")
st.caption("브랜드사 제출 상품제안서를 업로드하면 자동으로 네이버 최저가를 검수합니다.")

# ============================================================
# 사이드바
# ============================================================
with st.sidebar:
    st.header("사용 안내")
    st.markdown("""
    **1.** 브랜드사 제출 상품제안서(.xlsx) 업로드
    
    **2.** "검수 시작" 버튼 클릭
    
    **3.** 검수 결과 확인 후 엑셀 다운로드
    
    ---
    
    **검수 항목**
    - 기재 최저가 vs 실제 네이버 최저가
    - 우리 판매가의 시장 경쟁력
    - 검색 시장 풍부도
    - 가격 변동성 신호
    """)
    
    st.divider()
    
    test_mode = st.checkbox("테스트 모드 (처음 10건만 처리)", value=True)
    test_limit = st.number_input("처리 건수", min_value=1, max_value=100, value=10, disabled=not test_mode)

# ============================================================
# 1) 파일 업로드
# ============================================================
uploaded_file = st.file_uploader(
    "상품제안서 업로드",
    type=["xlsx"],
    help="애드웰 상품제안서 양식 그대로 업로드하세요."
)

if not uploaded_file:
    st.info("👆 위에 상품제안서 파일을 끌어다 놓거나 클릭하여 업로드하세요.")
    st.stop()

# 업로드된 파일의 bytes를 보관 (다운로드 시 원본 사용)
file_bytes = uploaded_file.getvalue()

# ============================================================
# 2) 파일 미리보기
# ============================================================
try:
    df_preview = pd.read_excel(BytesIO(file_bytes), sheet_name=0, header=1)
except Exception as e:
    st.error(f"엑셀 파일을 읽지 못했습니다: {e}")
    st.stop()

name_col = COL_MAP["name"]
if name_col not in df_preview.columns:
    st.error(f"필수 컬럼을 찾지 못했습니다. 양식이 '애드웰 상품제안서'와 일치하는지 확인해주세요.")
    st.stop()

df_valid = df_preview.dropna(subset=[name_col])

st.success(f"파일 업로드 완료: **{uploaded_file.name}**")
st.markdown(f"**검수 대상: {len(df_valid)}개 상품**")

with st.expander("업로드된 파일 미리보기 (처음 5행)"):
    # 미리보기용 가공: 수식 직접 계산 + 가격 콤마 처리
    preview_df = df_valid.head(5).copy()
    
    sale_col = COL_MAP["sale_price"]
    supply_col = COL_MAP["supply_price"]
    rate_col = COL_MAP["commission_rate"]
    discount_col = COL_MAP["discount_rate"]
    stated_col = COL_MAP["stated_lowest"]
    
    # 판매가 수식 직접 계산
    def fill_sale_price(row):
        existing = row.get(sale_col)
        if pd.notna(existing):
            return existing
        supply = row.get(supply_col)
        rate = row.get(rate_col)
        if pd.notna(supply) and pd.notna(rate) and rate < 1:
            return round(float(supply) / (1 - float(rate)), -1)
        return None
    
    preview_df[sale_col] = preview_df.apply(fill_sale_price, axis=1)
    
    # 할인율 수식 직접 계산
    def fill_discount_rate(row):
        existing = row.get(discount_col)
        if pd.notna(existing):
            return existing
        sale = row.get(sale_col)
        stated = row.get(stated_col)
        if pd.notna(sale) and pd.notna(stated) and stated > 0:
            return 1 - (float(sale) / float(stated))
        return None
    
    preview_df[discount_col] = preview_df.apply(fill_discount_rate, axis=1)
    
    # 가격 컬럼 콤마 처리
    price_cols = [
        COL_MAP["list_price"],
        COL_MAP["supply_price"],
        COL_MAP["sale_price"],
        COL_MAP["stated_lowest"],
    ]
    for col in price_cols:
        if col in preview_df.columns:
            preview_df[col] = preview_df[col].apply(
                lambda v: f"{int(round(float(v))):,}" if pd.notna(v) else "—"
            )
    
    # 할인율은 백분율 형식
    if discount_col in preview_df.columns:
        preview_df[discount_col] = preview_df[discount_col].apply(
            lambda v: f"{float(v)*100:.1f}%" if pd.notna(v) else "—"
        )
    
    st.dataframe(preview_df, use_container_width=True)

# ============================================================
# 3) 검수 실행
# ============================================================
start_button = st.button("🔍 검수 시작", type="primary", use_container_width=True)

if not start_button:
    st.stop()

if test_mode and len(df_valid) > test_limit:
    st.warning(f"⚠️ 테스트 모드: 처음 {test_limit}건만 처리합니다.")
    # 테스트 모드 적용을 위해 BytesIO에서 N건만 잘라낸 새 bytes를 만들기는 복잡하므로,
    # core에는 전체 파일을 주되, 결과만 처음 N건으로 자른다.
    # 단, excel_writer에는 전체 원본을 그대로 전달하면 됨 (테스트 모드는 검수 범위만 제한).

# 진행률 UI
progress_bar = st.progress(0, text="검수 준비 중...")
status_text = st.empty()

def update_progress(idx, total, product_name):
    progress = (idx + 1) / total if total > 0 else 1.0
    progress_bar.progress(progress, text=f"[{idx+1}/{total}] {product_name}")
    status_text.markdown(f"처리 중: **{product_name}**")

# 실제 검수 실행
limit_for_test = test_limit if test_mode else None

with st.spinner("검수 진행 중..."):
    try:
        # 테스트 모드일 때만 처리 건수 제한
        if limit_for_test:
            # 임시 DataFrame을 만들어 처음 N건만 검수
            from core import validate_row
            df_limited = df_valid.head(limit_for_test).reset_index(drop=True)
            results = []
            for idx, row in df_limited.iterrows():
                update_progress(idx, len(df_limited), str(row[name_col]))
                r = validate_row(row)
                if r.get("_api_limit"):
                    st.warning("⚠️ API 한도 도달. 지금까지의 결과만 표시합니다.")
                    break
                r.pop("_api_limit", None)
                results.append(r)
            df_validated = df_limited.head(len(results))
        else:
            results, df_validated = validate_excel_file(BytesIO(file_bytes), progress_callback=update_progress)
    except Exception as e:
        st.error(f"검수 중 오류 발생: {e}")
        st.stop()

progress_bar.progress(1.0, text="검수 완료")
status_text.empty()

# ============================================================
# 4) 결과 표시
# ============================================================
st.divider()
st.subheader("검수 결과")

승인_가능 = sum(1 for r in results if r["결과"] == "승인 가능")
확인_필요 = len(results) - 승인_가능

col1, col2, col3 = st.columns(3)
col1.metric("전체 상품", f"{len(results)}건")
col2.metric("승인 가능", f"{승인_가능}건")
col3.metric("확인 필요", f"{확인_필요}건")

# 필터
filter_option = st.radio(
    "필터",
    ["전체", "확인 필요만", "승인 가능만"],
    horizontal=True,
)

# 화면용 DataFrame 구성

def format_won(value):
    """숫자를 '8,624' 형태로 포맷. 빈 값은 '—'."""
    if value is None or value == "—":
        return "—"
    try:
        if pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value)


def compute_sale_price(supply, rate):
    """판매가 수식 직접 계산: 공급가 / (1 - 수수료율), 10원 단위 반올림."""
    try:
        if pd.isna(supply) or pd.isna(rate) or rate >= 1:
            return None
        return round(float(supply) / (1 - float(rate)), -1)
    except (TypeError, ValueError):
        return None


display_rows = []
for i, (_, row) in enumerate(df_validated.iterrows()):
    if i >= len(results):
        break
    r = results[i]
    
    공급가 = row.get(COL_MAP["supply_price"])
    수수료율 = row.get(COL_MAP["commission_rate"])
    판매가 = row.get(COL_MAP["sale_price"])
    # 판매가가 수식이라 None이면 직접 계산
    if 판매가 is None or (isinstance(판매가, float) and pd.isna(판매가)):
        판매가 = compute_sale_price(공급가, 수수료율)
    
    display_rows.append({
        "No": int(row.get(COL_MAP["no"])) if pd.notna(row.get(COL_MAP["no"])) else "—",
        "브랜드명": row.get(COL_MAP["brand"]),
        "상품명": row.get(COL_MAP["name"]),
        "공급가": format_won(공급가),
        "판매가": format_won(판매가),
        "기재 최저가 (A)": format_won(row.get(COL_MAP["stated_lowest"])),
        "결과": r["결과"],
        "네이버 조회가 (B)": format_won(r["네이버 조회가"]),
        "차이 (A − B)": r["차이"],
        "시장 풍부도": r["시장 풍부도"],
        "검수 메모": r["검수 메모"],
    })

display_df = pd.DataFrame(display_rows)

# 필터 적용
if filter_option == "확인 필요만":
    display_df = display_df[display_df["결과"] == "확인 필요"]
elif filter_option == "승인 가능만":
    display_df = display_df[display_df["결과"] == "승인 가능"]

# 색상 강조
def highlight_row(row):
    color = "#FAEEDA" if row["결과"] == "확인 필요" else "#EAF3DE"
    return [f"background-color: {color}"] * len(row)

styled = display_df.style.apply(highlight_row, axis=1)
st.dataframe(styled, use_container_width=True, height=420)

# ============================================================
# 5) 다운로드 — 원본 보존 + 우측에 검수 결과 추가
# ============================================================
st.divider()

try:
    output_bytes = append_results_to_excel(file_bytes, results)
    filename = uploaded_file.name.replace(".xlsx", f"_검수완료_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    
    st.download_button(
        label="📥 검수 결과가 추가된 엑셀 다운로드",
        data=output_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )
    st.caption("원본 파일은 그대로 보존되며, 검수 결과 5개 컬럼이 Q열부터 추가됩니다.")
except Exception as e:
    st.error(f"엑셀 생성 실패: {e}")