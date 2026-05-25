"""
검수 결과 DataFrame을 서식 적용된 엑셀 파일로 저장.
"""
from io import BytesIO
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def save_to_excel_bytes(df):
    """DataFrame을 엑셀로 변환하여 bytes로 반환 (Streamlit 다운로드용)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "검수결과"
    
    summary_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    header_fill = PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid")
    check_header_fill = PatternFill(start_color="DEEBF7", end_color="DEEBF7", fill_type="solid")
    ok_fill = PatternFill(start_color="EAF3DE", end_color="EAF3DE", fill_type="solid")
    review_fill = PatternFill(start_color="FAEEDA", end_color="FAEEDA", fill_type="solid")
    
    thin_border = Border(
        left=Side(style="thin", color="CCCCCC"),
        right=Side(style="thin", color="CCCCCC"),
        top=Side(style="thin", color="CCCCCC"),
        bottom=Side(style="thin", color="CCCCCC"),
    )
    
    승인_가능 = (df["종합결과"] == "승인 가능").sum()
    확인_필요 = (df["종합결과"] == "확인 필요").sum()
    검수시각 = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 1행: 요약
    ws.cell(row=1, column=1, value=f"[검수 요약]  전체 {len(df)}건  |  승인 가능 {승인_가능}건  |  확인 필요 {확인_필요}건  |  검수시각 {검수시각}")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(df.columns))
    summary_cell = ws.cell(row=1, column=1)
    summary_cell.fill = summary_fill
    summary_cell.font = Font(bold=True, size=11)
    summary_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 25
    
    원본_컬럼수 = 8
    
    # 3행: 헤더
    for col_idx, col_name in enumerate(df.columns, start=1):
        cell = ws.cell(row=3, column=col_idx, value=col_name)
        cell.font = Font(bold=True)
        cell.fill = check_header_fill if col_idx > 원본_컬럼수 else header_fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    
    # 4행~: 데이터
    for row_idx, (_, row) in enumerate(df.iterrows(), start=4):
        is_review = row["종합결과"] == "확인 필요"
        row_fill = review_fill if is_review else ok_fill
        
        for col_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin_border
            if col_idx > 원본_컬럼수:
                cell.fill = row_fill
            if df.columns[col_idx-1] == "종합결과":
                cell.font = Font(bold=True)
    
    widths = {
        "No": 5, "브랜드명": 10, "상품명": 35, "원가": 12, 
        "플랫폼 수수료 (15%)": 16, "판매가": 12, "네이버최저가": 12, "네이버최저가 URL": 30,
        "종합결과": 11, "산식검증": 14, "네이버조회가": 13, "차이": 18, "검수메모": 50,
    }
    for col_idx, col_name in enumerate(df.columns, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = widths.get(col_name, 15)
    
    ws.freeze_panes = "D4"
    
    # bytes로 변환
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()