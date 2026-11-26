from datetime import datetime
from io import BytesIO
import json
import re
import zipfile

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from xlrd.biffh import XLRDError

from i18n import translate


EXCEL_EXTENSIONS = {".xls", ".xlsx"}
COTTON_QUOTATION_SHEETS = (
    {
        "key": "quotation_summary",
        "worksheet": "COTTON",
        "table": "cotton_quotation_summary",
        "product": "棉花",
        "columns": (
            ("image_label", "图片", "TEXT"),
            ("batch_number", "批号", "TEXT"),
            ("crop_year", "CROP", "TEXT"),
            ("processor", "加工单位", "TEXT"),
            ("origin", "产地", "TEXT"),
            ("package_count", "包数", "INTEGER"),
            ("gross_weight_tons", "毛重(吨)", "REAL"),
            ("tare_weight_kg", "皮重(kg)", "REAL"),
            ("net_weight_tons", "净重(吨)", "REAL"),
            ("standard_weight_tons", "公重(吨)", "REAL"),
            ("moisture_percent", "回潮率(%)", "REAL"),
            ("impurity_percent", "含杂率(%)", "REAL"),
            ("micronaire", "马克隆值", "REAL"),
            ("length_mm", "长度(mm)", "REAL"),
            ("uniformity_percent", "整齐度(%)", "REAL"),
            ("color_grade", "颜色级", "TEXT"),
            ("strength_average", "断裂比强度_平均", "REAL"),
            ("strength_min", "断裂比强度_最小", "REAL"),
            ("strength_max", "断裂比强度_最大", "REAL"),
            ("gross_price_cny_per_ton", "毛重单价(元/吨)", "REAL"),
            ("basis_cny_per_ton", "基差(元/吨)", "REAL"),
            ("red_note", "红字备注", "TEXT"),
            ("product", "产品", "TEXT"),
        ),
    },
)
QUOTATION_SHEETS = (
    *COTTON_QUOTATION_SHEETS,
    {
        "key": "sesame",
        "worksheet": "SESAME",
        "table": "sesame_quotation_summary",
        "product": "芝麻",
        "columns": (
            ("batch_number", "批号", "TEXT"),
            ("processor", "加工单位", "TEXT"),
            ("crop_year", "CROP", "TEXT"),
            ("origin", "产地", "TEXT"),
            ("package_count", "包数", "INTEGER"),
            ("gross_weight_tons", "毛重(吨)", "REAL"),
            ("tare_weight_kg", "皮重(kg)", "REAL"),
            ("net_weight_tons", "净重(吨)", "REAL"),
            ("oil_percent", "含油率(%)", "REAL"),
            ("ffa_percent", "FFA(%)", "REAL"),
            ("purity_percent", "净度(%)", "REAL"),
            ("admixture_percent", "Admixture（%）", "REAL"),
            ("moisture_percent", "Moisture(%)", "REAL"),
            ("price_usc_per_mt", "单价(USC/Mt)", "REAL"),
            ("red_note", "红字备注", "TEXT"),
            ("product", "产品", "TEXT"),
        ),
    },
    {
        "key": "peanut",
        "worksheet": "PEANUT",
        "table": "peanut_quotation_summary",
        "product": "花生",
        "columns": (
            ("batch_number", "批号", "TEXT"),
            ("processor", "加工单位", "TEXT"),
            ("crop_year", "CROP", "TEXT"),
            ("origin", "产地", "TEXT"),
            ("package_count", "包数", "INTEGER"),
            ("gross_weight_tons", "毛重(吨)", "REAL"),
            ("tare_weight_kg", "皮重(kg)", "REAL"),
            ("net_weight_tons", "净重(吨)", "REAL"),
            ("oil_percent", "含油率(%)", "REAL"),
            ("size_per_oz", "Size (per oz)", "REAL"),
            ("split_percent", "Split (%)", "REAL"),
            ("ffa_percent", "FFA(%)", "REAL"),
            ("purity_percent", "净度(%)", "REAL"),
            ("admixture_percent", "Admixture（%）", "REAL"),
            ("moisture_percent", "Moisture(%)", "REAL"),
            ("aflatoxin_ppb", "Alfatoxin (ppd)", "REAL"),
            ("price_usc_per_mt", "单价(USC/Mt)", "REAL"),
            ("red_note", "红字备注", "TEXT"),
            ("product", "产品", "TEXT"),
        ),
    },
    {
        "key": "peanut_meal",
        "worksheet": "PEANUT MEAL",
        "table": "peanut_meal_quotation_summary",
        "product": "花生粕",
        "columns": (
            ("batch_number", "批号", "TEXT"),
            ("processor", "加工单位", "TEXT"),
            ("crop_year", "CROP", "TEXT"),
            ("origin", "产地", "TEXT"),
            ("package_count", "包数", "INTEGER"),
            ("gross_weight_tons", "毛重(吨)", "REAL"),
            ("tare_weight_kg", "皮重(kg)", "REAL"),
            ("net_weight_tons", "净重(吨)", "REAL"),
            ("oil_percent", "含油率(%)", "REAL"),
            ("protein_percent", "Protein (%)", "REAL"),
            ("fiber_percent", "Fiber (%)", "REAL"),
            ("ffa_percent", "FFA(%)", "REAL"),
            ("admixture_percent", "Admixture（%）", "REAL"),
            ("moisture_percent", "Moisture(%)", "REAL"),
            ("aflatoxin_ppb", "Alfatoxin (ppd)", "REAL"),
            ("price_usc_per_mt", "单价(USC/Mt)", "REAL"),
            ("red_note", "红字备注", "TEXT"),
            ("product", "产品", "TEXT"),
        ),
    },
)
COTTON_QUOTATION_HEADER_ALIASES = {
    "批次号": "batch_number",
    "棉花批号": "batch_number",
    "产品名称": "product",
    "品名": "product",
    "加工厂": "processor",
    "生产单位": "processor",
    "轧花厂": "processor",
    "地区": "origin",
    "产地/地区": "origin",
    "产地（中文）": "origin",
    "总包数": "package_count",
    "毛重": "gross_weight_tons",
    "净重": "net_weight_tons",
    "公重": "standard_weight_tons",
    "马克隆": "micronaire",
    "长度": "length_mm",
    "整齐度": "uniformity_percent",
    "单价(元/吨)": "gross_price_cny_per_ton",
    "价格(元/吨)": "gross_price_cny_per_ton",
    "基差": "basis_cny_per_ton",
    "断裂比平均": "strength_average",
    "断裂比最小": "strength_min",
    "断裂比最大": "strength_max",
    "batch": "batch_number",
    "batchno": "batch_number",
    "batchnumber": "batch_number",
    "crop": "crop_year",
    "product": "product",
    "lotnumber": "batch_number",
    "processor": "processor",
    "manufacturer": "processor",
    "origin": "origin",
    "region": "origin",
    "packagecount": "package_count",
    "grossweightton": "gross_weight_tons",
    "netweightton": "net_weight_tons",
    "standardweightton": "standard_weight_tons",
    "micronaire": "micronaire",
    "lengthmm": "length_mm",
    "uniformity": "uniformity_percent",
    "grossprice(cny/ton)": "gross_price_cny_per_ton",
    "grossprice(cny/tonne)": "gross_price_cny_per_ton",
    "basis(cny/ton)": "basis_cny_per_ton",
    "basis(cny/tonne)": "basis_cny_per_ton",
}
COTTON_QUOTATION_REQUIRED_FIELDS = {"batch_number"}
COTTON_QUOTATION_CONTEXT_FIELDS = {"processor", "origin"}
COTTON_QUOTATION_SIGNAL_FIELDS = {
    "basis_cny_per_ton",
    "color_grade",
    "gross_price_cny_per_ton",
    "impurity_percent",
    "length_mm",
    "micronaire",
    "moisture_percent",
    "strength_average",
    "strength_max",
    "strength_min",
}


