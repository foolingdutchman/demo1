from io import BytesIO
import zipfile
from html import escape

import pandas as pd
from openpyxl.utils.exceptions import InvalidFileException
from xlrd.biffh import XLRDError


class ExcelAnalysisError(Exception):
    pass


def summarize_sheet(data):
    missing_summary = pd.DataFrame(
        {
            "数据类型": data.dtypes.astype(str),
            "缺失值数量": data.isna().sum(),
            "缺失值比例(%)": (data.isna().mean() * 100).round(2),
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


def build_excel_analysis(file_bytes):
    try:
        sections = []
        with pd.ExcelFile(BytesIO(file_bytes)) as workbook:
            if not workbook.sheet_names:
                raise ValueError("工作簿中没有工作表")

            for sheet_name in workbook.sheet_names:
                data = pd.read_excel(workbook, sheet_name=sheet_name)
                missing_summary, numeric_data, numeric_summary = summarize_sheet(data)
                sections.append(
                    f'<section class="analysis-sheet"><h3>{escape(sheet_name)}</h3>'
                    f'<p>行数：{len(data):,}，列数：{len(data.columns):,}</p>'
                    '<h4>数据预览（前 20 行）</h4>'
                    f'{_render_table(data.head(20))}'
                    '<h4>列类型与缺失值</h4>'
                    f'{_render_table(missing_summary, index=True)}'
                )
                if numeric_summary is not None:
                    sections.extend(
                        (
                            "<h4>数值列统计</h4>",
                            _render_table(numeric_summary, index=True),
                        )
                    )
                else:
                    sections.append("<p>该工作表没有可分析的数值列。</p>")
                sections.append("</section>")

        return (
            '<section class="analysis"><h2>Excel 分析结果</h2>'
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

    st.title("Excel 文件分析")
    uploaded_file = st.file_uploader("上传 Excel 文件", type=["xlsx", "xls"])
    if uploaded_file is None:
        return

    try:
        with pd.ExcelFile(BytesIO(uploaded_file.getvalue())) as workbook:
            if not workbook.sheet_names:
                raise ValueError("工作簿中没有工作表")
            sheet_name = st.selectbox("选择工作表", workbook.sheet_names)
            data = pd.read_excel(workbook, sheet_name=sheet_name)
    except (
        ImportError,
        InvalidFileException,
        OSError,
        ValueError,
        XLRDError,
        zipfile.BadZipFile,
    ) as error:
        st.error(f"无法读取或分析此 Excel 文件：{error}")
        return

    st.subheader("数据预览")
    st.dataframe(data.head(20), use_container_width=True)

    st.subheader("基本信息")
    col1, col2 = st.columns(2)
    col1.metric("行数", len(data))
    col2.metric("列数", len(data.columns))
    missing_summary, numeric_data, numeric_summary = summarize_sheet(data)
    st.write("各列数据类型与缺失值")
    st.dataframe(missing_summary, use_container_width=True)

    if numeric_summary is not None:
        st.subheader("数值列统计")
        st.dataframe(numeric_summary, use_container_width=True)
        column = st.selectbox("选择数值列查看分布", numeric_data.columns)
        st.bar_chart(numeric_data[column].dropna().value_counts().sort_index())
    else:
        st.info("该工作表没有可分析的数值列。")


if __name__ == "__main__":
    run_streamlit_app()
