from http.server import BaseHTTPRequestHandler, HTTPServer
import codecs
import hashlib
import hmac
import re
import secrets
import time
from email import policy
from email.parser import BytesParser
from contextlib import closing
from html import escape
from http.cookies import CookieError, SimpleCookie
import mimetypes
from pathlib import Path
import sqlite3
from string import Template
from urllib.parse import parse_qs, quote, urlsplit


INDEX_TEMPLATE = Template(Path(__file__).with_name("index.html").read_text(encoding="utf-8"))
POST_TEMPLATE = Template(Path(__file__).with_name("post.html").read_text(encoding="utf-8"))
AUTH_TEMPLATE = Template(Path(__file__).with_name("auth.html").read_text(encoding="utf-8"))
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FORM_BYTES = 16 * 1024
PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME = 7 * 24 * 60 * 60
DATABASE_PATH = Path(__file__).with_name("uploads.db")
TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json"}


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


def build_text_preview(filename, file_bytes):
    if Path(filename).suffix.lower() not in TEXT_EXTENSIONS:
        return '<section class="preview"><p>该文件暂不支持网页预览，可下载到本地查看。</p></section>'

    try:
        if file_bytes.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            encoding = "utf-32"
        elif file_bytes.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding = "utf-16"
        else:
            encoding = "utf-8-sig"
        file_content = file_bytes.decode(encoding)
    except UnicodeDecodeError:
        return '<section class="preview"><p>文件已保存，但无法按支持的文本编码预览。</p></section>'

    return (
        f'<section class="preview"><h2>文本预览</h2>'
        f'<pre>{escape(file_content)}</pre></section>'
    )


class WebsiteHandler(BaseHTTPRequestHandler):
    def _send_page(self, page, status_code=200):
        body = page.encode("utf-8") if isinstance(page, str) else page
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    @staticmethod
    def _safe_next_path(request_target):
        candidate = parse_qs(urlsplit(request_target).query).get("next", ["/"])[0]
        parsed = urlsplit(candidate)
        if (
            not candidate.startswith("/")
            or candidate.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or "\\" in candidate
            or any(ord(character) < 32 for character in candidate)
        ):
            return "/"
        return candidate

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
        return row if row else None

    @staticmethod
    def _session_token_hash(token):
        try:
            return hashlib.sha256(token.encode("ascii")).hexdigest()
        except UnicodeEncodeError:
            return None

    def _send_auth_page(self, next_path="/", message="", status_code=200, mode="login"):
        page = AUTH_TEMPLATE.substitute(
            next_path=escape(next_path, quote=True),
            message=escape(message),
            mode=mode,
        )
        self._send_page(page, status_code)

    def _create_session(self, user_id, next_path):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        expires_at = int(time.time()) + SESSION_LIFETIME
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, user_id, expires_at),
            )
        cookie = f"session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_LIFETIME}"
        self._redirect(next_path, cookie)

    def _read_form(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error_page(400, "无效的请求长度")
            return None
        if content_length <= 0 or content_length > MAX_FORM_BYTES:
            self.send_error_page(400, "表单内容为空或超出大小限制")
            return None
        if not self.headers.get("Content-Type", "").lower().startswith("application/x-www-form-urlencoded"):
            self.send_error_page(400, "请求格式错误")
            return None
        try:
            body = self.rfile.read(content_length).decode("utf-8")
            return {
                key: values[0]
                for key, values in parse_qs(body, keep_blank_values=True, max_num_fields=8).items()
            }
        except (UnicodeDecodeError, ValueError):
            self.send_error_page(400, "表单内容无效")
            return None

    def _handle_auth(self):
        fields = self._read_form()
        if fields is None:
            return

        next_path = self._safe_next_path("?next=" + fields.get("next", "/"))
        mode = fields.get("mode", "login")
        if mode not in {"login", "register"}:
            self._send_auth_page(next_path, "无效的操作模式。", 400)
            return

        username = fields.get("username", "").strip()
        password = fields.get("password", "")
        confirm_password = fields.get("confirm_password", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            self._send_auth_page(next_path, "用户名须为 3 至 32 位字母、数字、下划线、点或连字符。", 400, mode)
            return
        if not 8 <= len(password) <= 128:
            self._send_auth_page(next_path, "密码长度须为 8 至 128 位。", 400, mode)
            return

        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            user = connection.execute(
                "SELECT id, username, password_salt, password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if mode == "register":
                if user is not None:
                    self._send_auth_page(next_path, "该用户名已注册，请切换到登录。", 409, mode)
                    return
                if password != confirm_password:
                    self._send_auth_page(next_path, "两次输入的密码不一致。", 400, mode)
                    return
                if not all(re.search(pattern, password) for pattern in (r"[A-Z]", r"[a-z]", r"[0-9]")):
                    self._send_auth_page(next_path, "新密码须同时包含大写字母、小写字母和数字。", 400, mode)
                    return
                salt = secrets.token_bytes(16)
                password_hash = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
                )
                cursor = connection.execute(
                    "INSERT INTO users (username, password_salt, password_hash) VALUES (?, ?, ?)",
                    (username, sqlite3.Binary(salt), sqlite3.Binary(password_hash)),
                )
                user_id = cursor.lastrowid
            else:
                if user is None:
                    self._send_auth_page(next_path, "该用户名尚未注册，请切换到注册。", 404, mode)
                    return
                supplied_hash = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), user[2], PASSWORD_ITERATIONS
                )
                if not hmac.compare_digest(supplied_hash, user[3]):
                    self._send_auth_page(next_path, "用户名或密码不正确。", 401, mode)
                    return
                user_id = user[0]

        self._create_session(user_id, next_path)

    def _handle_logout(self):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            cookies = SimpleCookie()
        session_cookie = cookies.get("session")
        if session_cookie is not None:
            token_hash = self._session_token_hash(session_cookie.value)
            if token_hash is not None:
                with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self._redirect(
            "/login",
            "session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
        )

    def send_error_page(self, status_code, message):
        page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>请求失败</title></head>
