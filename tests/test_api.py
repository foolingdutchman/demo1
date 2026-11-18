import io
import json
from http.cookiejar import CookieJar
from http.server import HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import api_server
import pandas as pd


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = api_server.DATABASE_PATH
        api_server.DATABASE_PATH = Path(self.temp_dir.name) / "test.db"
        api_server.initialize_database()
        self.server = HTTPServer(("127.0.0.1", 0), api_server.ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        api_server.DATABASE_PATH = self.original_database_path
        self.temp_dir.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        request_headers = {"Origin": "http://127.0.0.1:5500"}
        if headers:
            request_headers.update(headers)
        request = Request(
            self.base_url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            return self.opener.open(request)
        except HTTPError as error:
            return error

    def register(self):
        return self.request(
            "/api/auth",
            "POST",
            json.dumps(
                {
                    "mode": "register",
                    "username": "api_test_user",
                    "password": "SecretPass123",
                    "confirm_password": "SecretPass123",
                }
            ).encode(),
            {"Content-Type": "application/json"},
        )

    def upload(self, filename, content, content_type):
        boundary = "----test-boundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.request(
            "/api/uploads",
            "POST",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

    def test_unauthenticated_api_error_is_json(self):
        response = self.request("/api/uploads?locale=en")
        self.assertEqual(response.code, 401)
        self.assertEqual(response.headers.get_content_type(), "application/json")
        error = json.loads(response.read())["error"]
        self.assertEqual(error["code"], "unauthorized")
        self.assertEqual(error["message"], "Please sign in to continue.")

    def test_register_upload_analyze_download_and_delete(self):
        response = self.register()
        self.assertEqual(response.code, 201)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            "http://127.0.0.1:5500",
        )
        self.assertTrue(json.loads(self.request("/api/session").read())["authenticated"])

        content = b"hello API\nsecond line"
        upload_response = self.upload("notes.txt", content, "text/plain")
        self.assertEqual(upload_response.code, 201)
        upload_id = json.loads(upload_response.read())["upload"]["id"]

        details = json.loads(self.request(f"/api/uploads/{upload_id}").read())
        self.assertEqual(details["preview"], {"type": "text", "text": content.decode()})
        analysis = json.loads(
            self.request(f"/api/uploads/{upload_id}?analyze=1").read()
        )["analysis"]
        self.assertEqual(
            analysis,
            {
                "type": "text",
                "line_count": 2,
                "character_count": 21,
                "word_count": 4,
            },
        )
        self.assertEqual(
            self.request(f"/api/uploads/{upload_id}/content?download=1").read(),
            content,
        )

        deleted = self.request(f"/api/uploads/{upload_id}", "DELETE")
        self.assertEqual(json.loads(deleted.read()), {"ok": True})
        self.assertEqual(self.request("/api/logout", "POST").code, 200)

    def test_excel_analysis_is_json_data(self):
        self.assertEqual(self.register().code, 201)
        workbook = io.BytesIO()
        pd.DataFrame({"value": [1, 2], "label": ["a", None]}).to_excel(
            workbook, index=False
        )
        upload_response = self.upload(
            "sample.xlsx",
            workbook.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        response = self.request(f"/api/uploads/{upload_id}?analyze=1")
        analysis = json.loads(response.read())["analysis"]
        self.assertEqual(analysis["type"], "excel")
        self.assertEqual(analysis["sheets"][0]["preview"]["columns"], ["value", "label"])
        self.assertEqual(analysis["sheets"][0]["rows"], 2)


if __name__ == "__main__":
    unittest.main()
