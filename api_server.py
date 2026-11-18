from contextlib import closing
from email import policy
from email.parser import BytesParser
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import hmac
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import parse_qs, quote, urlsplit

from excel_analysis import ExcelAnalysisError, build_excel_analysis
from i18n import SUPPORTED_LOCALES, get_locale, get_messages, translate
from image_analysis import IMAGE_MIME_TYPES, is_image_file
from pdf_analysis import PdfAnalysisError, build_pdf_analysis


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024
PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME = 7 * 24 * 60 * 60
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
