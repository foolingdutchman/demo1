import base64
from contextlib import closing
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from html.parser import HTMLParser
import hashlib
import hmac
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen

from excel_analysis import ExcelAnalysisError, build_excel_analysis, extract_excel_text
from i18n import SUPPORTED_LOCALES, get_locale, get_messages, translate
from image_analysis import IMAGE_MIME_TYPES, is_image_file
from pdf_analysis import PdfAnalysisError, build_pdf_analysis, extract_pdf_text


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024
PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME = 7 * 24 * 60 * 60
AI_API_BASE_URL = os.environ.get(
    "AI_API_BASE_URL", "https://api.openai.com/v1"
).strip().rstrip("/")
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "").strip()
AI_MAX_INPUT_CHARS = int(os.environ.get("AI_MAX_INPUT_CHARS", "60000"))
AI_API_TIMEOUT_SECONDS = int(os.environ.get("AI_API_TIMEOUT_SECONDS", "60"))
AI_SUPPORTED_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
COTTON_CUSTOMS_COST_CNY = 100
IMPORT_VAT_FACTOR = 1.09
US_CENTS_PER_METRIC_TON_CONVERSION = 22.0462
COTTON_CLEARANCE_COST_US_CENTS = 1.5
NO_QUOTA_TARIFF_FACTOR = 1.4
DATABASE_PATH = Path(__file__).with_name("uploads.db")
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json"}
EXCEL_EXTENSIONS = {".xls", ".xlsx"}
PDF_EXTENSIONS = {".pdf"}
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


class AIAnalysisInputError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code


class AIProviderError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


class BankOfChinaRateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._is_usd_row = False
        self._in_cell = False
        self._cell_parts = []
        self.usd_cells = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._is_usd_row = dict(attrs).get("data-currency") == "美元"
            if self._is_usd_row:
                self.usd_cells = []
        elif tag == "td" and self._is_usd_row:
            self._in_cell = True
            self._cell_parts = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._in_cell:
            self.usd_cells.append("".join(self._cell_parts).strip())
            self._in_cell = False
        elif tag == "tr":
            self._is_usd_row = False