<body>
    <h1>请求失败</h1>
    <p>{escape(message)}</p>
    <p><a href="/">返回首页</a></p>
</body>
</html>""".encode("utf-8")
        self._send_page(page, status_code)

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/login":
            user = self._current_user()
            next_path = self._safe_next_path(self.path)
            if user is not None:
                self._redirect(next_path)
            else:
                self._send_auth_page(next_path)
            return

        user = self._current_user()
        if user is None:
            self._redirect("/login?next=" + quote(self.path, safe=""))
            return
        user_id, username = user

        if request_path.startswith("/post/"):
            upload_id_text = request_path.removeprefix("/post/")
            if not upload_id_text.isdigit():
                self.send_error_page(404, "文件不存在")
                return

            try:
                with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                    upload = connection.execute(
                        """SELECT filename, content_type, size_bytes, uploaded_at, content
                           FROM uploads WHERE id = ? AND user_id = ?""",
                        (int(upload_id_text), user_id),
                    ).fetchone()
            except sqlite3.Error:
                self.send_error_page(500, "读取数据库失败")
                return

            if upload is None:
                self.send_error_page(404, "文件不存在")
                return

            filename, content_type, size_bytes, uploaded_at, file_bytes = upload
            page = POST_TEMPLATE.substitute(
                filename=escape(filename),
                file_size=f"{size_bytes:,}",
                upload_id=int(upload_id_text),
                content_type=escape(content_type),
                uploaded_at=escape(uploaded_at),
                text_preview=build_text_preview(filename, file_bytes),
            )
            self._send_page(page)
            return

        if request_path.startswith("/download/"):
            upload_id_text = request_path.removeprefix("/download/")
            if not upload_id_text.isdigit():
                self.send_error_page(404, "文件不存在")
                return

            try:
                with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                    upload = connection.execute(
                        "SELECT filename, content_type, content FROM uploads WHERE id = ? AND user_id = ?",
                        (int(upload_id_text), user_id),
                    ).fetchone()
            except sqlite3.Error:
                self.send_error_page(500, "读取数据库失败")
                return

            if upload is None:
                self.send_error_page(404, "文件不存在")
                return

            filename, content_type, file_bytes = upload
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
            )
            self.send_header("Content-Length", str(len(file_bytes)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(file_bytes)
            return

        if request_path != "/":
            self.send_error_page(404, "页面不存在")
            return
        with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
            uploads = connection.execute(
                """SELECT id, filename, size_bytes, uploaded_at FROM uploads
                   WHERE user_id = ? ORDER BY uploaded_at DESC, id DESC""",
                (user_id,),
            ).fetchall()
        if uploads:
            upload_records = "".join(
                f'<tr><td><a href="/post/{upload_id}">{escape(filename)}</a></td>'
                f'<td>{size_bytes:,} 字节</td><td>{escape(uploaded_at)}</td>'
                f'<td><form class="delete-form" method="post" action="/delete/{upload_id}" '
                f'onsubmit="return confirm(\'确定删除这个文件吗？\')">'
                '<button class="delete-button" type="submit">删除</button></form></td></tr>'
                for upload_id, filename, size_bytes, uploaded_at in uploads
            )
            upload_records = (
                "<div class=\"table-wrap\"><table><thead><tr>"
                "<th>文件名</th><th>大小</th><th>上传时间</th><th>操作</th></tr></thead>"
                f"<tbody>{upload_records}</tbody></table></div>"
            )
        else:
            upload_records = '<p class="empty">还没有上传文件。</p>'
        page = INDEX_TEMPLATE.substitute(
            username=escape(username),
            upload_records=upload_records,
        )
        self._send_page(page)

    def do_POST(self):
        request_path = urlsplit(self.path).path
        if request_path == "/auth":
            self._handle_auth()
            return
        if request_path == "/logout":
            self._handle_logout()
            return
        if request_path.startswith("/delete/"):
            user = self._current_user()
            if user is None:
                self._redirect("/login?next=%2F")
                return
            upload_id_text = request_path.removeprefix("/delete/")
            if not upload_id_text.isdigit():
                self.send_error_page(404, "文件不存在")
                return
            try:
                with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                    result = connection.execute(
                        "DELETE FROM uploads WHERE id = ? AND user_id = ?",
                        (int(upload_id_text), user[0]),
                    )
            except sqlite3.Error:
                self.send_error_page(500, "删除文件失败")
                return
            if result.rowcount == 0:
                self.send_error_page(404, "文件不存在")
                return
            self._redirect("/")
            return
        if request_path != "/button-click":
            self.send_error(404)
            return
        user = self._current_user()
        if user is None:
            self._redirect("/login?next=%2F")
            return
        user_id, _ = user

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error_page(400, "无效的请求长度")
            return

        if content_length <= 0:
            self.send_error_page(400, "请选择要上传的文件")
            return
        if content_length > MAX_UPLOAD_BYTES:
            self.send_error_page(413, "文件不能超过 10 MiB")
            return

        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            self.send_error_page(400, "请求格式必须是 multipart/form-data")
            return

        request_body = self.rfile.read(content_length)
        if len(request_body) != content_length:
            self.send_error_page(400, "上传数据不完整")
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
            self.send_error_page(400, "请选择要上传的文件")
            return

        original_filename = Path(uploaded_file.get_filename()).name
        file_bytes = uploaded_file.get_payload(decode=True) or b""
        stored_content_type = mimetypes.guess_type(original_filename)[0] or "application/octet-stream"
        try:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
                cursor = connection.execute(
                          """INSERT INTO uploads (user_id, filename, content_type, size_bytes, content)
                              VALUES (?, ?, ?, ?, ?)""",
                    (
                                user_id,
                        original_filename,
                        stored_content_type,
                        len(file_bytes),
                        sqlite3.Binary(file_bytes),
                    ),
                )
                upload_id = cursor.lastrowid
        except sqlite3.Error:
            self.send_error_page(500, "文件已接收，但保存到数据库失败")
            return

        self._redirect(f"/post/{upload_id}")


if __name__ == "__main__":
    initialize_database()
    address = ("127.0.0.1", 8000)
    server = HTTPServer(address, WebsiteHandler)
    print("网站已启动： http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n网站已停止。")
        server.server_close()