class ExcelAnalysisError(Exception):
    pass


def normalize_cotton_quotation_header(value):
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", "", str(value)).replace("（", "(").replace("）", ")").replace(
        "_", ""
    ).casefold()


def extract_cotton_quotation_rows(file_bytes):
    columns = COTTON_QUOTATION_SHEETS[0]["columns"]
    field_by_header = {
        normalize_cotton_quotation_header(header): column
        for column, header, _column_type in columns
    }
    field_by_header.update(
        {
            normalize_cotton_quotation_header(header): column
            for header, column in COTTON_QUOTATION_HEADER_ALIASES.items()
        }
    )
    best_candidate = None
    try:
        with pd.ExcelFile(BytesIO(file_bytes)) as workbook:
            for sheet_name in workbook.sheet_names:
                data = pd.read_excel(
                    workbook,
                    sheet_name=sheet_name,
                    header=None,
                    dtype=object,
                )
                for header_row, row in enumerate(
                    data.iloc[:15].itertuples(index=False, name=None)
                ):
                    header_map = {}
                    for column_index, header in enumerate(row):
                        field = field_by_header.get(
                            normalize_cotton_quotation_header(header)
                        )
                        if field is not None and field not in header_map.values():
                            header_map[column_index] = field
                    fields = set(header_map.values())
                    has_context = bool(fields & COTTON_QUOTATION_CONTEXT_FIELDS)
                    has_signal = bool(fields & COTTON_QUOTATION_SIGNAL_FIELDS)
                    if (
                        len(fields) >= 5
                        and COTTON_QUOTATION_REQUIRED_FIELDS.issubset(fields)
                        and has_context
                        and has_signal
                        and (
                            best_candidate is None
                            or len(fields) > len(best_candidate["header_map"])
                        )
                    ):
                        best_candidate = {
                            "data": data,
                            "header_map": header_map,
                            "header_row": header_row,
                            "score": len(fields),
                            "sheet_name": sheet_name,
                        }
    except (
        ImportError,
        InvalidFileException,
        OSError,
        ValueError,
        XLRDError,
        zipfile.BadZipFile,
    ) as error:
        raise ExcelAnalysisError(str(error)) from error

    if best_candidate is None:
        return None

    records = []
    data = best_candidate["data"]
    for source_row_number in range(best_candidate["header_row"] + 2, len(data) + 1):
        row = data.iloc[source_row_number - 1].tolist()
        values = {column: None for column, _header, _kind in columns}
        for column_index, field in best_candidate["header_map"].items():
            if column_index >= len(row):
                continue
            value = row[column_index]
            if pd.isna(value):
                continue
            if hasattr(value, "item") and not isinstance(value, (str, bytes)):
                value = value.item()
            if field == "batch_number":
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                value = str(value).strip()
            elif isinstance(value, datetime):
                value = value.isoformat()
            elif not isinstance(value, (int, float, str, bytes)):
                value = str(value)
            values[field] = value
        if any(value is not None for value in values.values()):
            records.append((source_row_number, values))

    return best_candidate["sheet_name"], records


