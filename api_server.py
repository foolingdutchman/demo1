from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, quote, urlsplit

import ai_analysis
import auth_manager
import db_manager
import image_analysis
import order_manager
import outcall_api
import purchase_contract
import quotation_manager
import products_pricing
from ai_analysis import TEXT_EXTENSIONS, decode_text_file
from excel_analysis import EXCEL_EXTENSIONS, ExcelAnalysisError, build_excel_analysis
from i18n import SUPPORTED_LOCALES, get_locale, get_messages, translate
from image_analysis import (
    IMAGE_MIME_TYPES,
    analyze_image_shipping_document,
    is_image_file,
)
from pdf_analysis import (
    PDF_EXTENSIONS,
    PdfAnalysisError,
    analyze_pdf_shipping_document,
    build_pdf_analysis,
)


LOCAL_ENV_PATH = Path(__file__).with_name(".env")


def load_local_environment():
    if not LOCAL_ENV_PATH.is_file():
        return

    for line_number, line in enumerate(
        LOCAL_ENV_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env entry on line {line_number}")

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid .env variable name on line {line_number}")
        if value[:1] in {"'", '"'} or value[-1:] in {"'", '"'}:
            if len(value) < 2 or value[0] != value[-1]:
                raise ValueError(f"Invalid .env value on line {line_number}")
            value = value[1:-1]
        os.environ.setdefault(name, value)


load_local_environment()
ai_analysis.load_ai_configuration()


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024
SERVER_HOST = os.environ.get("HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("PORT", "8000"))
CORS_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500",
    ).split(",")
    if origin.strip()
}
ANALYZING_RELATED_DOCUMENTS = set()
ANALYZING_RELATED_DOCUMENTS_LOCK = threading.Lock()


def build_preview(filename, upload_id, file_bytes, locale):
    extension = Path(filename).suffix.lower()
    if extension in PDF_EXTENSIONS:
        return {"type": "pdf", "url": f"/api/uploads/{upload_id}/content"}
    if is_image_file(filename):
        return image_analysis.build_image_preview(upload_id)
    if extension not in TEXT_EXTENSIONS:
        return {"type": "unsupported", "message": translate(locale, "preview.unsupported")}

    text = decode_text_file(file_bytes)
    if text is None:
        return {"type": "invalid_encoding", "message": translate(locale, "preview.invalid_encoding")}
    return {"type": "text", "text": text}


def build_analysis(filename, file_bytes, locale):
    extension = Path(filename).suffix.lower()
    if is_image_file(filename):
        return image_analysis.build_image_analysis(locale)
    if extension == ".pdf":
        try:
            return build_pdf_analysis(file_bytes, locale)
        except PdfAnalysisError as error:
            logging.warning("PDF analysis failed for %s: %s", filename, error)
            return {
                "type": "error",
                "message": translate(locale, "analysis.cannot_analyze", error=str(error)),
            }
    if extension in EXCEL_EXTENSIONS:
        try:
            return build_excel_analysis(file_bytes, locale)
        except ExcelAnalysisError as error:
            logging.warning("Excel analysis failed for %s: %s", filename, error)
            return {
                "type": "error",
                "message": translate(locale, "analysis.cannot_analyze", error=str(error)),
            }
    if extension not in TEXT_EXTENSIONS:
        return {"type": "unsupported", "message": translate(locale, "analysis.unsupported")}

    text = decode_text_file(file_bytes)
    if text is None:
        return {
            "type": "error",
            "message": translate(locale, "analysis.invalid_encoding"),
        }
    return {
        "type": "text",
        "line_count": len(text.splitlines()),
        "character_count": len(text),
        "word_count": len(text.split()),
    }


