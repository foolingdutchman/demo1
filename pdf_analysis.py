import base64
import re
from collections import Counter
from io import BytesIO

from i18n import translate


PDF_EXTENSIONS = {".pdf"}
SHIPPING_DOCUMENT_KEYWORDS = {
    "bill_of_lading": (
        "bill of lading",
        "ocean bill",
        "b/l no",
        "bl no",
        "海运提单",
    ),
    "certificate_of_origin": (
        "certificate of origin",
        "origin certificate",
        "原产地证",
        "产地证",
    ),
    "phytosanitary_certificate": (
        "phytosanitary certificate",
        "phytosanitary",
        "plant quarantine certificate",
        "植检证",
        "植物检疫证书",
    ),
}


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


def analyze_shipping_document_text(text):
    normalized = re.sub(r"\s+", " ", text or "").strip()
    lowered = normalized.lower()
    scores = {
        category: sum(lowered.count(keyword.lower()) for keyword in keywords)
        for category, keywords in SHIPPING_DOCUMENT_KEYWORDS.items()
    }
    category = max(scores, key=scores.get) if scores and max(scores.values()) else None
    fields = {}
    field_patterns = {
        "document_number": (
            r"(?:B/?L|BL|CERTIFICATE)\s*(?:NO\.?|NUMBER)\s*[:：]?\s*([A-Z0-9/_.-]+)"
        ),
        "vessel": r"VESSEL(?:\s*&\s*VOYAGE)?\s*[:：]?\s*([^\n\r]{3,80})",
        "port_of_loading": (
            r"PORT\s+OF\s+LOADING\s*[:：]?\s*([^\n\r]{2,80})"
        ),
        "port_of_discharge": (
            r"PORT\s+OF\s+DISCHARGE\s*[:：]?\s*([^\n\r]{2,80})"
        ),
        "country_of_origin": (
            r"(?:COUNTRY|PLACE)\s+OF\s+ORIGIN\s*[:：]?\s*([^\n\r]{2,80})"
        ),
        "issue_date": (
            r"(?:DATE\s+OF\s+ISSUE|ISSUED\s+ON|DATE)\s*[:：]?\s*"
            r"(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2})"
        ),
        "consignee": r"CONSIGNEE\s*[:：]?\s*([^\n\r]{2,120})",
        "BL_NUMBER": (
            r"(?:(?:B/?L|BL)\s*(?:NO\.?|NUMBER)\s*|"
            r"BILL\s+OF\s+LADING\s+(?:NO\.?|NUMBER)\s*)"
            r"[:：]?\s*([A-Z0-9][A-Z0-9/_.-]{2,})"
        ),
        "BL_NET_WEIGHT": (
            r"(?:NET\s+WEIGHT|NET\s+WT\.?)\s*[:：]?\s*([\d,.]+)|"
            r"GROSS\s*/\s*NET(?:\s+WEIGHT)?\s*[:：]?\s*"
            r"[\d,.]+\s*/\s*([\d,.]+)"
        ),
        "GROSS_WEIGHT": (
            r"(?:GROSS\s+WEIGHT|GROSS\s+WT\.?|"
            r"GROSS\s*/\s*NET(?:\s+WEIGHT)?)"
            r"\s*[:：]?\s*([\d,.]+)"
        ),
        "WEIGHT_UNIT": (
            r"(?:NET\s+WEIGHT|NET\s+WT\.?|GROSS\s+WEIGHT|GROSS\s+WT\.?|"
            r"GROSS\s*/\s*NET(?:\s+WEIGHT)?)\s*[:：]?\s*[\d,.]+"
            r"(?:\s*/\s*[\d,.]+)?\s*(KGS?|KILOS?|MTS?|TONS?)"
        ),
        "DEPARTURE_DATE": (
            r"(?:ETD|DEPARTURE\s+DATE|DATE\s+OF\s+DEPARTURE|SAILED\s+ON)"
            r"\s*[:：]?\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
            r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|[A-Z]{3,9}\s+\d{1,2},?\s+\d{4})"
        ),
        "ARRIVAL_DATE": (
            r"(?:ETA|ARRIVAL\s+DATE|DATE\s+OF\s+ARRIVAL)"
            r"\s*[:：]?\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
            r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|[A-Z]{3,9}\s+\d{1,2},?\s+\d{4})"
        ),
        "PACK_NUMBER": (
            r"(?:NO\.?\s+OF\s+PACKAGES|PACKAGES?|BAGS?|BALES?)"
            r"\s*[:：]?\s*([\d,]+)"
        ),
        "PACK_UNIT": (
            r"(?:NO\.?\s+OF\s+PACKAGES|PACKAGES?)\s*[:：]?\s*[\d,]+\s*"
            r"(BAGS?|BALES?|PACKAGES?|PALLETS?|CASES?|CARTONS?)"
        ),
        "VOYAGE_NUMBER": (
            r"(?:VOYAGE|VOY\.?|VESSEL\s+VOYAGE)\s*(?:NO\.?|NUMBER)?\s*"
            r"[:：]?\s*([A-Z0-9/-]+)"
        ),
        "COO_INOVICE_NUMBER": (
            r"(?:COO\s+INVOICE|ORIGIN\s+INVOICE|INVOICE)"
            r"\s*(?:NO\.?|NUMBER)\s*[:：]?\s*([A-Z0-9/_.-]+)"
        ),
        "COO_INVOICE_DATE": (
            r"(?:COO\s+INVOICE|ORIGIN\s+INVOICE|INVOICE)\s*DATE"
            r"\s*[:：]?\s*(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
            r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})"
        ),
    }
    for field, pattern in field_patterns.items():
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            value = next(
                (group for group in match.groups() if group is not None),
                match.group(0),
            )
            fields[field] = re.sub(r"\s+", " ", value).strip(" :-\t")
    if category == "bill_of_lading":
        fields.setdefault("BL_NUMBER", fields.get("document_number", ""))
    return {
        "category": category,
        "category_label": {
            None: "unrecognized",
            "bill_of_lading": "bill_of_lading",
            "certificate_of_origin": "certificate_of_origin",
            "phytosanitary_certificate": "phytosanitary_certificate",
        }[category],
        "fields": fields,
        "text": text or "",
        "matched": category is not None,
        "waybill": (
            {
                field: fields.get(field, "")
                for field in (
                    "BL_NUMBER",
                    "BL_NET_WEIGHT",
                    "GROSS_WEIGHT",
                    "WEIGHT_UNIT",
                    "DEPARTURE_DATE",
                    "ARRIVAL_DATE",
                    "PACK_NUMBER",
                    "PACK_UNIT",
                    "VOYAGE_NUMBER",
                    "COO_INOVICE_NUMBER",
                    "COO_INVOICE_DATE",
                )
            }
            if category == "bill_of_lading"
            else None
        ),
    }


def analyze_pdf_shipping_document(pdf_bytes, locale="zh-CN"):
    page_count, text = extract_pdf_text(pdf_bytes, locale)
    needs_ocr = len(text.strip()) < 30
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(pdf_bytes))
        needs_ocr = needs_ocr or any(
            bool(getattr(page, "images", [])) for page in reader.pages
        )
    except Exception:
        pass
    ocr_text = ""
    if needs_ocr:
        try:
            from image_analysis import extract_document_ocr_text

            ocr_text = extract_document_ocr_text(pdf_bytes, ".pdf")
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            ocr_text = ""
    combined_text = "\n".join(
        part for part in (text, ocr_text) if part.strip()
    ).strip()
    analysis = analyze_shipping_document_text(combined_text)
    analysis["source"] = "pdf"
    analysis["page_count"] = page_count
    analysis["text_source"] = "text_and_ocr" if text and ocr_text else (
        "ocr" if ocr_text else "text"
    )
    return analysis


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