def summarize_sheet(data, locale="zh-CN"):
    missing_summary = pd.DataFrame(
        {
            translate(locale, "excel.data_type"): data.dtypes.astype(str),
            translate(locale, "excel.missing_count"): data.isna().sum(),
            translate(locale, "excel.missing_percent"): (data.isna().mean() * 100).round(2),
        }
    )
    numeric_data = data.select_dtypes(include="number")
    numeric_summary = numeric_data.describe().T if not numeric_data.empty else None
    return missing_summary, numeric_data, numeric_summary


def build_excel_analysis(file_bytes, locale="zh-CN"):
    try:
        sheets = []
        with pd.ExcelFile(BytesIO(file_bytes)) as workbook:
            if not workbook.sheet_names:
                raise ValueError(translate(locale, "excel.no_sheets"))

            for sheet_name in workbook.sheet_names:
                data = pd.read_excel(workbook, sheet_name=sheet_name)
                missing_summary, _, numeric_summary = summarize_sheet(data, locale)
                preview = json.loads(
                    data.head(20).to_json(orient="split", date_format="iso")
                )
                missing = [
                    {
                        "column": str(column),
                        "data_type": str(missing_summary.loc[column].iloc[0]),
                        "count": int(missing_summary.loc[column].iloc[1]),
                        "percent": float(missing_summary.loc[column].iloc[2]),
                    }
                    for column in data.columns
                ]
                summary = None
                if numeric_summary is not None:
                    summary_frame = numeric_summary.rename_axis("column").reset_index()
                    summary = json.loads(
                        summary_frame.to_json(orient="split", date_format="iso")
                    )
                sheets.append(
                    {
                        "name": str(sheet_name),
                        "rows": len(data),
                        "columns": len(data.columns),
                        "preview": preview,
                        "missing": missing,
                        "numeric_summary": summary,
                    }
                )

        return {"type": "excel", "sheets": sheets}
    except (
        ImportError,
        InvalidFileException,
        OSError,
        ValueError,
        XLRDError,
        zipfile.BadZipFile,
    ) as error:
        raise ExcelAnalysisError(str(error)) from error


