from io import BytesIO
import zipfile
from html import escape

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


def _render_table(data, index=False):
    return (
        '<div class="analysis-table-wrap">'
        + data.to_html(
            index=index,
            border=0,
            classes="analysis-table",
            escape=True,
            na_rep="",
        )
        + "</div>"
    )


def build_excel_analysis(file_bytes, locale="zh-CN"):
    try:
        sections = []
        with pd.ExcelFile(BytesIO(file_bytes)) as workbook:
            if not workbook.sheet_names:
                raise ValueError(translate(locale, "excel.no_sheets"))

            for sheet_name in workbook.sheet_names:
                data = pd.read_excel(workbook, sheet_name=sheet_name)
                missing_summary, _, numeric_summary = summarize_sheet(data, locale)
                if numeric_summary is not None:
                    numeric_summary.columns = [
                        translate(locale, f"excel.stat.{column}")
                        for column in numeric_summary.columns
                    ]
                sections.append(
                    f'<section class="analysis-sheet"><h3>{escape(sheet_name)}</h3>'
                    f'<p>{translate(locale, "excel.rows_columns", rows=f"{len(data):,}", columns=f"{len(data.columns):,}")}</p>'
                    f'<h4>{translate(locale, "excel.preview")}</h4>'
                    f'{_render_table(data.head(20))}'
                    f'<h4>{translate(locale, "excel.columns_missing")}</h4>'
                    f'{_render_table(missing_summary, index=True)}'
                )
                if numeric_summary is not None:
                    sections.extend(
                        (
                            f"<h4>{translate(locale, 'excel.numeric_summary')}</h4>",
                            _render_table(numeric_summary, index=True),
                        )
                    )
                else:
                    sections.append(
                        f"<p>{translate(locale, 'excel.no_numeric')}</p>"
                    )
                sections.append("</section>")

        return (
            f'<section class="analysis"><h2>{translate(locale, "analysis.excel")}</h2>'
            + "".join(sections)
            + "</section>"
        )
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
