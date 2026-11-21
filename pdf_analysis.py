import base64
import re
from collections import Counter
from io import BytesIO

from i18n import translate


PDF_EXTENSIONS = {".pdf"}


class PdfAnalysisError(Exception):
    pass


def extract_pdf_text(pdf_bytes, locale="zh-CN"):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as error:
        raise PdfAnalysisError(translate(locale, "pdf.missing_library")) from error

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except (PdfReadError, OSError, ValueError) as error:
        raise PdfAnalysisError(translate(locale, "pdf.read_error", error=error)) from error

    return len(reader.pages), text


def build_pdf_analysis(pdf_bytes, locale="zh-CN"):
    page_count, text = extract_pdf_text(pdf_bytes, locale)
    word_count = len(re.findall(r"\b[\w'-]+\b", text))
    tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    frequent = Counter(token for token in tokens if len(token) > 1).most_common(10)
    return {
        "type": "pdf",
        "page_count": page_count,
        "character_count": len(text),
        "word_count": word_count,
        "frequent_words": [
            {"word": word, "count": count} for word, count in frequent
        ],
        "text": text,
    }


def run_streamlit_app():
    import streamlit as st

    locale = st.session_state.get("pdf_language", "zh-CN")
    st.set_page_config(
        page_title=translate(locale, "pdf.title"), page_icon="📄", layout="wide"
    )
    locale = st.selectbox(
        translate(locale, "language.label"),
        ("zh-CN", "en"),
        format_func=lambda value: translate(
            value, "language.chinese" if value == "zh-CN" else "language.english"
        ),
        key="pdf_language",
    )
    st.title(translate(locale, "pdf.title"))
    st.write(translate(locale, "pdf.upload_intro"))

    uploaded_file = st.file_uploader(translate(locale, "pdf.upload"), type="pdf")
    if uploaded_file is None:
        return

    pdf_bytes = uploaded_file.getvalue()
    preview, analysis = st.columns(2)

    with preview:
        st.subheader(translate(locale, "pdf.preview"))
        pdf_data = base64.b64encode(pdf_bytes).decode("utf-8")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{pdf_data}" '
            'width="100%" height="700" type="application/pdf"></iframe>',
            unsafe_allow_html=True,
        )

    try:
        page_count, text = extract_pdf_text(pdf_bytes, locale)
    except PdfAnalysisError as error:
        st.error(str(error))
        return

    with analysis:
        st.subheader(translate(locale, "pdf.results"))
        st.metric(translate(locale, "pdf.pages"), page_count)
        st.metric(translate(locale, "pdf.characters"), len(text))
        st.metric(translate(locale, "pdf.words"), len(re.findall(r"\b[\w'-]+\b", text)))
        if text:
            st.text_area(translate(locale, "pdf.extracted_text"), text, height=500)
            tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())
            frequent = Counter(token for token in tokens if len(token) > 1).most_common(10)
            if frequent:
                words = ", ".join(f"{word} ({count})" for word, count in frequent)
                st.write(translate(locale, "pdf.frequent_words", words=words))
        else:
            st.info(translate(locale, "pdf.no_text"))


if __name__ == "__main__":
    run_streamlit_app()