def initialize_database():
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                expires_at INTEGER NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                content BLOB NOT NULL
            )"""
        )
        upload_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(uploads)")
        }
        if "user_id" not in upload_columns:
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN user_id INTEGER REFERENCES users(id)"
            )


def decode_text_file(file_bytes):
    import codecs

    try:
        if file_bytes.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = "utf-32"
        elif file_bytes.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
        return file_bytes.decode(encoding)
    except UnicodeDecodeError:
        return None


def build_preview(filename, upload_id, file_bytes, locale):
    extension = Path(filename).suffix.lower()
    if extension in PDF_EXTENSIONS:
        return {"type": "pdf", "url": f"/api/uploads/{upload_id}/content"}
    if is_image_file(filename):
        return {"type": "image", "url": f"/api/uploads/{upload_id}/content"}
    if extension not in TEXT_EXTENSIONS:
        return {"type": "unsupported", "message": translate(locale, "preview.unsupported")}

    text = decode_text_file(file_bytes)
    if text is None:
        return {"type": "invalid_encoding", "message": translate(locale, "preview.invalid_encoding")}
    return {"type": "text", "text": text}


def build_analysis(filename, file_bytes, locale):
    extension = Path(filename).suffix.lower()
    if is_image_file(filename):
        return {
            "type": "image",
            "message": translate(locale, "analysis.image_pending"),
        }
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


def build_ai_messages(filename, file_bytes, locale):
    extension = Path(filename).suffix.lower()
    if is_image_file(filename):
        image_mime_type = IMAGE_MIME_TYPES[extension]
        if image_mime_type not in AI_SUPPORTED_IMAGE_MIME_TYPES:
            raise AIAnalysisInputError(
                415,
                "ai.unsupported_image",
                translate(locale, "ai.unsupported_image"),
            )
        image_data = base64.b64encode(file_bytes).decode("ascii")
        user_content = [
            {"type": "text", "text": translate(locale, "ai.image_prompt")},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image_mime_type};base64,{image_data}",
                },
            },
        ]
    else:
        try:
            if extension in PDF_EXTENSIONS:
                _, document_text = extract_pdf_text(file_bytes, locale)
                if not document_text.strip():
                    raise AIAnalysisInputError(
                        422,
                        "ai.pdf_no_text",
                        translate(locale, "ai.pdf_no_text"),
                    )
            elif extension in EXCEL_EXTENSIONS:
                document_text = extract_excel_text(file_bytes)
            elif extension in TEXT_EXTENSIONS:
                document_text = decode_text_file(file_bytes)
                if document_text is None:
                    raise AIAnalysisInputError(
                        422,
                        "ai.invalid_encoding",
                        translate(locale, "ai.invalid_encoding"),
                    )
            else:
                raise AIAnalysisInputError(
                    415,
                    "ai.unsupported",
                    translate(locale, "ai.unsupported"),
                )
        except (PdfAnalysisError, ExcelAnalysisError) as error:
            raise AIAnalysisInputError(
                422,
                "ai.document_read_failed",
                translate(locale, "ai.document_read_failed"),
            ) from error

        if not document_text.strip():
            raise AIAnalysisInputError(
                422,
                "ai.empty_content",
                translate(locale, "ai.empty_content"),
            )
        if len(document_text) > AI_MAX_INPUT_CHARS:
            raise AIAnalysisInputError(
                413,
                "ai.input_too_large",
                translate(
                    locale,
                    "ai.input_too_large",
                    limit=AI_MAX_INPUT_CHARS,
                ),
            )
        user_content = (
            f"{translate(locale, 'ai.document_prompt')}\n\n{document_text}"
        )

    return [
        {"role": "system", "content": translate(locale, "ai.system_prompt")},
        {"role": "user", "content": user_content},
    ]


def request_ai_completion(messages):
    if not AI_API_KEY or not AI_MODEL:
        raise AIProviderError(503, "ai.not_configured")
    endpoint = urlsplit(AI_API_BASE_URL)
    if endpoint.scheme not in {"http", "https"} or not endpoint.netloc:
        raise AIProviderError(503, "ai.invalid_configuration")
    if endpoint.scheme != "https" and endpoint.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise AIProviderError(503, "ai.invalid_configuration")

    request = Request(
        f"{AI_API_BASE_URL}/chat/completions",
        data=json.dumps(
            {
                "model": AI_MODEL,
                "messages": messages,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=AI_API_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "\n".join(
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI provider returned an empty response")
        return content.strip()
    except HTTPError as error:
        logging.warning("AI provider returned HTTP %s", error.code)
        raise AIProviderError(502, "ai.provider_failed") from error
    except (
        URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as error:
        logging.warning("AI provider request failed: %s", type(error).__name__)
        raise AIProviderError(502, "ai.provider_failed") from error


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

    @staticmethod
    def _session_token_hash(token):
        try:
            return hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError:
            return None

    def _current_user(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        session_cookie = cookies.get("session")
        if session_cookie is None:
            return None
        token_hash = self._session_token_hash(session_cookie.value)
        if token_hash is None:
            return None
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            row = connection.execute(
                """SELECT users.id, users.username FROM sessions
                   JOIN users ON users.id = sessions.user_id
                   WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
                (token_hash, int(time.time())),
            ).fetchone()
        return row

    def _require_user(self):
        try:
            user = self._current_user()
        except sqlite3.Error:
            logging.exception("Could not read the current session")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return None
        if user is None:
            self._send_error_json(401, "unauthorized", self._t("auth.login_required"))
            return None
        return user

    def _create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        expires_at = int(time.time()) + SESSION_LIFETIME
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),)
            )
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
        return (
            f"session={token}; HttpOnly; SameSite=Lax; Path=/; "
            f"Max-Age={SESSION_LIFETIME}"
        )

    def _handle_auth(self):
        fields = self._read_json()
        if fields is None:
            return
        mode = fields.get("mode", "login")
        if mode not in {"login", "register"}:
            self._send_error_json(400, "invalid_mode", self._t("request.invalid_mode"))
            return
        username = fields.get("username", "").strip()
        password = fields.get("password", "")
        confirm_password = fields.get("confirm_password", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            self._send_error_json(400, "invalid_username", self._t("auth.invalid_username"))
            return
        if not 8 <= len(password) <= 128:
            self._send_error_json(400, "invalid_password", self._t("auth.invalid_password_length"))
            return

        try:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                user = connection.execute(
                    "SELECT id, username, password_salt, password_hash "
                    "FROM users WHERE username = ?",
                    (username,),
                ).fetchone()
                if mode == "register":
                    if user is not None:
                        self._send_error_json(409, "username_exists", self._t("auth.username_exists"))
                        return
                    if password != confirm_password:
                        self._send_error_json(400, "password_mismatch", self._t("auth.password_mismatch"))
                        return
                    if not all(
                        re.search(pattern, password)
                        for pattern in (r"[A-Z]", r"[a-z]", r"[0-9]")
                    ):
                        self._send_error_json(400, "weak_password", self._t("auth.password_strength"))
                        return
                    salt = secrets.token_bytes(16)
                    password_hash = hashlib.pbkdf2_hmac(
                        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
                    )
                    cursor = connection.execute(
                        "INSERT INTO users (username, password_salt, password_hash) "
                        "VALUES (?, ?, ?)",
                        (username, sqlite3.Binary(salt), sqlite3.Binary(password_hash)),
                    )
                    user_id = cursor.lastrowid
                else:
                    if user is None:
                        self._send_error_json(404, "username_missing", self._t("auth.username_missing"))
                        return
                    supplied_hash = hashlib.pbkdf2_hmac(
                        "sha256", password.encode("utf-8"), user[2], PASSWORD_ITERATIONS
                    )
                    if not hmac.compare_digest(supplied_hash, user[3]):
                        self._send_error_json(401, "invalid_credentials", self._t("auth.invalid_credentials"))
                        return
                    user_id = user[0]
                    username = user[1]
            cookie = self._create_session(user_id)
        except sqlite3.Error:
            logging.exception("Authentication database operation failed")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        self._send_json(
            {"user": {"id": user_id, "username": username}},
            200 if mode == "login" else 201,
            cookie,
        )

    def _handle_logout(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            cookies = SimpleCookie()
        session_cookie = cookies.get("session")
        try:
            if session_cookie is not None:
                token_hash = self._session_token_hash(session_cookie.value)
                if token_hash is not None:
                    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                        connection.execute(
                            "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
                        )
        except sqlite3.Error:
            logging.exception("Could not delete the session")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        self._send_json(
            {"ok": True},
            cookie="session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
        )

    @staticmethod
    def _fetch_public_json(url):
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "demo1-market-board/1.0",
            },
        )
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _fetch_public_text(url):
        request = Request(
            url,
            headers={
                "Accept": "text/plain",
                "User-Agent": "Mozilla/5.0 (compatible; demo1-market-board/1.0)",
                "Referer": "https://finance.sina.com.cn/",
            },
        )
        with urlopen(request, timeout=8) as response:
            return response.read().decode("gbk")

    @staticmethod
    def _fetch_public_html(url):
        request = Request(
            url,
            headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0 (compatible; demo1-market-board/1.0)",
            },
        )
        with urlopen(request, timeout=8) as response:
            return response.read().decode("utf-8")

    def _handle_market_prices(self):
        try:
            gold = self._fetch_public_json("https://api.gold-api.com/price/XAU")
            crypto = self._fetch_public_json(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin%2Cethereum&vs_currencies=usd"
            )
            ice_cotton = self._fetch_public_json(
                "https://query1.finance.yahoo.com/v8/finance/chart/CT%3DF"
                "?range=1d&interval=1m"
            )
            zce_cotton = self._fetch_public_text(
                "https://hq.sinajs.cn/list=nf_CF0"
            )
            boc_rates = self._fetch_public_html(
                "https://www.boc.cn/sourcedb/whpj/"
            )
            ice_result = ice_cotton["chart"]["result"][0]
            ice_price = float(ice_result["meta"]["regularMarketPrice"])
            cotton_quote = re.search(r'hq_str_nf_CF0="([^"]*)"', zce_cotton)
            if cotton_quote is None:
                raise ValueError("Public market API returned an invalid cotton quote")
            cotton_fields = cotton_quote.group(1).split(",")
            if len(cotton_fields) <= 6 or cotton_fields[0] != "棉花连续":
                raise ValueError("Public market API returned an invalid cotton quote")
            rate_parser = BankOfChinaRateParser()
            rate_parser.feed(boc_rates)
            if rate_parser.usd_cells is None or len(rate_parser.usd_cells) <= 3:
                raise ValueError("Bank of China USD exchange rate was not found")
            # Bank of China publishes exchange rates per 100 units of foreign currency.
            usd_cny_spot_sell = round(float(rate_parser.usd_cells[3]) / 100, 4)
            prices = {
                "gold": float(gold["price"]),
                "btc": float(crypto["bitcoin"]["usd"]),
                "eth": float(crypto["ethereum"]["usd"]),
                "ice_cotton": ice_price,
                "zce_cotton": float(cotton_fields[6]),
                "usd_cny": usd_cny_spot_sell,
            }
            if any(not math.isfinite(price) or price <= 0 for price in prices.values()):
                raise ValueError("Public market API returned an invalid price")
            import_cotton_before_tariff = (
                (prices["zce_cotton"] - COTTON_CUSTOMS_COST_CNY)
                / IMPORT_VAT_FACTOR
                / prices["usd_cny"]
                / US_CENTS_PER_METRIC_TON_CONVERSION
                - COTTON_CLEARANCE_COST_US_CENTS
            )
            import_cotton_price = round(
                import_cotton_before_tariff / NO_QUOTA_TARIFF_FACTOR,
                2,
            )
            if not math.isfinite(import_cotton_price) or import_cotton_price <= 0:
                raise ValueError("Calculated import cotton price is invalid")
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            logging.exception("Could not retrieve public market prices")
            self._send_error_json(
                502,
                "market_data_unavailable",
                self._t("market.unavailable"),
            )
            return
        self._send_json(
            {
                "currency": "USD",
                "asset_currencies": {
                    "ice_cotton": "US¢",
                    "zce_cotton": "CNY",
                    "usd_cny": "CNY",
                },
                "prices": prices,
                "derived_prices": {"import_cotton": import_cotton_price},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    @staticmethod
    def _market_candle(date, open_price, high, low, close):
        try:
            open_price, high, low, close = map(
                float, (open_price, high, low, close)
            )
        except (TypeError, ValueError):
            return None
        prices = (open_price, high, low, close)
        if (
            not date
            or any(not math.isfinite(price) or price <= 0 for price in prices)
            or high < max(open_price, close)
            or low > min(open_price, close)
            or high < low
        ):
            return None
        return {
            "date": date,
            "price": close,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
        }

    @classmethod
    def _yahoo_history_points(cls, payload):
        chart = payload["chart"]["result"][0]
        quote = chart["indicators"]["quote"][0]
        points = []
        for timestamp, open_price, high, low, close in zip(
            chart["timestamp"],
            quote["open"],
            quote["high"],
            quote["low"],
            quote["close"],
        ):
            date = datetime.fromtimestamp(
                timestamp, timezone.utc
            ).date().isoformat()
            point = cls._market_candle(date, open_price, high, low, close)
            if point:
                points.append(point)
        if not points:
            raise ValueError("Historical prices were not returned")
        return points

    def _handle_market_history(self, asset):
        assets = {"gold", "btc", "eth", "ice_cotton", "zce_cotton", "usd_cny"}
        if asset not in assets:
            self._send_error_json(
                400,
                "invalid_market_asset",
                self._t("market.invalid_asset"),
            )
            return

        try:
            if asset == "zce_cotton":
                response = self._fetch_public_text(
                    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
                    "var%20_gvHistoryData/InnerFuturesNewService.getDailyKLine"
                    "?symbol=CF0"
                )
                match = re.search(r"_gvHistoryData\((\[.*\])\)", response, re.S)
                if match is None:
                    raise ValueError("Historical cotton prices were not returned")
                history = json.loads(match.group(1))
                points = []
                for item in history[-30:]:
                    point = self._market_candle(
                        item.get("d"),
                        item.get("o"),
                        item.get("h"),
                        item.get("l"),
                        item.get("c"),
                    )
                    if point:
                        points.append(point)
            else:
                symbols = {
                    "gold": "GC%3DF",
                    "btc": "BTC-USD",
                    "eth": "ETH-USD",
                    "ice_cotton": "CT%3DF",
                    "usd_cny": "CNY%3DX",
                }
                payload = self._fetch_public_json(
                    "https://query1.finance.yahoo.com/v8/finance/chart/"
                    f"{symbols[asset]}?range=1mo&interval=1d"
                )
                points = self._yahoo_history_points(payload)
            if not points:
                raise ValueError("Historical prices were not returned")
        except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError):
            logging.exception("Could not retrieve market history for %s", asset)
            self._send_error_json(
                502,
                "market_history_unavailable",
                self._t("market.history_unavailable"),
            )
            return

        currencies = {
            "gold": "USD",
            "btc": "USD",
            "eth": "USD",
            "ice_cotton": "US¢",
            "zce_cotton": "CNY",
            "usd_cny": "CNY",
        }
        self._send_json(
            {
                "asset": asset,
                "currency": currencies[asset],
                "prices": points,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _handle_upload(self, user_id):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(400, "invalid_length", self._t("request.invalid_length"))
            return
        if content_length <= 0:
            self._send_error_json(400, "missing_file", self._t("upload.select_file"))
            return
        if content_length > MAX_UPLOAD_BYTES:
            self._send_error_json(413, "file_too_large", self._t("upload.too_large"))
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self._send_error_json(400, "invalid_content_type", self._t("upload.invalid_type"))
            return
        request_body = self.rfile.read(content_length)
        if len(request_body) != content_length:
            self._send_error_json(400, "incomplete_upload", self._t("upload.incomplete"))
            return
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1")
            + request_body
        )
        uploaded_file = next(
            (
                part
                for part in message.iter_parts()
                if part.get_content_disposition() == "form-data"
                and part.get_param("name", header="content-disposition") == "file"
            ),
            None,
        )
        if uploaded_file is None or not uploaded_file.get_filename():
            self._send_error_json(400, "missing_file", self._t("upload.select_file"))
            return

        filename = Path(uploaded_file.get_filename()).name
        file_bytes = uploaded_file.get_payload(decode=True) or b""
        stored_content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                cursor = connection.execute(
                    """INSERT INTO uploads (user_id, filename, content_type, size_bytes, content)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        filename,
                        stored_content_type,
                        len(file_bytes),
                        sqlite3.Binary(file_bytes),
                    ),
                )
                upload_id = cursor.lastrowid
                uploaded_at = connection.execute(
                    "SELECT uploaded_at FROM uploads WHERE id = ?", (upload_id,)
                ).fetchone()[0]
        except sqlite3.Error:
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
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                upload = connection.execute(
                    """SELECT filename, content_type, content FROM uploads
                       WHERE id = ? AND user_id = ?""",
                    (upload_id, user_id),
                ).fetchone()
        except sqlite3.Error:
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
        if not AI_API_KEY or not AI_MODEL:
            self._send_error_json(
                503,
                "ai.not_configured",
                self._t("ai.not_configured"),
            )
            return
        try:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                upload = connection.execute(
                    """SELECT filename, content FROM uploads
                       WHERE id = ? AND user_id = ?""",
                    (upload_id, user_id),
                ).fetchone()
        except sqlite3.Error:
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
            messages = build_ai_messages(filename, bytes(file_bytes), self._locale())
            analysis = request_ai_completion(messages)
        except AIAnalysisInputError as error:
            self._send_error_json(error.status, error.code, str(error))
            return
        except AIProviderError as error:
            self._send_error_json(
                error.status,
                error.code,
                self._t(error.code),
            )
            return
        self._send_json(
            {
                "analysis": {
                    "type": "ai",
                    "text": analysis,
                    "model": AI_MODEL,
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
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
        if parsed.path == "/api/session":
            try:
                user = self._current_user()
            except sqlite3.Error:
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
                with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                    uploads = connection.execute(
                        """SELECT id, filename, content_type, size_bytes, uploaded_at
                           FROM uploads WHERE user_id = ?
                           ORDER BY uploaded_at DESC, id DESC""",
                        (user_id,),
                    ).fetchall()
            except sqlite3.Error:
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
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                upload = connection.execute(
                    """SELECT filename, content_type, size_bytes, uploaded_at, content
                       FROM uploads WHERE id = ? AND user_id = ?""",
                    (upload_id, user_id),
                ).fetchone()
        except sqlite3.Error:
            logging.exception("Could not read uploaded file metadata")
            self._send_error_json(500, "database_error", self._t("request.database_read_error"))
            return
        if upload is None:
            self._send_error_json(404, "file_not_found", self._t("request.file_not_found"))
            return
        filename, content_type, size_bytes, uploaded_at, file_bytes = upload
        locale = self._locale()
        include_analysis = parse_qs(parsed.query).get("analyze") == ["1"]
        response = {
            "upload": {
                "id": upload_id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "uploaded_at": uploaded_at,
            },
            "preview": build_preview(filename, upload_id, file_bytes, locale),
            "analysis": build_analysis(filename, file_bytes, locale) if include_analysis else None,
        }
        self._send_json(response)

    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/api/auth":
            self._handle_auth()
            return
        if parsed.path == "/api/logout":
            self._handle_logout()
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

    def do_DELETE(self):
        parsed = urlsplit(self.path)
        upload_id = self._upload_id(parsed.path)
        if upload_id is None:
            self._send_error_json(404, "not_found", self._t("request.file_not_found"))
            return
        user = self._require_user()
        if user is None:
            return
        try:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                result = connection.execute(
                    "DELETE FROM uploads WHERE id = ? AND user_id = ?",
                    (upload_id, user[0]),
                )
        except sqlite3.Error:
            logging.exception("Could not delete uploaded file")
            self._send_error_json(500, "database_error", self._t("request.delete_failed"))
            return
        if result.rowcount == 0:
            self._send_error_json(404, "file_not_found", self._t("request.file_not_found"))
            return
        self._send_json({"ok": True})


def main():
    initialize_database()
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
