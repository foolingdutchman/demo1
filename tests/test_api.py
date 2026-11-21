import io
import json
from contextlib import closing
from http.cookiejar import CookieJar
from http.server import HTTPServer
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch

import ai_analysis
import api_server
import db_manager
import excel_analysis
import pandas as pd
from openpyxl import Workbook


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = db_manager.DATABASE_PATH
        db_manager.DATABASE_PATH = Path(self.temp_dir.name) / "test.db"
        db_manager.initialize_database()
        self.server = HTTPServer(("127.0.0.1", 0), api_server.ApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def test_local_environment_file_loads_without_overriding_process_values(self):
        env_path = Path(self.temp_dir.name) / ".env"
        env_path.write_text(
            "AI_API_KEY=local-key\nAI_MODEL='local-model'\n",
            encoding="utf-8",
        )
        with patch.object(api_server, "LOCAL_ENV_PATH", env_path), patch.dict(
            api_server.os.environ,
            {"AI_API_KEY": "process-key"},
        ):
            api_server.os.environ.pop("AI_MODEL", None)
            api_server.load_local_environment()

            self.assertEqual(api_server.os.environ["AI_API_KEY"], "process-key")
            self.assertEqual(api_server.os.environ["AI_MODEL"], "local-model")

    def test_database_initialization_migrates_existing_upload_table(self):
        legacy_database = Path(self.temp_dir.name) / "legacy.db"
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.execute(
                """CREATE TABLE uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    content BLOB NOT NULL
                )"""
            )

        with patch.object(db_manager, "DATABASE_PATH", legacy_database):
            db_manager.initialize_database()

        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(uploads)")
            }
        self.assertTrue(
            {"ai_analysis", "ai_model", "ai_analyzed_at"}.issubset(columns)
        )

    def test_cotton_quotation_workbook_is_imported_once_for_authenticated_users(self):
        workbook_path = Path(self.temp_dir.name) / "cotton_quotation.xlsx"
        workbook = Workbook()
        row_values = {
            "image_label": "图1",
            "batch_number": "000123",
            "processor": "测试棉业",
            "origin": "新疆",
            "gross_price_cny_per_ton": 17700,
            "basis_cny_per_ton": 840,
            "strength_average": 31.2,
        }
        for index, sheet_config in enumerate(excel_analysis.COTTON_QUOTATION_SHEETS):
            worksheet = workbook.active if index == 0 else workbook.create_sheet()
            worksheet.title = sheet_config["worksheet"]
            worksheet.append(
                [header for _column, header, _column_type in sheet_config["columns"]]
            )
            worksheet.append(
                [
                    row_values.get(column)
                    for column, _header, _column_type in sheet_config["columns"]
                ]
            )
        workbook.save(workbook_path)
        workbook.close()

        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            connection.execute("DELETE FROM cotton_quotation_summary")
            connection.execute("CREATE TABLE cotton_quotation_prices (id INTEGER)")
            connection.execute("CREATE TABLE cotton_quotation_strength (id INTEGER)")

        with patch.object(db_manager, "COTTON_QUOTATION_PATH", workbook_path):
            db_manager.initialize_database()

        with patch.object(
            db_manager,
            "load_workbook",
            side_effect=AssertionError("populated summary should not be re-imported"),
        ):
            db_manager.initialize_database()

        unauthorized = self.request("/api/cotton-quotations")
        self.assertEqual(unauthorized.code, 401)
        self.assertEqual(self.register().code, 201)
        response = self.request("/api/cotton-quotations")
        self.assertEqual(response.code, 200)
        sheets = json.loads(response.read())["sheets"]
        self.assertEqual(set(sheets), {"quotation_summary"})
        self.assertEqual(len(sheets["quotation_summary"]), 1)
        self.assertEqual(sheets["quotation_summary"][0]["batch_number"], "000123")
        self.assertEqual(sheets["quotation_summary"][0]["gross_price_cny_per_ton"], 17700)
        self.assertEqual(
            sheets["quotation_summary"][0]["source_filename"],
            workbook_path.name,
        )
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name LIKE 'cotton_quotation_%'"
                )
            }
        self.assertEqual(tables, {"cotton_quotation_summary"})

    def test_analyzing_cotton_excel_imports_rows_idempotently(self):
        self.assertEqual(self.request("/api/cotton-quotations").code, 401)
        self.assertEqual(self.register().code, 201)
        workbook = io.BytesIO()
        pd.DataFrame(
            {
                "批号": ["000200", "000201"],
                "加工单位": ["测试棉业甲", "测试棉业乙"],
                "产地": ["新疆", "新疆"],
                "毛重单价(元/吨)": [17700, None],
                "基差(元/吨)": [None, 840],
                "马克隆值": [4.5, 4.7],
            }
        ).to_excel(workbook, index=False, sheet_name="本次报价", startrow=1)
        upload_response = self.upload(
            "cotton_upload.xlsx",
            workbook.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        upload_id = json.loads(upload_response.read())["upload"]["id"]

        response = self.request(f"/api/uploads/{upload_id}?analyze=1")
        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        self.assertEqual(result["analysis"]["type"], "excel")
        self.assertEqual(
            result["cotton_quotation_import"],
            {
                "rows_found": 2,
                "rows_inserted": 2,
                "already_imported": False,
            },
        )

        repeated = self.request(f"/api/uploads/{upload_id}?analyze=1")
        self.assertEqual(repeated.code, 200)
        self.assertEqual(
            json.loads(repeated.read())["cotton_quotation_import"],
            {
                "rows_found": 2,
                "rows_inserted": 0,
                "already_imported": True,
            },
        )
        quotations = json.loads(self.request("/api/cotton-quotations").read())
        imported_rows = quotations["sheets"]["quotation_summary"]
        self.assertEqual(
            [row["batch_number"] for row in imported_rows if row["source_filename"] == "cotton_upload.xlsx"],
            ["000200", "000201"],
        )
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM cotton_quotation_summary "
                "WHERE source_upload_id = ?",
                (upload_id,),
            ).fetchone()[0]
        self.assertEqual(count, 2)

        self.assertEqual(self.request("/api/logout", "POST").code, 200)
        second_user = {
            "mode": "register",
            "username": "api_test_user_two",
            "password": "SecretPass123",
            "confirm_password": "SecretPass123",
        }
        self.assertEqual(
            self.request(
                "/api/auth",
                "POST",
                json.dumps(second_user).encode(),
                {"Content-Type": "application/json"},
            ).code,
            201,
        )
        second_user_data = json.loads(
            self.request("/api/cotton-quotations").read()
        )["sheets"]["quotation_summary"]
        self.assertEqual(
            [row["batch_number"] for row in second_user_data if row["source_filename"] == "cotton_upload.xlsx"],
            ["000200", "000201"],
        )

    def test_analyzing_luidao_fuzhong_cotton_headers_imports_rows(self):
        self.assertEqual(self.register().code, 201)
        workbook = io.BytesIO()
        pd.DataFrame(
            {
                "基差": [840],
                "产地（中文）": ["新疆"],
                "轧花厂": ["测试轧花厂"],
                "批号": ["000300"],
                "公重": [12.3],
                "毛重": [12.8],
                "包数": [50],
                "长度": [29],
                "颜色级": ["白棉1级"],
                "马克隆": [4.5],
                "整齐度": [82],
            }
        ).to_excel(workbook, index=False, sheet_name="报价")
        upload_response = self.upload(
            "路易达孚国产棉报价 11-03 external.xlsx",
            workbook.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        upload_id = json.loads(upload_response.read())["upload"]["id"]

        response = self.request(f"/api/uploads/{upload_id}?analyze=1")
        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        self.assertEqual(
            result["cotton_quotation_import"],
            {
                "rows_found": 1,
                "rows_inserted": 1,
                "already_imported": False,
            },
        )
        quotations = json.loads(self.request("/api/cotton-quotations").read())
        imported = next(
            row
            for row in quotations["sheets"]["quotation_summary"]
            if row["source_filename"] == "路易达孚国产棉报价 11-03 external.xlsx"
        )
        self.assertEqual(imported["batch_number"], "000300")
        self.assertEqual(imported["processor"], "测试轧花厂")
        self.assertEqual(imported["origin"], "新疆")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        db_manager.DATABASE_PATH = self.original_database_path
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

    def test_auth_manager_preserves_login_and_logout_api_behavior(self):
        self.assertEqual(self.register().code, 201)
        self.assertEqual(self.request("/api/logout", "POST").code, 200)

        credentials = {
            "mode": "login",
            "username": "api_test_user",
            "password": "SecretPass123",
        }
        invalid_login = self.request(
            "/api/auth",
            "POST",
            json.dumps({**credentials, "password": "WrongPass123"}).encode(),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(invalid_login.code, 401)
        self.assertEqual(
            json.loads(invalid_login.read())["error"]["code"],
            "invalid_credentials",
        )

        login = self.request(
            "/api/auth",
            "POST",
            json.dumps(credentials).encode(),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(login.code, 200)
        self.assertEqual(json.loads(login.read())["user"]["username"], "api_test_user")
        self.assertTrue(json.loads(self.request("/api/session").read())["authenticated"])

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

    def test_public_market_prices_are_available_without_authentication(self):
        def fake_public_json(url):
            if "gold-api.com" in url:
                return {"price": 2345.67}
            if "coingecko.com" in url:
                return {"bitcoin": {"usd": 68000.25}, "ethereum": {"usd": 3500.5}}
            return {
                "chart": {
                    "result": [{"meta": {"regularMarketPrice": 79.96}}]
                }
            }

        with patch.object(
            api_server.outcall_api, "fetch_public_json", side_effect=fake_public_json
        ) as fetch, patch.object(
            api_server.outcall_api,
            "fetch_public_text",
            return_value=(
                'var hq_str_nf_CF0="棉花连续,094409,15560.000,15625.000,'
                '15460.000,0.000,15470.000,15475.000,15475.000";'
            ),
        ) as fetch_text:
            with patch.object(
                api_server.outcall_api,
                "fetch_public_html",
                return_value=(
                    "<table><tr data-currency='美元'>"
                    "<td>美元</td><td>666.30</td><td>666.30</td>"
                    "<td>669.11</td><td>669.11</td><td>671.31</td>"
                    "<td>2026/11/20 09:55:53</td><td>09:55:53</td>"
                    "</tr></table>"
                ),
            ) as fetch_html:
                response = self.request("/api/market-prices")

        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(
            result["prices"],
            {
                "gold": 2345.67,
                "btc": 68000.25,
                "eth": 3500.5,
                "ice_cotton": 79.96,
                "zce_cotton": 15470.0,
                "usd_cny": 6.6911,
            },
        )
        self.assertEqual(
            result["asset_currencies"],
            {"ice_cotton": "US¢", "zce_cotton": "CNY", "usd_cny": "CNY"},
        )
        self.assertEqual(
            result["derived_prices"]["import_cotton"],
            round(
                (15470 - 100) / 1.09 / 6.6911 / 22.0462
                - 3800 / 6.6911 / 22.0462
                - 1.5,
                2,
            ),
        )
        self.assertIn("updated_at", result)
        self.assertEqual(fetch.call_count, 3)
        fetch_text.assert_called_once()
        fetch_html.assert_called_once()

    def test_market_api_failure_returns_translated_error(self):
        with patch.object(
            api_server.outcall_api,
            "fetch_public_json",
            side_effect=URLError("upstream unavailable"),
        ):
            response = self.request("/api/market-prices?locale=en")

        self.assertEqual(response.code, 502)
        error = json.loads(response.read())["error"]
        self.assertEqual(error["code"], "market_data_unavailable")
        self.assertEqual(
            error["message"],
            "Prices are temporarily unavailable. Please try again later.",
        )

    def test_market_history_returns_yahoo_ohlc_candles(self):
        history = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1735693200, 1735779600, 1735866000],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0, 102.0, None],
                                    "high": [104.0, 106.0, None],
                                    "low": [99.0, 101.0, None],
                                    "close": [103.0, 105.0, None],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        with patch.object(
            api_server.outcall_api, "fetch_public_json", return_value=history
        ) as fetch:
            response = self.request("/api/market-history?asset=btc&locale=en")

        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        self.assertEqual(result["asset"], "btc")
        self.assertEqual(result["currency"], "USD")
        self.assertIn("BTC-USD", fetch.call_args.args[0])
        self.assertEqual(
            result["prices"],
            [
                {
                    "date": "2025-01-01",
                    "price": 103.0,
                    "open": 100.0,
                    "high": 104.0,
                    "low": 99.0,
                    "close": 103.0,
                },
                {
                    "date": "2025-01-02",
                    "price": 105.0,
                    "open": 102.0,
                    "high": 106.0,
                    "low": 101.0,
                    "close": 105.0,
                },
            ],
        )

    def test_market_history_reads_sina_cotton_ohlc_candles(self):
        history = (
            'var _gvHistoryData([{"d":"2026-11-18","o":"15490.000",'
            '"h":"15520.000","l":"15480.000","c":"15500.000"},'
            '{"d":"2026-11-19","o":"15500.000","h":"15580.000",'
            '"l":"15490.000","c":"15550.000"}]);'
        )
        with patch.object(
            api_server.outcall_api, "fetch_public_text", return_value=history
        ):
            response = self.request("/api/market-history?asset=zce_cotton")

        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(
            result["prices"],
            [
                {
                    "date": "2026-11-18",
                    "price": 15500.0,
                    "open": 15490.0,
                    "high": 15520.0,
                    "low": 15480.0,
                    "close": 15500.0,
                },
                {
                    "date": "2026-11-19",
                    "price": 15550.0,
                    "open": 15500.0,
                    "high": 15580.0,
                    "low": 15490.0,
                    "close": 15550.0,
                },
            ],
        )

    def test_market_history_rejects_unknown_assets(self):
        response = self.request("/api/market-history?asset=unknown&locale=en")
        self.assertEqual(response.code, 400)
        self.assertEqual(
            json.loads(response.read())["error"]["code"],
            "invalid_market_asset",
        )

    def test_ai_analysis_requires_server_configuration(self):
        self.assertEqual(self.register().code, 201)
        upload_response = self.upload("notes.txt", b"Private file contents", "text/plain")
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        with patch.object(ai_analysis, "AI_API_KEY", ""), patch.object(
            ai_analysis, "AI_MODEL", ""
        ):
            response = self.request(
                f"/api/uploads/{upload_id}/ai-analysis",
                "POST",
            )

        self.assertEqual(response.code, 503)
        self.assertEqual(
            json.loads(response.read())["error"]["code"],
            "ai.not_configured",
        )

    def test_ai_analysis_requires_authentication(self):
        response = self.request("/api/uploads/1/ai-analysis", "POST")
        self.assertEqual(response.code, 401)
        self.assertEqual(
            json.loads(response.read())["error"]["code"],
            "unauthorized",
        )

    def test_ai_provider_uses_openai_compatible_chat_completions(self):
        response = io.BytesIO(
            json.dumps(
                {
                    "choices": [
                        {"message": {"content": "Summary from provider."}}
                    ]
                }
            ).encode()
        )
        messages = [{"role": "user", "content": "Analyze this document."}]
        with patch.object(ai_analysis, "AI_API_BASE_URL", "https://ai.example/v1"), patch.object(
            ai_analysis, "AI_API_KEY", "server-secret"
        ), patch.object(
            ai_analysis, "AI_MODEL", "test-model"
        ), patch.object(
            ai_analysis, "urlopen", return_value=response
        ) as open_url:
            result = ai_analysis.request_ai_completion(messages)

        request = open_url.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(result, "Summary from provider.")
        self.assertEqual(request.full_url, "https://ai.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer server-secret")
        self.assertEqual(payload, {"model": "test-model", "messages": messages})

    def test_ai_analysis_sends_extracted_text_and_returns_model_result(self):
        self.assertEqual(self.register().code, 201)
        content = b"Quarterly sales increased by 12 percent."
        upload_response = self.upload("report.txt", content, "text/plain")
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        with patch.object(ai_analysis, "AI_API_KEY", "test-key"), patch.object(
            ai_analysis, "AI_MODEL", "test-model"
        ), patch.object(
            ai_analysis,
            "request_ai_completion",
            return_value="Summary: sales increased.",
        ) as complete:
            response = self.request(
                f"/api/uploads/{upload_id}/ai-analysis",
                "POST",
            )

        self.assertEqual(response.code, 200)
        result = json.loads(response.read())["analysis"]
        self.assertEqual(result["type"], "ai")
        self.assertEqual(result["text"], "Summary: sales increased.")
        self.assertEqual(result["model"], "test-model")
        self.assertIn("analyzed_at", result)
        self.assertIn(content.decode(), complete.call_args.args[0][1]["content"])

        details = json.loads(self.request(f"/api/uploads/{upload_id}").read())
        self.assertEqual(details["ai_analysis"], result)

    def test_ai_analysis_sends_supported_image_as_data_url(self):
        self.assertEqual(self.register().code, 201)
        upload_response = self.upload("chart.png", b"png bytes", "image/png")
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        with patch.object(ai_analysis, "AI_API_KEY", "test-key"), patch.object(
            ai_analysis, "AI_MODEL", "vision-model"
        ), patch.object(
            ai_analysis,
            "request_ai_completion",
            return_value="A chart.",
        ) as complete:
            response = self.request(
                f"/api/uploads/{upload_id}/ai-analysis",
                "POST",
            )

        self.assertEqual(response.code, 200)
        image_content = complete.call_args.args[0][1]["content"]
        self.assertIn("棉花质检报告", image_content[0]["text"])
        self.assertIn("断裂比强度", image_content[0]["text"])
        self.assertEqual(
            image_content[1]["image_url"]["url"],
            "data:image/png;base64,cG5nIGJ5dGVz",
        )

    def test_ai_analysis_extracts_all_excel_sheet_content(self):
        self.assertEqual(self.register().code, 201)
        workbook = io.BytesIO()
        pd.DataFrame({"value": [1, 2], "label": ["a", "b"]}).to_excel(
            workbook,
            index=False,
        )
        upload_response = self.upload(
            "sample.xlsx",
            workbook.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        with patch.object(ai_analysis, "AI_API_KEY", "test-key"), patch.object(
            ai_analysis, "AI_MODEL", "test-model"
        ), patch.object(
            ai_analysis,
            "request_ai_completion",
            return_value="Two rows.",
        ) as complete:
            response = self.request(
                f"/api/uploads/{upload_id}/ai-analysis",
                "POST",
            )

        self.assertEqual(response.code, 200)
        document = complete.call_args.args[0][1]["content"]
        self.assertIn("value,label", document)
        self.assertIn("1,a", document)
        self.assertIn("2,b", document)

    def test_ai_analysis_rejects_text_over_configured_limit(self):
        self.assertEqual(self.register().code, 201)
        upload_response = self.upload("report.txt", b"123456", "text/plain")
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        with patch.object(ai_analysis, "AI_API_KEY", "test-key"), patch.object(
            ai_analysis, "AI_MODEL", "test-model"
        ), patch.object(
            ai_analysis, "AI_MAX_INPUT_CHARS", 5
        ), patch.object(
            ai_analysis, "request_ai_completion"
        ) as complete:
            response = self.request(
                f"/api/uploads/{upload_id}/ai-analysis",
                "POST",
            )

        self.assertEqual(response.code, 413)
        self.assertEqual(
            json.loads(response.read())["error"]["code"],
            "ai.input_too_large",
        )
        complete.assert_not_called()

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
        result = json.loads(response.read())
        analysis = result["analysis"]
        self.assertEqual(analysis["type"], "excel")
        self.assertIsNone(result["cotton_quotation_import"])
        self.assertEqual(analysis["sheets"][0]["preview"]["columns"], ["value", "label"])
        self.assertEqual(analysis["sheets"][0]["rows"], 2)


if __name__ == "__main__":
    unittest.main()
