"""
네이버 최저가 자동 검수 — Streamlit 웹 화면.
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from core import validate_dataframe, set_credentials
from excel_writer import save_to_excel_bytes

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
    # 로컬에서는 secrets 없으니 .env로 진행 (이미 core가 처리)
    pass

st.title("📋 입점 상품 최저가 검수")
st.caption("브랜드사 제출 엑셀을 업로드하면 자동으로 검수합니다.")

# ============================================================
# 사이드바: 안내
# ============================================================
with st.sidebar:
    st.header("사용 안내")
    st.markdown("""
    **1.** 브랜드사 제출 엑셀(.xlsx) 업로드
    
    **2.** "검수 시작" 버튼 클릭
    
    **3.** 검수 완료 후 결과 다운로드
    
    ---
    
    **필수 컬럼**
    - No, 브랜드명, 상품명
    - 원가, 플랫폼 수수료 (15%), 판매가
    - 네이버최저가, 네이버최저가 URL
    """)
    
    st.divider()
    
    test_mode = st.checkbox("테스트 모드 (처음 10건만 처리)", value=True)
    test_limit = st.number_input("처리 건수", min_value=1, max_value=100, value=10, disabled=not test_mode)

# ============================================================
# 1) 파일 업로드
# ============================================================
uploaded_file = st.file_uploader(
    "엑셀 파일 업로드",
    type=["xlsx"],
    help="브랜드사 제출 양식 그대로 업로드하세요."
)

if not uploaded_file:
    st.info("👆 위에 엑셀 파일을 끌어다 놓거나 클릭하여 업로드하세요.")
    st.stop()

# ============================================================
# 2) 파일 미리보기
# ============================================================
try:
    df_preview = pd.read_excel(uploaded_file)
except Exception as e:
    st.error(f"엑셀 파일을 읽지 못했습니다: {e}")
    st.stop()

df_preview_clean = df_preview.dropna(subset=["상품명"]) if "상품명" in df_preview.columns else df_preview

st.success(f"파일 업로드 완료: **{uploaded_file.name}**")
st.markdown(f"**검수 대상: {len(df_preview_clean)}개 상품**")

with st.expander("업로드된 파일 미리보기 (처음 5행)"):
    st.dataframe(df_preview_clean.head(5), use_container_width=True)

# ============================================================
# 3) 검수 실행
# ============================================================
start_button = st.button("🔍 검수 시작", type="primary", use_container_width=True)

if not start_button:
    st.stop()

# 테스트 모드 적용
df_to_validate = df_preview_clean.copy()
if test_mode and len(df_to_validate) > test_limit:
    df_to_validate = df_to_validate.head(test_limit)
    st.warning(f"⚠️ 테스트 모드: 처음 {test_limit}건만 처리합니다.")

# 진행률 UI
progress_bar = st.progress(0, text="검수 준비 중...")
status_text = st.empty()

def update_progress(idx, total, product_name):
    progress = (idx + 1) / total
    progress_bar.progress(progress, text=f"[{idx+1}/{total}] {product_name}")
    status_text.markdown(f"처리 중: **{product_name}**")

# 실제 검수 실행
with st.spinner("검수 진행 중..."):
    try:
        result_df = validate_dataframe(df_to_validate, progress_callback=update_progress)
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

# 메트릭
승인_가능 = (result_df["종합결과"] == "승인 가능").sum()
확인_필요 = (result_df["종합결과"] == "확인 필요").sum()

col1, col2, col3 = st.columns(3)
col1.metric("전체 상품", f"{len(result_df)}건")
col2.metric("승인 가능", f"{승인_가능}건")
col3.metric("확인 필요", f"{확인_필요}건")

# 필터
filter_option = st.radio(
    "필터",
    ["전체", "확인 필요만", "승인 가능만"],
    horizontal=True,
)

if filter_option == "확인 필요만":
    display_df = result_df[result_df["종합결과"] == "확인 필요"]
elif filter_option == "승인 가능만":
    display_df = result_df[result_df["종합결과"] == "승인 가능"]
else:
    display_df = result_df

# 표
def highlight_row(row):
    color = "#FAEEDA" if row["종합결과"] == "확인 필요" else "#EAF3DE"
    return [f"background-color: {color}"] * len(row)

styled = display_df.style.apply(highlight_row, axis=1)
st.dataframe(styled, use_container_width=True, height=400)

# ============================================================
# 5) 다운로드
# ============================================================
st.divider()

excel_bytes = save_to_excel_bytes(result_df)
filename = f"검수결과_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

st.download_button(
    label="📥 엑셀 다운로드",
    data=excel_bytes,
    file_name=filename,
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    use_container_width=True,
)