class ApiHandler(BaseHTTPRequestHandler):
    def _locale(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            language_cookie = None
        else:
            language_cookie = cookies.get("language")
        requested = parse_qs(urlsplit(self.path).query).get("locale", [None])[0]
        preferred = requested or (language_cookie.value if language_cookie else None)
        return get_locale(self.headers.get("Accept-Language", ""), preferred)

    def _t(self, key, **values):
        return translate(self._locale(), key, **values)

    def _add_cors_headers(self):
        origin = self.headers.get("Origin")
        if origin in CORS_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _send_json(self, data, status_code=200, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self._add_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status_code, code, message):
        self._send_json(
            {"error": {"code": code, "message": message}},
            status_code,
        )

    def _send_file(self, body, content_type, filename):
        self.send_response(200)
        self._add_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        self._send_error_json(
            code,
            "http_error",
            message or self.responses.get(code, ("Request failed",))[0],
        )

    def _read_json(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(400, "invalid_length", self._t("request.invalid_length"))
            return None
        if content_length <= 0 or content_length > MAX_JSON_BYTES:
            self._send_error_json(400, "invalid_size", self._t("request.invalid_form_size"))
            return None
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._send_error_json(400, "invalid_content_type", self._t("request.invalid_form_type"))
            return None
        try:
            fields = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error_json(400, "invalid_json", self._t("request.invalid_form_content"))
            return None
        if not isinstance(fields, dict) or any(
            not isinstance(value, str) for value in fields.values()
        ):
            self._send_error_json(400, "invalid_fields", self._t("request.invalid_form_content"))
            return None
        return fields

    def _require_user(self):
        try:
            user = auth_manager.get_current_user(self.headers.get("Cookie", ""))
        except db_manager.DatabaseError:
            logging.exception("Could not read the current session")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return None
        if user is None:
            self._send_error_json(401, "unauthorized", self._t("auth.login_required"))
            return None
        return user

    def _handle_auth(self):
        fields = self._read_json()
        if fields is None:
            return
        try:
            body, status_code, cookie = auth_manager.authenticate(fields)
        except auth_manager.AuthError as error:
            self._send_error_json(
                error.status,
                error.code,
                self._t(error.message_key),
            )
            return
        except db_manager.DatabaseError:
            logging.exception("Authentication database operation failed")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        self._send_json(body, status_code, cookie)

    def _handle_logout(self):
        try:
            cookie = auth_manager.logout(self.headers.get("Cookie", ""))
        except db_manager.DatabaseError:
            logging.exception("Could not delete the session")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        self._send_json(
            {"ok": True},
            cookie=cookie,
        )

    def _handle_market_prices(self):
        try:
            result = outcall_api.get_market_prices()
        except outcall_api.MarketDataUnavailable:
            self._send_error_json(
                502,
                "market_data_unavailable",
                self._t("market.unavailable"),
            )
            return
        self._send_json(result)

    def _handle_sudan_cotton_pricing(self):
        fields = self._read_json()
        if fields is None:
            return
        try:
            result = products_pricing.calculate_sudan_cotton_pricing(fields)
        except products_pricing.PricingInputError:
            self._send_error_json(
                400,
                "invalid_pricing_input",
                self._t("market.sudan_pricing_invalid_input"),
            )
            return
        self._send_json(result)

    def _handle_product_purchase_pricing(self):
        fields = self._read_json()
        if fields is None:
            return
        try:
            result = products_pricing.calculate_product_purchase_pricing(fields)
        except products_pricing.PricingInputError:
            self._send_error_json(
                400,
                "invalid_product_pricing_input",
                self._t("market.product_pricing_invalid_input"),
            )
            return
        self._send_json(result)

    def _handle_cotton_quotations(self, user_id):
        try:
            sheets = db_manager.get_cotton_quotations(user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not read cotton quotations")
            self._send_error_json(
                500,
                "database_error",
                self._t("request.database_read_error"),
            )
            return
        self._send_json({"sheets": sheets})

    def _handle_update_cotton_quotation(self, row_number, user_id):
        fields = self._read_json()
        if fields is None:
            return
        try:
            values = quotation_manager.validate_fields(fields)
        except (ValueError, OverflowError):
            self._send_error_json(400, "invalid_quotation", self._t("quotation.invalid"))
            return
        try:
            updated = db_manager.update_cotton_quotation(row_number, user_id, values)
        except db_manager.DatabaseError:
            logging.exception("Could not update cotton quotation")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        if not updated:
            self._send_error_json(404, "quotation_not_found", self._t("quotation.not_editable"))
            return
        self._send_json({"ok": True})

    def _handle_create_order(self, user_id):
        fields = self._read_json()
        if fields is None:
            return
        try:
            order_id = order_manager.create_order(user_id, fields)
        except db_manager.QuotationAlreadyOrdered:
            self._send_error_json(409, "quotation_already_ordered", self._t("order.already_ordered"))
            return
        except (ValueError, OverflowError):
            self._send_error_json(400, "invalid_order", self._t("order.invalid"))
            return
        except db_manager.DatabaseError:
            logging.exception("Could not save order")
            self._send_error_json(500, "database_error", self._t("order.save_failed"))
            return
        self._send_json({"order": {"id": order_id}}, 201)

    def _handle_list_suppliers(self, user_id):
        try:
            suppliers = db_manager.list_suppliers(user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not list suppliers")
            self._send_error_json(
                500, "database_error", self._t("request.database_read_error")
            )
            return
        self._send_json({"suppliers": suppliers})

    def _handle_create_supplier(self, user_id):
        fields = self._read_json()
        if fields is None:
            return
        try:
            supplier = db_manager.create_supplier(user_id, fields)
        except ValueError:
            self._send_error_json(
                400, "invalid_supplier", self._t("supplier.invalid")
            )
            return
        except db_manager.DatabaseError:
            logging.exception("Could not create supplier")
            self._send_error_json(
                500, "database_error", self._t("supplier.save_failed")
            )
            return
        self._send_json({"supplier": supplier}, 201)

    def _handle_list_orders(self, user_id, order_id=None):
        try:
            orders = db_manager.list_orders(user_id, order_id)
        except (db_manager.DatabaseError, json.JSONDecodeError):
            logging.exception("Could not read orders")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        if order_id is not None:
            if not orders:
                self._send_error_json(404, "order_not_found", self._t("order.not_found"))
                return
            self._send_json({"order": orders[0]})
        else:
            self._send_json({"orders": orders})

    def _handle_list_waybills(self, user_id):
        try:
            waybills = db_manager.list_waybills(user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not read waybills")
            self._send_error_json(
                500, "database_error", self._t("request.database_read_error")
            )
            return
        self._send_json({"waybills": waybills})

    def _handle_update_order(self, user_id, order_id):
        fields = self._read_json()
        if fields is None:
            return
        try:
            updated = order_manager.update_order(user_id, order_id, fields)
        except (ValueError, OverflowError):
            self._send_error_json(400, "invalid_order", self._t("order.invalid"))
            return
        except db_manager.DatabaseError:
            logging.exception("Could not update order")
            self._send_error_json(500, "database_error", self._t("order.save_failed"))
            return
        if not updated["updated"]:
            self._send_error_json(404, "order_not_found", self._t("order.not_found"))
            return
        self._send_json({
            "ok": True,
            "purchase_contract_deleted": bool(
                updated["purchase_contract_deleted"]
            ),
        })

    def _handle_delete_order(self, user_id, order_id):
        try:
            deleted = db_manager.delete_order(user_id, order_id)
        except db_manager.DatabaseError:
            logging.exception("Could not delete order")
            self._send_error_json(500, "database_error", self._t("request.delete_failed"))
            return
        if not deleted:
            self._send_error_json(404, "order_not_found", self._t("order.not_found"))
            return
        self._send_json({"ok": True})

    def _handle_user_profile(self, user_id):
        try:
            profile = db_manager.get_user_profile(user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not read user profile %s", user_id)
            self._send_error_json(
                500, "database_error", self._t("request.database_read_error")
            )
            return
        if profile is None:
            self._send_error_json(404, "user_not_found", self._t("profile.not_found"))
            return
        self._send_json({"profile": profile})

    def _handle_update_user_profile(self, user_id):
        fields = self._read_json()
        if fields is None:
            return
        expected = {
            "company_name",
            "company_address",
            "foreign_bank_account",
            "cny_bank_account",
        }
        if fields.keys() != expected or any(len(value) > 2000 for value in fields.values()):
            self._send_error_json(
                400, "invalid_profile", self._t("profile.invalid")
            )
            return
        values = {key: value.strip() or None for key, value in fields.items()}
        try:
            updated = db_manager.update_user_profile(user_id, values)
        except (ValueError, db_manager.DatabaseError):
            logging.exception("Could not update user profile %s", user_id)
            self._send_error_json(
                500, "database_error", self._t("profile.save_failed")
            )
            return
        if not updated:
            self._send_error_json(404, "user_not_found", self._t("profile.not_found"))
            return
        self._send_json({"ok": True})

    def _handle_change_password(self, user_id, username):
        fields = self._read_json()
        if fields is None:
            return
        try:
            auth_manager.change_password(user_id, username, fields)
        except auth_manager.AuthError as error:
            self._send_error_json(
                error.status, error.code, self._t(error.message_key)
            )
            return
        except db_manager.DatabaseError:
            logging.exception("Could not change password for user %s", user_id)
            self._send_error_json(
                500, "database_error", self._t("profile.password_save_failed")
            )
            return
        self._send_json({"ok": True})

    def _handle_list_order_documents(self, user_id, order_id):
        try:
            orders = db_manager.list_orders(user_id, order_id)
            if not orders:
                self._send_error_json(404, "order_not_found", self._t("order.not_found"))
                return
            documents = db_manager.list_order_documents(user_id, order_id)
            related_documents = db_manager.list_order_related_documents(
                user_id, order_id
            )
            waybills = db_manager.list_waybills(user_id, order_id)
            self._queue_pending_document_analysis(
                user_id, order_id, related_documents, self._locale()
            )
        except (db_manager.DatabaseError, json.JSONDecodeError):
            logging.exception("Could not read documents for order %s", order_id)
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        self._send_json({
            "documents": documents,
            "related_documents": related_documents,
            "waybills": waybills,
            "has_intermediary": bool(orders[0].get("intermediary_id")),
        })

    def _handle_generate_purchase_contract(self, user_id, username, order_id):
        try:
            orders = db_manager.list_orders(user_id, order_id)
            if not orders:
                self._send_error_json(404, "order_not_found", self._t("order.not_found"))
                return
            existing_documents = {
                item["document_type"]
                for item in db_manager.list_order_documents(user_id, order_id)
            }
            contract_types = (
                ["supplier_intermediary_contract", "intermediary_user_contract"]
                if orders[0].get("intermediary_id")
                else ["purchase_contract"]
            )
            missing_types = [
                document_type
                for document_type in contract_types
                if document_type not in existing_documents
            ]
            if missing_types:
                profile = db_manager.get_user_profile(user_id)
                if profile is None:
                    self._send_error_json(
                        404, "user_not_found", self._t("profile.not_found")
                    )
                    return
                generated = []
                for document_type in missing_types:
                    party_role = (
                        "direct"
                        if document_type == "purchase_contract"
                        else document_type.removesuffix("_contract")
                    )
                    generated.append((
                        document_type,
                        f"{document_type.replace('_', '-')}-{order_id}.pdf",
                        "application/pdf",
                        purchase_contract.build_contract_pdf(
                            orders[0], username, profile, party_role
                        ),
                    ))
                db_manager.create_order_documents(
                    user_id, order_id, generated
                )
            documents = [
                item
                for item in db_manager.list_order_documents(user_id, order_id)
                if item["document_type"] in contract_types
            ]
        except (db_manager.DatabaseError, json.JSONDecodeError):
            logging.exception("Could not store purchase contract for order %s", order_id)
            self._send_error_json(500, "database_error", self._t("request.database_write_error"))
            return
        except purchase_contract.ContractGenerationError:
            logging.exception("Could not generate purchase contract for order %s", order_id)
            self._send_error_json(500, "contract_failed", self._t("order.contract_failed"))
            return
        self._send_json(
            {
                "documents": documents,
                "document": documents[0] if documents else None,
            },
            201 if missing_types else 200,
        )

    def _handle_download_order_document(self, user_id, order_id, document_type):
        try:
            document = db_manager.get_order_document(
                user_id, order_id, document_type
            )
        except db_manager.DatabaseError:
            logging.exception("Could not read document for order %s", order_id)
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        if document is None:
            self._send_error_json(404, "document_not_found", self._t("order.document_not_found"))
            return
        filename, content_type, content = document
        self._send_file(content, content_type, filename)

    def _handle_upload_related_document(self, user_id, order_id):
        try:
            if not db_manager.list_orders(user_id, order_id):
                self._send_error_json(404, "order_not_found", self._t("order.not_found"))
                return
        except (db_manager.DatabaseError, json.JSONDecodeError):
            logging.exception("Could not read order %s for document upload", order_id)
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        uploaded_files = self._read_uploaded_files()
        if not uploaded_files:
            return
        for filename, _content_type, _content in uploaded_files:
            extension = Path(filename).suffix.lower()
            if extension not in PDF_EXTENSIONS and extension not in IMAGE_MIME_TYPES:
                self._send_error_json(
                    415, "invalid_document_type", self._t("order.document.file_type")
                )
                return
        documents = []
        try:
            for filename, content_type, content in uploaded_files:
                documents.append(db_manager.create_order_related_document(
                    user_id,
                    order_id,
                    filename,
                    content_type,
                    content,
                    None,
                ))
        except db_manager.DatabaseError:
            logging.exception("Could not save related document for order %s", order_id)
            self._send_error_json(
                500, "database_error", self._t("request.database_write_error")
            )
            return
        self._queue_pending_document_analysis(
            user_id, order_id, documents, self._locale()
        )
        self._send_json({"documents": documents}, 201)

    def _analyze_related_document(
        self, user_id, order_id, document_id, filename, content, locale
    ):
        try:
            extension = Path(filename).suffix.lower()
            if extension in PDF_EXTENSIONS:
                analysis = analyze_pdf_shipping_document(content, locale)
            else:
                analysis = analyze_image_shipping_document(
                    filename, content, locale
                )
            db_manager.update_order_related_analysis(
                document_id, user_id, order_id, analysis
            )
            if analysis.get("category") == "bill_of_lading":
                db_manager.upsert_waybill(
                    user_id,
                    order_id,
                    document_id,
                    analysis.get("waybill") or analysis.get("fields", {}),
                )
        except (PdfAnalysisError, db_manager.DatabaseError, RuntimeError):
            logging.exception(
                "Could not analyze related document %s", document_id
            )
        finally:
            with ANALYZING_RELATED_DOCUMENTS_LOCK:
                ANALYZING_RELATED_DOCUMENTS.discard(document_id)

    def _queue_pending_document_analysis(
        self, user_id, order_id, documents, locale
    ):
        for document in documents:
            if document.get("analysis_status") != "pending":
                continue
            document_id = document["id"]
            with ANALYZING_RELATED_DOCUMENTS_LOCK:
                if document_id in ANALYZING_RELATED_DOCUMENTS:
                    continue
                ANALYZING_RELATED_DOCUMENTS.add(document_id)
            stored = db_manager.get_order_related_document_bytes(
                user_id, order_id, document_id
            )
            if stored is None:
                with ANALYZING_RELATED_DOCUMENTS_LOCK:
                    ANALYZING_RELATED_DOCUMENTS.discard(document_id)
                continue
            filename, _content_type, content = stored
            threading.Thread(
                target=self._analyze_related_document,
                args=(
                    user_id,
                    order_id,
                    document_id,
                    filename,
                    content,
                    locale,
                ),
                daemon=True,
            ).start()

    def _handle_save_related_document_review(
        self, user_id, order_id, document_id
    ):
        fields = self._read_json()
        if fields is None:
            return
        if fields.keys() != {"summary", "recognized_text"}:
            self._send_error_json(
                400, "invalid_fields", self._t("request.invalid_form_content")
            )
            return
        try:
            updated = db_manager.update_order_related_review(
                document_id,
                user_id,
                order_id,
                fields["summary"],
                fields["recognized_text"],
            )
        except db_manager.DatabaseError:
            logging.exception("Could not save document review %s", document_id)
            self._send_error_json(
                500, "database_error", self._t("request.database_write_error")
            )
            return
        if not updated:
            self._send_error_json(
                404, "document_not_found", self._t("order.document_not_found")
            )
            return
        self._send_json({"ok": True})

    def _handle_update_waybill(self, user_id, order_id, document_id):
        fields = self._read_json()
        if fields is None:
            return
        try:
            waybill = db_manager.upsert_waybill(
                user_id, order_id, document_id, fields
            )
        except ValueError:
            self._send_error_json(
                400, "invalid_waybill", self._t("waybill.invalid")
            )
            return
        except db_manager.DatabaseError:
            logging.exception("Could not save waybill for order %s", order_id)
            self._send_error_json(
                500, "database_error", self._t("request.database_write_error")
            )
            return
        if waybill is None:
            self._send_error_json(
                404, "document_not_found", self._t("order.document_not_found")
            )
            return
        self._send_json({"waybill": waybill})

    def _handle_delete_related_document(self, user_id, order_id, document_id):
        try:
            deleted = db_manager.delete_order_related_document(
                user_id, order_id, document_id
            )
        except db_manager.DatabaseError:
            logging.exception("Could not delete related document %s", document_id)
            self._send_error_json(
                500, "database_error", self._t("request.delete_failed")
            )
            return
        if not deleted:
            self._send_error_json(
                404, "document_not_found", self._t("order.document_not_found")
            )
            return
        self._send_json({"ok": True})

    def _handle_download_related_document(self, user_id, order_id, document_id):
        try:
            document = db_manager.get_order_related_document(
                user_id, order_id, document_id
            )
        except db_manager.DatabaseError:
            logging.exception("Could not read related document %s", document_id)
            self._send_error_json(
                500, "database_error", self._t("request.database_read_error")
            )
            return
        if document is None:
            self._send_error_json(
                404, "document_not_found", self._t("order.document_not_found")
            )
            return
        filename, content_type, content = document
        self._send_file(content, content_type, filename)

    def _order_id(self, path):
        match = re.fullmatch(r"/api/orders/([1-9][0-9]{0,18})", path)
        if match is None or int(match.group(1)) >= 2 ** 63:
            return None
        return int(match.group(1))

    def _order_documents_match(self, path):
        match = re.fullmatch(
            r"/api/orders/([1-9][0-9]{0,18})/documents"
            r"(?:/([a-z_]+)(/content)?)?",
            path,
        )
        if match is None or int(match.group(1)) >= 2 ** 63:
            return None
        return int(match.group(1)), match.group(2), bool(match.group(3))

    def _related_document_match(self, path):
        match = re.fullmatch(
            r"/api/orders/([1-9][0-9]{0,18})/related-documents/([1-9][0-9]*)"
            r"(?:/content)?",
            path,
        )
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2)), path.endswith("/content")

    def _handle_market_history(self, asset):
        try:
            result = outcall_api.get_market_history(asset)
        except outcall_api.InvalidMarketAsset:
            self._send_error_json(
                400,
                "invalid_market_asset",
                self._t("market.invalid_asset"),
            )
            return
        except outcall_api.MarketDataUnavailable:
            self._send_error_json(
                502,
                "market_history_unavailable",
                self._t("market.history_unavailable"),
            )
            return
        self._send_json(result)

    def _read_uploaded_files(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(400, "invalid_length", self._t("request.invalid_length"))
            return None
        if content_length <= 0:
            self._send_error_json(400, "missing_file", self._t("upload.select_file"))
            return None
        if content_length > MAX_UPLOAD_BYTES:
            self._send_error_json(413, "file_too_large", self._t("upload.too_large"))
            return None

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self._send_error_json(400, "invalid_content_type", self._t("upload.invalid_type"))
            return None
        request_body = self.rfile.read(content_length)
        if len(request_body) != content_length:
            self._send_error_json(400, "incomplete_upload", self._t("upload.incomplete"))
            return None
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1")
            + request_body
        )
        uploaded_files = [
            part
            for part in message.iter_parts()
            if part.get_content_disposition() == "form-data"
            and part.get_param("name", header="content-disposition") == "file"
            and part.get_filename()
        ]
        if not uploaded_files:
            self._send_error_json(400, "missing_file", self._t("upload.select_file"))
            return None

        return [
            (
                Path(uploaded_file.get_filename()).name,
                mimetypes.guess_type(uploaded_file.get_filename())[0]
                or "application/octet-stream",
                uploaded_file.get_payload(decode=True) or b"",
            )
            for uploaded_file in uploaded_files
        ]

    def _read_uploaded_file(self):
        uploaded_files = self._read_uploaded_files()
        return uploaded_files[0] if uploaded_files else None

    def _handle_upload(self, user_id):
        uploaded = self._read_uploaded_file()
        if uploaded is None:
            return
        filename, stored_content_type, file_bytes = uploaded
        try:
            upload_id, uploaded_at = db_manager.create_upload(
                user_id,
                filename,
                stored_content_type,
                file_bytes,
            )
        except db_manager.DatabaseError:
            logging.exception("Could not save uploaded file")
            self._send_error_json(500, "database_error", self._t("upload.database_failed"))
            return
        self._send_json(
            {
                "upload": {
                    "id": upload_id,
                    "filename": filename,
                    "content_type": stored_content_type,
                    "size_bytes": len(file_bytes),
                    "uploaded_at": uploaded_at,
                }
            },
            201,
        )

    def _upload_id(self, path, suffix=""):
        prefix = "/api/uploads/"
        if not path.startswith(prefix):
            return None
        value = path[len(prefix):]
        if suffix:
            if not value.endswith(suffix):
                return None
            value = value[:-len(suffix)]
        if not value.isdigit():
            return None
        return int(value)

    def _handle_content(self, upload_id, user_id, download):
        try:
            upload = db_manager.get_upload_content(upload_id, user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not read uploaded file")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        if upload is None:
            self._send_error_json(404, "file_not_found", self._t("request.file_not_found"))
            return
        filename, stored_content_type, file_bytes = upload
        extension = Path(filename).suffix.lower()
        content_type = (
            "application/pdf" if extension in PDF_EXTENSIONS
            else IMAGE_MIME_TYPES.get(extension, stored_content_type)
        )
        previewable = extension in PDF_EXTENSIONS or extension in IMAGE_MIME_TYPES
        disposition = "inline" if previewable and not download else "attachment"
        body = bytes(file_bytes)
        self.send_response(200)
        self._add_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}",
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _handle_ai_analysis(self, upload_id, user_id):
        if not ai_analysis.AI_API_KEY or not ai_analysis.AI_MODEL:
            self._send_error_json(
                503,
                "ai.not_configured",
                self._t("ai.not_configured"),
            )
            return
        try:
            upload = db_manager.get_upload_for_ai(upload_id, user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not read uploaded file for AI analysis")
            self._send_error_json(
                500,
                "database_error",
                self._t("request.database_read_error"),
            )
            return
        if upload is None:
            self._send_error_json(
                404,
                "file_not_found",
                self._t("request.file_not_found"),
            )
            return

        filename, file_bytes = upload
        try:
            messages = ai_analysis.build_ai_messages(
                filename,
                bytes(file_bytes),
                self._locale(),
            )
            analysis = ai_analysis.request_ai_completion(messages)
        except ai_analysis.AIAnalysisInputError as error:
            self._send_error_json(error.status, error.code, str(error))
            return
        except ai_analysis.AIProviderError as error:
            self._send_error_json(
                error.status,
                error.code,
                self._t(error.code),
            )
            return
        analyzed_at = datetime.now(timezone.utc).isoformat()
        try:
            affected_rows = db_manager.save_ai_analysis(
                upload_id,
                user_id,
                analysis,
                ai_analysis.AI_MODEL,
                analyzed_at,
            )
        except db_manager.DatabaseError:
            logging.exception("Could not save AI analysis")
            self._send_error_json(
                500,
                "database_error",
                self._t("request.database_write_error"),
            )
            return
        if affected_rows != 1:
            self._send_error_json(
                404,
                "file_not_found",
                self._t("request.file_not_found"),
            )
            return
        self._send_json(
            {
                "analysis": {
                    "type": "ai",
                    "text": analysis,
                    "model": ai_analysis.AI_MODEL,
                    "analyzed_at": analyzed_at,
                }
            }
        )

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if origin and origin not in CORS_ORIGINS:
            self._send_error_json(403, "origin_not_allowed", "This origin is not allowed.")
            return
        self.send_response(204)
        self._add_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/health":
            self._send_json({"status": "ok"})
            return
        if parsed.path == "/api/translations":
            requested_locale = parse_qs(parsed.query).get("locale", [""])[0]
            locale = get_locale(self.headers.get("Accept-Language", ""), requested_locale)
            self._send_json({"locale": locale, "messages": get_messages(locale)})
            return
        if parsed.path == "/api/market-prices":
            self._handle_market_prices()
            return
        if parsed.path == "/api/market-history":
            asset = parse_qs(parsed.query).get("asset", [""])[0]
            self._handle_market_history(asset)
            return
        if parsed.path == "/api/cotton-quotations":
            user = self._require_user()
            if user is not None:
                self._handle_cotton_quotations(user[0])
            return
        if parsed.path == "/api/orders":
            user = self._require_user()
            if user is not None:
                self._handle_list_orders(user[0])
            return
        if parsed.path == "/api/waybills":
            user = self._require_user()
            if user is not None:
                self._handle_list_waybills(user[0])
            return
        if parsed.path == "/api/suppliers":
            user = self._require_user()
            if user is not None:
                self._handle_list_suppliers(user[0])
            return
        if parsed.path == "/api/user-profile":
            user = self._require_user()
            if user is not None:
                self._handle_user_profile(user[0])
            return
        related_document_match = self._related_document_match(parsed.path)
        if related_document_match is not None:
            user = self._require_user()
            if user is not None:
                order_id, document_id, download = related_document_match
                if download:
                    self._handle_download_related_document(
                        user[0], order_id, document_id
                    )
                else:
                    self._send_json({
                        "documents": db_manager.list_order_related_documents(
                            user[0], order_id
                        )
                    })
            return
        documents_match = self._order_documents_match(parsed.path)
        if documents_match is not None:
            user = self._require_user()
            if user is not None:
                order_id, document_type, download = documents_match
                if document_type is None:
                    self._handle_list_order_documents(user[0], order_id)
                elif download:
                    self._handle_download_order_document(
                        user[0], order_id, document_type
                    )
                else:
                    self._send_error_json(
                        404, "not_found", self._t("request.page_not_found")
                    )
            return
        order_id = self._order_id(parsed.path)
        if order_id is not None:
            user = self._require_user()
            if user is not None:
                self._handle_list_orders(user[0], order_id)
            return
        if parsed.path == "/api/session":
            try:
                user = auth_manager.get_current_user(
                    self.headers.get("Cookie", "")
                )
            except db_manager.DatabaseError:
                logging.exception("Could not read the current session")
                self._send_error_json(500, "database_error", self._t("request.database_read_error"))
                return
            self._send_json(
                {
                    "authenticated": user is not None,
                    "user": (
                        {"id": user[0], "username": user[1]} if user is not None else None
                    ),
                    "locale": self._locale(),
                }
            )
            return

        if not parsed.path.startswith("/api/uploads"):
            self._send_error_json(404, "not_found", self._t("request.page_not_found"))
            return
        user = self._require_user()
        if user is None:
            return
        user_id = user[0]
        if parsed.path == "/api/uploads":
            try:
                uploads = db_manager.list_uploads(user_id)
            except db_manager.DatabaseError:
                logging.exception("Could not list uploaded files")
                self._send_error_json(500, "database_error", self._t("request.database_read_error"))
                return
            self._send_json(
                {
                    "uploads": [
                        {
                            "id": row[0],
                            "filename": row[1],
                            "content_type": row[2],
                            "size_bytes": row[3],
                            "uploaded_at": row[4],
                        }
                        for row in uploads
                    ]
                }
            )
            return

        content_upload_id = self._upload_id(parsed.path, "/content")
        if content_upload_id is not None:
            download = parse_qs(parsed.query).get("download") == ["1"]
            self._handle_content(content_upload_id, user_id, download)
            return
        upload_id = self._upload_id(parsed.path)
        if upload_id is None:
            self._send_error_json(404, "not_found", self._t("request.file_not_found"))
            return
        try:
            upload = db_manager.get_upload_details(upload_id, user_id)
        except db_manager.DatabaseError:
            logging.exception("Could not read uploaded file metadata")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        if upload is None:
            self._send_error_json(404, "file_not_found", self._t("request.file_not_found"))
            return
        (
            filename,
            content_type,
            size_bytes,
            uploaded_at,
            file_bytes,
            ai_analysis,
            ai_model,
            ai_analyzed_at,
        ) = upload
        locale = self._locale()
        include_analysis = parse_qs(parsed.query).get("analyze") == ["1"]
        analysis = build_analysis(filename, file_bytes, locale) if include_analysis else None
        cotton_quotation_import = None
        if analysis is not None and analysis.get("type") == "excel":
            try:
                cotton_quotation_import = db_manager.import_uploaded_cotton_quotations(
                    upload_id,
                    filename,
                    bytes(file_bytes),
                )
            except (ExcelAnalysisError, db_manager.DatabaseError):
                logging.exception(
                    "Could not import cotton quotations from upload %s", upload_id
                )
                self._send_error_json(
                    500,
                    "cotton_quotation_import_failed",
                    self._t("market.cotton_import_failed"),
                )
                return
        response = {
            "upload": {
                "id": upload_id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "uploaded_at": uploaded_at,
            },
            "preview": build_preview(filename, upload_id, file_bytes, locale),
            "analysis": analysis,
            "cotton_quotation_import": cotton_quotation_import,
            "ai_analysis": (
                {
                    "type": "ai",
                    "text": ai_analysis,
                    "model": ai_model,
                    "analyzed_at": ai_analyzed_at,
                }
                if ai_analysis is not None
                else None
            ),
        }
        self._send_json(response)

    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/suppliers":
            user = self._require_user()
            if user is not None:
                self._handle_create_supplier(user[0])
            return
        documents_match = self._order_documents_match(parsed.path)
        if documents_match is not None:
            order_id, document_type, download = documents_match
            if document_type in {"purchase_contract", "related_documents"} and not download:
                user = self._require_user()
                if user is not None:
                    if document_type == "purchase_contract":
                        self._handle_generate_purchase_contract(
                            user[0], user[1], order_id
                        )
                    else:
                        self._handle_upload_related_document(user[0], order_id)
                return
        if parsed.path == "/api/sudan-cotton-pricing":
            self._handle_sudan_cotton_pricing()
            return
        if parsed.path == "/api/product-pricing":
            self._handle_product_purchase_pricing()
            return
        if parsed.path == "/api/auth":
            self._handle_auth()
            return
        if parsed.path == "/api/logout":
            self._handle_logout()
            return
        if parsed.path == "/api/orders":
            user = self._require_user()
            if user is not None:
                self._handle_create_order(user[0])
            return
        ai_analysis_upload_id = self._upload_id(
            parsed.path,
            "/ai-analysis",
        )
        if ai_analysis_upload_id is not None:
            user = self._require_user()
            if user is not None:
                self._handle_ai_analysis(ai_analysis_upload_id, user[0])
            return
        if parsed.path != "/api/uploads":
            self._send_error_json(404, "not_found", self._t("request.page_not_found"))
            return
        user = self._require_user()
        if user is not None:
            self._handle_upload(user[0])

    def do_PATCH(self):
        parsed = urlsplit(self.path)
        waybill_match = re.fullmatch(
            r"/api/orders/([1-9][0-9]{0,18})/related-documents/"
            r"([1-9][0-9]*)/waybill",
            parsed.path,
        )
        if waybill_match is not None:
            user = self._require_user()
            if user is not None:
                self._handle_update_waybill(
                    user[0], int(waybill_match.group(1)),
                    int(waybill_match.group(2)),
                )
            return
        related_document_match = self._related_document_match(parsed.path)
        if related_document_match is not None:
            order_id, document_id, download = related_document_match
            if download:
                self._send_error_json(
                    404, "not_found", self._t("request.page_not_found")
                )
                return
            user = self._require_user()
            if user is not None:
                self._handle_save_related_document_review(
                    user[0], order_id, document_id
                )
            return
        if parsed.path == "/api/user-profile":
            user = self._require_user()
            if user is not None:
                self._handle_update_user_profile(user[0])
            return
        if parsed.path == "/api/user-password":
            user = self._require_user()
            if user is not None:
                self._handle_change_password(user[0], user[1])
            return
        order_id = self._order_id(parsed.path)
        if order_id is not None:
            user = self._require_user()
            if user is not None:
                self._handle_update_order(user[0], order_id)
            return
        match = re.fullmatch(r"/api/cotton-quotations/([1-9][0-9]{0,18})", parsed.path)
        if match is None:
            self._send_error_json(404, "not_found", self._t("request.page_not_found"))
            return
        row_number = int(match.group(1))
        if row_number >= 2 ** 63:
            self._send_error_json(404, "not_found", self._t("request.page_not_found"))
            return
        user = self._require_user()
        if user is not None:
            self._handle_update_cotton_quotation(row_number, user[0])

    def do_DELETE(self):
        parsed = urlsplit(self.path)
        related_document_match = self._related_document_match(parsed.path)
        if related_document_match is not None:
            order_id, document_id, download = related_document_match
            if download:
                self._send_error_json(
                    404, "not_found", self._t("request.page_not_found")
                )
                return
            user = self._require_user()
            if user is not None:
                self._handle_delete_related_document(
                    user[0], order_id, document_id
                )
            return
        order_id = self._order_id(parsed.path)
        if order_id is not None:
            user = self._require_user()
            if user is not None:
                self._handle_delete_order(user[0], order_id)
            return
        upload_id = self._upload_id(parsed.path)
        if upload_id is None:
            self._send_error_json(404, "not_found", self._t("request.file_not_found"))
            return
        user = self._require_user()
        if user is None:
            return
        try:
            affected_rows = db_manager.delete_upload(upload_id, user[0])
        except db_manager.DatabaseError:
            logging.exception("Could not delete uploaded file")
            self._send_error_json(500, "database_error", self._t("request.delete_failed"))
            return
        if affected_rows == 0:
            self._send_error_json(404, "file_not_found", self._t("request.file_not_found"))
            return
        self._send_json({"ok": True})


def main():
    db_manager.initialize_database()
    address = (SERVER_HOST, SERVER_PORT)
    server = HTTPServer(address, ApiHandler)
    console_locale = get_locale(os.environ.get("LANG", ""))
    print(translate(console_locale, "server.started", url=f"http://{address[0]}:{address[1]}"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n" + translate(console_locale, "server.stopped"))
        server.server_close()


if __name__ == "__main__":
    main()