def extract_excel_text(file_bytes):
    try:
        sheets = []
        with pd.ExcelFile(BytesIO(file_bytes)) as workbook:
            if not workbook.sheet_names:
                raise ValueError("Workbook contains no worksheets")
            for sheet_name in workbook.sheet_names:
                data = pd.read_excel(workbook, sheet_name=sheet_name)
                sheets.append(
                    f"Sheet: {sheet_name}\n{data.to_csv(index=False, na_rep='')}"
                )
        return "\n\n".join(sheets)
    except (
        ImportError,
        InvalidFileException,
        OSError,
        ValueError,
        XLRDError,
        zipfile.BadZipFile,
    ) as error:
        raise ExcelAnalysisError(str(error)) from error


def run_streamlit_app():
    import streamlit as st

    locale = st.session_state.get("excel_language", "zh-CN")
    locale = st.selectbox(
        translate(locale, "language.label"),
        ("zh-CN", "en"),
        format_func=lambda value: translate(
            value, "language.chinese" if value == "zh-CN" else "language.english"
        ),
        key="excel_language",
    )
    st.title(translate(locale, "excel.title"))
    uploaded_file = st.file_uploader(
        translate(locale, "excel.upload"), type=["xlsx", "xls"]
    )
    if uploaded_file is None:
        return

    try:
        with pd.ExcelFile(BytesIO(uploaded_file.getvalue())) as workbook:
            if not workbook.sheet_names:
                raise ValueError(translate(locale, "excel.no_sheets"))
            sheet_name = st.selectbox(translate(locale, "excel.sheet"), workbook.sheet_names)
            data = pd.read_excel(workbook, sheet_name=sheet_name)
    except (
        ImportError,
        InvalidFileException,
        OSError,
        ValueError,
        XLRDError,
        zipfile.BadZipFile,
    ) as error:
        st.error(translate(locale, "excel.read_error", error=error))
        return

    st.subheader(translate(locale, "excel.preview"))
    st.dataframe(data.head(20), use_container_width=True)

    st.subheader(translate(locale, "excel.basic_info"))
    col1, col2 = st.columns(2)
    col1.metric(translate(locale, "excel.rows"), len(data))
    col2.metric(translate(locale, "excel.columns"), len(data.columns))
    missing_summary, numeric_data, numeric_summary = summarize_sheet(data, locale)
    st.write(translate(locale, "excel.column_details"))
    st.dataframe(missing_summary, use_container_width=True)

    if numeric_summary is not None:
        st.subheader(translate(locale, "excel.numeric_summary"))
        st.dataframe(numeric_summary, use_container_width=True)
        column = st.selectbox(
            translate(locale, "excel.select_column"), numeric_data.columns
        )
        st.bar_chart(numeric_data[column].dropna().value_counts().sort_index())
    else:
        st.info(translate(locale, "excel.no_numeric"))


if __name__ == "__main__":
    run_streamlit_app()
