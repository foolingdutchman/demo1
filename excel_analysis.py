from io import BytesIO
import json
import zipfile

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from xlrd.biffh import XLRDError

from i18n import translate


class ExcelAnalysisError(Exception):
    pass


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
