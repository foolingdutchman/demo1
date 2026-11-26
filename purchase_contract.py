from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont


TEMPLATE_PATH = Path(__file__).with_name("templates") / "purchase_contract_timplate.md"
USD_PER_TON_PER_USC_LB = Decimal("22.0462")
PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 42
INK = colors.black
NAVY = colors.black
TEAL = colors.black
PALE_TEAL = colors.white
BORDER = colors.transparent


class ContractGenerationError(ValueError):
    pass


def _text(value, default="—"):
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _decimal(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ContractGenerationError("Invalid contract amount") from error
    if not number.is_finite() or number <= 0:
        raise ContractGenerationError("Contract amounts must be positive")
    return number


def _number(value, places=2, default="—"):
    if value is None or value == "":
        return default
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return default
    if not number.is_finite():
        return default
    rendered = f"{number:.{places}f}".rstrip("0").rstrip(".")
    return rendered or "0"


def build_contract_context(order, username, profile=None, party_role="direct"):
    profile = profile or {}
    quantity = _decimal(order.get("quantity_tons"))
    price_usd_per_ton = _decimal(order.get("supplier_price_usd_per_ton"))
    product_type = (
        order.get("product_type")
        or order.get("source_quotation_product")
        or "cotton"
    )
    product = {
        "cotton": "COTTON",
        "sesame": "SESAME",
        "peanut": "PEANUT",
        "peanut_meal": "PEANUT MEAL",
    }.get(product_type, _text(order.get("product")).upper())
    is_cotton = product_type == "cotton"
    quotation_date = _text(order.get("quotation_date"), default="")
    created_at = _text(order.get("created_at"), default="")
    user_company = profile.get("company_name") or username
    user_address = profile.get("company_address")
    if party_role == "supplier_intermediary":
        seller = order.get("supplier_company_name") or order.get("processor") or order.get("origin")
        seller_address = order.get("supplier_address")
        buyer = order.get("intermediary_company_name")
        buyer_address = order.get("intermediary_address")
        number_suffix = "-A"
    elif party_role == "intermediary_user":
        seller = order.get("intermediary_company_name")
        seller_address = order.get("intermediary_address")
        buyer = user_company
        buyer_address = user_address
        number_suffix = "-B"
    else:
        seller = order.get("supplier_company_name") or order.get("processor") or order.get("origin")
        seller_address = order.get("supplier_address")
        buyer = user_company
        buyer_address = user_address
        number_suffix = ""
    return {
        "contract_no": f"PC-{int(order['id']):06d}{number_suffix}",
        "date": created_at[:10] or quotation_date,
        "seller": _text(seller),
        "seller_address": _text(seller_address),
        "buyer": _text(buyer),
        "buyer_address": _text(buyer_address),
        "product": product,
        "crop_year": quotation_date[:4] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", quotation_date) else "—",
        "staple_length": _number(order.get("length_mm")),
        "mic": _number(order.get("micronaire")),
        "strength": _number(order.get("strength_min"), default=_number(order.get("strength_average"))),
        "quantity": _number(quantity, 3),
        "shipment_date": _text(order.get("delivery_date")),
        "unit_price": _number(
            price_usd_per_ton / USD_PER_TON_PER_USC_LB
            if is_cotton else price_usd_per_ton,
            2,
        ),
        "unit_price_currency": "USC/LB" if is_cotton else "USD/MT",
        "price_term": _text(order.get("supplier_quote_basis"), default="CIF"),
        "amount": f"{quantity * price_usd_per_ton:,.2f}",
    }


def render_contract_text(order, username, template_text=None, profile=None, party_role="direct"):
    try:
        template = template_text if template_text is not None else TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractGenerationError("Purchase contract template is unavailable") from error
    values = build_contract_context(order, username, profile, party_role)
    try:
        rendered = template.format(**values).strip()
    except (KeyError, ValueError) as error:
        raise ContractGenerationError("Purchase contract template is invalid") from error
    if re.search(r"\{[^{}]+\}", rendered):
        raise ContractGenerationError("Purchase contract contains unresolved fields")
    return rendered


def _paragraph(text, style):
    return Paragraph(escape(" ".join(text.replace("\t", " ").split())), style)


def _contract_styles(font_name):
    return {
        "title": ParagraphStyle(
            "ContractTitle",
            fontName=font_name,
            fontSize=18,
            leading=24,
            alignment=TA_CENTER,
            textColor=NAVY,
            spaceAfter=14,
        ),
        "meta": ParagraphStyle(
            "ContractMeta",
            fontName=font_name,
            fontSize=9,
            leading=13,
            textColor=INK,
        ),
        "party_label": ParagraphStyle(
            "PartyLabel",
            fontName=font_name,
            fontSize=8.5,
            leading=11,
            textColor=INK,
        ),
        "party_value": ParagraphStyle(
            "PartyValue",
            fontName=font_name,
            fontSize=10,
            leading=14,
            textColor=INK,
        ),
        "intro": ParagraphStyle(
            "ContractIntro",
            fontName=font_name,
            fontSize=10,
            leading=15,
            alignment=TA_JUSTIFY,
            textColor=INK,
            spaceAfter=9,
        ),
        "clause": ParagraphStyle(
            "ContractClause",
            fontName=font_name,
            fontSize=9.5,
            leading=14.5,
            textColor=NAVY,
            spaceBefore=5,
            spaceAfter=2,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ContractBody",
            fontName=font_name,
            fontSize=9.5,
            leading=14.5,
            alignment=TA_JUSTIFY,
            textColor=INK,
            leftIndent=14,
            firstLineIndent=0,
            spaceAfter=3,
        ),
        "subclause": ParagraphStyle(
            "ContractSubclause",
            fontName=font_name,
            fontSize=9,
            leading=13.5,
            alignment=TA_JUSTIFY,
            textColor=INK,
            leftIndent=28,
            firstLineIndent=-12,
            spaceAfter=2,
        ),
        "signature_label": ParagraphStyle(
            "SignatureLabel",
            fontName=font_name,
            fontSize=9,
            leading=13,
            alignment=TA_LEFT,
            textColor=NAVY,
        ),
    }


def _page_decorator(contract_no):
    def decorate(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, PAGE_HEIGHT - 26, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 26)
        canvas.setFont("STSong-Light", 7.5)
        canvas.setFillColor(colors.black)
        canvas.drawString(MARGIN, 22, f"PURCHASE CONTRACT  |  {contract_no}")
        canvas.drawRightString(
            PAGE_WIDTH - MARGIN,
            22,
            f"Page {document.page}",
        )
        canvas.restoreState()

    return decorate


def _contract_flowables(text, styles, context):
    lines = text.splitlines()
    flowables = [_paragraph(lines[0], styles["title"])]
    metadata = [
        [
            _paragraph("CONTRACT NO.", styles["party_label"]),
            _paragraph(context["contract_no"], styles["party_value"]),
            _paragraph("DATE", styles["party_label"]),
            _paragraph(context["date"], styles["party_value"]),
        ],
    ]
    metadata_table = Table(metadata, colWidths=[76, 155, 42, 107])
    metadata_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_TEAL),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flowables.extend([metadata_table, Spacer(1, 12)])

    parties = Table([
        [_paragraph("SELLER", styles["party_label"]),
         _paragraph(context["seller"], styles["party_value"])],
        [_paragraph("ADD.", styles["party_label"]),
         _paragraph(context["seller_address"], styles["party_value"])],
        [_paragraph("BUYER", styles["party_label"]),
         _paragraph(context["buyer"], styles["party_value"])],
        [_paragraph("ADD.", styles["party_label"]),
         _paragraph(context["buyer_address"], styles["party_value"])],
    ], colWidths=[62, 318])
    parties.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, -1), PALE_TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flowables.extend([parties, Spacer(1, 12)])

    index = 1
    in_body = False
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        index += 1
        if not stripped:
            flowables.append(Spacer(1, 3))
            continue
        if stripped.startswith(("Contract No.:", "Seller:", "Add:", "Buyer:")):
            continue
        if stripped.startswith("The Seller "):
            seller_label = Paragraph(
                f"Seller:<br/>{escape(context['seller'])}",
                styles["signature_label"],
            )
            buyer_label = Paragraph(
                f"Buyer:<br/>{escape(context['buyer'])}",
                styles["signature_label"],
            )
            signature = Table([
                [seller_label, buyer_label],
                [Spacer(1, 34), Spacer(1, 34)],
                [_paragraph("Signature / Company Stamp", styles["meta"]),
                 _paragraph("Signature / Company Stamp", styles["meta"])],
            ], colWidths=[190, 190])
            signature.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("LINEBELOW", (0, 1), (0, 1), 0.35, BORDER),
                ("LINEBELOW", (1, 1), (1, 1), 0.35, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            flowables.extend([Spacer(1, 14), KeepTogether([signature])])
            continue
        if stripped.startswith("The buyer hereby confirmed"):
            in_body = True
            flowables.append(_paragraph(stripped, styles["intro"]))
            continue
        if not in_body:
            continue
        if re.match(r"^\d+\)", stripped):
            flowables.append(_paragraph(stripped, styles["clause"]))
        elif re.match(r"^\d+\.", stripped):
            flowables.append(_paragraph(stripped, styles["subclause"]))
        else:
            style = styles["subclause"] if raw[:1].isspace() else styles["body"]
            flowables.append(_paragraph(stripped, style))
    return flowables


def build_contract_pdf(order, username, profile=None, party_role="direct"):
    text = render_contract_text(
        order, username, profile=profile, party_role=party_role
    )
    pdf = BytesIO()
    font_name = "STSong-Light"
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    context = build_contract_context(order, username, profile, party_role)
    document = SimpleDocTemplate(
        pdf,
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT),
        rightMargin=MARGIN,
        leftMargin=MARGIN,
        topMargin=36,
        bottomMargin=40,
        title=f"Purchase Contract {context['contract_no']}",
        author=context["buyer"],
    )
    styles = _contract_styles(font_name)
    page_decorator = _page_decorator(context["contract_no"])
    document.build(
        _contract_flowables(text, styles, context),
        onFirstPage=page_decorator,
        onLaterPages=page_decorator,
    )
    return pdf.getvalue()
