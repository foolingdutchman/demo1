import io
import json
from concurrent.futures import ThreadPoolExecutor
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
import image_analysis
import order_manager
import pdf_analysis
import pandas as pd
from openpyxl import Workbook
from pypdf import PdfReader


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

    def test_quotation_date_column_is_created_and_migrated(self):
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            columns = {
                row[1]: row[2]
                for row in connection.execute("PRAGMA table_info(cotton_quotation_summary)")
            }
        self.assertEqual(columns["quotation_date"], "TEXT")

        legacy_database = Path(self.temp_dir.name) / "legacy_quotations.db"
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.execute(
                "CREATE TABLE cotton_quotation_summary "
                "(row_number INTEGER PRIMARY KEY, batch_number TEXT)"
            )
            connection.execute(
                "INSERT INTO cotton_quotation_summary VALUES (1, 'existing')"
            )

        with patch.object(db_manager, "DATABASE_PATH", legacy_database):
            db_manager.initialize_database()
            with closing(sqlite3.connect(legacy_database)) as connection, connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT batch_number, quotation_date FROM cotton_quotation_summary"
                    ).fetchone(),
                    ("existing", None),
                )

                connection.execute(
                    "UPDATE cotton_quotation_summary SET quotation_date = ?",
                    ("2026-11-23",),
                )
            db_manager.initialize_database()

        with closing(sqlite3.connect(legacy_database)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT batch_number, quotation_date FROM cotton_quotation_summary"
                ).fetchone(),
                ("existing", "2026-11-23"),
            )

    def test_delivery_date_migration_preserves_existing_orders(self):
        legacy_database = Path(self.temp_dir.name) / "legacy_orders.db"
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, created_at TEXT)")
            connection.execute("INSERT INTO orders VALUES (1, '2026-01-01')")
        with patch.object(db_manager, "DATABASE_PATH", legacy_database):
            db_manager.initialize_database()
            db_manager.initialize_database()
        with closing(sqlite3.connect(legacy_database)) as connection:
            self.assertEqual(
                connection.execute("SELECT id, created_at, delivery_date FROM orders").fetchone(),
                (1, "2026-01-01", None),
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
            for sheet_config in excel_analysis.QUOTATION_SHEETS:
                connection.execute(f"DELETE FROM {sheet_config['table']}")
            connection.execute("CREATE TABLE cotton_quotation_prices (id INTEGER)")
            connection.execute("CREATE TABLE cotton_quotation_strength (id INTEGER)")

        with patch.object(db_manager, "COTTON_QUOTATION_PATH", workbook_path):
            db_manager.initialize_database()

        with patch.object(db_manager, "COTTON_QUOTATION_PATH", workbook_path):
            db_manager.initialize_database()

        unauthorized = self.request("/api/cotton-quotations")
        self.assertEqual(unauthorized.code, 401)
        self.assertEqual(self.register().code, 201)
        response = self.request("/api/cotton-quotations")
        self.assertEqual(response.code, 200)
        sheets = json.loads(response.read())["sheets"]
        self.assertEqual(
            set(sheets),
            {"quotation_summary", "sesame", "peanut", "peanut_meal"},
        )
        self.assertEqual(len(sheets["quotation_summary"]), 1)
        self.assertEqual(sheets["sesame"], [])
        self.assertEqual(sheets["peanut"], [])
        self.assertEqual(sheets["peanut_meal"], [])
        self.assertEqual(sheets["quotation_summary"][0]["batch_number"], "000123")
        self.assertIsNone(sheets["quotation_summary"][0]["quotation_date"])
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

    def test_existing_quotation_dates_are_backfilled_only_when_missing(self):
        upload_id, _ = db_manager.create_upload(
            None, "quote.xlsx", "application/octet-stream", b"test"
        )
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            connection.execute(
                "UPDATE uploads SET uploaded_at = ? WHERE id = ?",
                ("2025-01-02 23:59:59", upload_id),
            )
            connection.execute("DELETE FROM cotton_quotation_summary")
            connection.executemany(
                "INSERT INTO cotton_quotation_summary "
                "(row_number, source_upload_id, quotation_date) VALUES (?, ?, ?)",
                [(1, upload_id, None), (2, upload_id, "2024-12-31"), (3, None, None)],
            )

        db_manager.initialize_database()
        db_manager.initialize_database()
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            dates = connection.execute(
                "SELECT quotation_date FROM cotton_quotation_summary ORDER BY row_number"
            ).fetchall()
        self.assertEqual(dates, [("2025-01-02",), ("2024-12-31",), (None,)])

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
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            connection.execute(
                "UPDATE uploads SET uploaded_at = ? WHERE id = ?",
                ("2025-01-02 23:59:59", upload_id),
            )

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
            ["000201", "000200"],
        )
        self.assertEqual(
            [row["quotation_date"] for row in imported_rows if row["source_filename"] == "cotton_upload.xlsx"],
            ["2025-01-02", "2025-01-02"],
        )
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            dates = connection.execute(
                "SELECT quotation_date FROM cotton_quotation_summary "
                "WHERE source_upload_id = ? ORDER BY row_number",
                (upload_id,),
            ).fetchall()
        self.assertEqual(dates, [("2025-01-02",), ("2025-01-02",)])

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
            ["000201", "000200"],
        )

    def test_quotation_management_orders_and_restricts_updates(self):
        headers = {"Content-Type": "application/json"}
        update = json.dumps({
            "quotation_date": "2026-03-04",
            "gross_price_cny_per_ton": "18300.5",
            "batch_number": "000007",
        }).encode()
        self.assertEqual(self.request(
            "/api/cotton-quotations/1", "PATCH", update, headers
        ).code, 401)
        self.assertEqual(self.register().code, 201)
        upload_response = self.upload("quote.xlsx", b"test", "application/octet-stream")
        upload_id = json.loads(upload_response.read())["upload"]["id"]
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            connection.execute("DELETE FROM cotton_quotation_summary")
            connection.executemany(
                "INSERT INTO cotton_quotation_summary "
                "(row_number, batch_number, quotation_date, source_upload_id) "
                "VALUES (?, ?, ?, ?)",
                [
                    (1, "old", "2024-01-01", upload_id),
                    (2, "same-first", "2025-01-02", upload_id),
                    (3, "same-second", "2025-01-02", upload_id),
                    (4, "newest", "2026-01-01", upload_id),
                    (5, "middle", "2025-12-01", upload_id),
                    (6, "undated", None, None),
                ],
            )
        records = json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"]
        self.assertEqual([row["row_number"] for row in records], [4, 5, 3, 2, 1, 6])
        self.assertTrue(records[0]["editable"])
        self.assertFalse(records[-1]["editable"])

        for invalid in (
            {"quotation_date": "2025-02-29"},
            {"quotation_date": ""},
            {"package_count": "1.5"},
            {"package_count": "9223372036854775808"},
            {"gross_price_cny_per_ton": "NaN"},
            {"source_upload_id": "1"},
            {},
        ):
            response = self.request(
                "/api/cotton-quotations/5", "PATCH",
                json.dumps(invalid).encode(), headers,
            )
            self.assertEqual(response.code, 400, invalid)
        self.assertEqual(self.request(
            "/api/cotton-quotations/6", "PATCH", update, headers
        ).code, 404)
        self.assertEqual(self.request(
            "/api/cotton-quotations/999", "PATCH", update, headers
        ).code, 404)
        self.assertEqual(self.request(
            "/api/cotton-quotations/5", "PATCH", update, headers
        ).code, 200)
        updated = json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"][0]
        self.assertEqual(updated["row_number"], 5)
        self.assertEqual(updated["batch_number"], "000007")
        self.assertEqual(updated["gross_price_cny_per_ton"], 18300.5)
        self.assertEqual(updated["quotation_date"], "2026-03-04")
        self.assertEqual(updated["source_filename"], None)

        self.assertEqual(self.request("/api/logout", "POST").code, 200)
        other = {"mode": "register", "username": "other_user", "password": "SecretPass123",
                 "confirm_password": "SecretPass123"}
        self.assertEqual(self.request("/api/auth", "POST", json.dumps(other).encode(), headers).code, 201)
        self.assertEqual(self.request(
            "/api/cotton-quotations/5", "PATCH", update, headers
        ).code, 404)
        records = json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"]
        self.assertFalse(any(row["editable"] for row in records))
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            self.assertEqual(connection.execute(
                "SELECT batch_number, quotation_date, gross_price_cny_per_ton "
                "FROM cotton_quotation_summary WHERE row_number = 5"
            ).fetchone(), ("000007", "2026-03-04", 18300.5))
        self.assertEqual(self.request("/api/logout", "POST").code, 200)
        owner = {"mode": "login", "username": "api_test_user", "password": "SecretPass123"}
        self.assertEqual(self.request("/api/auth", "POST", json.dumps(owner).encode(), headers).code, 200)
        self.assertEqual(self.request(f"/api/uploads/{upload_id}", "DELETE").code, 200)
        self.assertEqual(self.request(
            "/api/cotton-quotations/5", "PATCH", update, headers
        ).code, 404)
        preflight = self.request("/api/cotton-quotations/5", "OPTIONS")
        self.assertIn("PATCH", preflight.headers["Access-Control-Allow-Methods"])

    def test_create_order_saves_quote_and_recalculated_pricing(self):
        headers = {"Content-Type": "application/json"}
        pricing = {
            "direct_buyer_bid_cny_per_ton": "", "buyer_bid_vat_inclusive": "true",
            "domestic_benchmark_cny_per_ton": "15360", "benchmark_vat_inclusive": "true",
            "quality_discount_cny_per_ton": "500", "quality_discount_basis": "gross",
            "domestic_sales_vat_percent": "9", "fx_cny_per_usd": "6.7",
            "duty_percent": "0", "import_vat_percent": "9",
            "import_vat_recoverable_percent": "100", "quota_cost_cny_per_ton": "4200",
            "quota_recoverable_vat_cny_per_ton": "0", "other_nonrecoverable_cost_cny_per_ton": "500",
            "other_recoverable_vat_cny_per_ton": "0", "target_profit_cny_per_ton": "300",
            "supplier_quote_usd_per_ton": "1150", "supplier_quote_basis": "FOB",
            "ocean_freight_usd_per_ton": "40", "insurance_usd_per_ton": "5",
            "other_cif_cost_usd_per_ton": "5", "quantity_tons": "100",
        }
        fields = {f"pricing_{key}": value for key, value in pricing.items()}
        fields.update({
            "benchmark_source": "manual",
            "delivery_date": "2026-12-10",
            "quotation_batch_number": "000123",
            "quotation_product": "棉花",
            "quotation_origin": "苏丹",
            "quotation_quotation_date": "2026-11-23",
            "quotation_source_filename": "quote.xlsx",
        })
        body = lambda data: json.dumps(data).encode()
        self.assertEqual(self.request("/api/orders").code, 401)
        self.assertEqual(self.request("/api/orders/1").code, 401)
        self.assertEqual(self.request("/api/orders/1/documents").code, 401)
        self.assertEqual(
            self.request(
                "/api/orders/1/documents/purchase_contract", "POST"
            ).code,
            401,
        )
        self.assertEqual(self.request("/api/orders/1", "DELETE").code, 401)
        self.assertEqual(self.request("/api/orders/1", "PATCH", body({}), headers).code, 401)
        self.assertEqual(self.request("/api/orders", "POST", body(fields), headers).code, 401)
        self.assertEqual(self.register().code, 201)
        supplier = self.request(
            "/api/suppliers",
            "POST",
            body({
                "short_name": "Test Supplier",
                "company_name": "Test Cotton Export Company",
                "company_address": "Khartoum, Sudan",
                "bank_account": "IBAN TEST-123",
            }),
            headers,
        )
        self.assertEqual(supplier.code, 201)
        supplier_id = json.loads(supplier.read())["supplier"]["id"]
        fields["supplier_id"] = str(supplier_id)
        profile_values = {
            "company_name": "Buyer Company Ltd.",
            "company_address": "Shanghai, China",
            "foreign_bank_account": "",
            "cny_bank_account": "",
        }
        self.assertEqual(
            self.request(
                "/api/user-profile",
                "PATCH",
                body(profile_values),
                headers,
            ).code,
            200,
        )
        self.assertEqual(json.loads(self.request("/api/orders").read()), {"orders": []})
        quotations = json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"]
        fields["quotation_id"] = str(quotations[0]["row_number"])
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            connection.execute(
                "UPDATE cotton_quotation_summary SET batch_number=?, product=?, origin=?, quotation_date=?, "
                "source_filename=?, net_weight_tons=? WHERE row_number=?",
                ("original", "棉花", "苏丹", "2026-11-23", "quote.xlsx", 100, int(fields["quotation_id"])),
            )
        for invalid in (
            {**fields, "pricing_quantity_tons": ""},
            {**fields, "pricing_quantity_tons": "-1"},
            {**fields, "pricing_supplier_quote_usd_per_ton": "0"},
            {**fields, "pricing_quantity_tons": "99"},
            {**fields, "quotation_quotation_date": "2026-02-30"},
            {**fields, "quotation_quotation_date": ""},
            {**fields, "delivery_date": ""},
            {**fields, "delivery_date": "2026-02-30"},
            {**fields, "quotation_id": "999999"},
            {**fields, "unexpected": "value"},
        ):
            self.assertEqual(self.request("/api/orders", "POST", body(invalid), headers).code, 400)
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
        response = self.request("/api/orders", "POST", body(fields), headers)
        self.assertEqual(response.code, 201)
        order_id = json.loads(response.read())["order"]["id"]
        db_manager.initialize_database()
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            row = connection.execute(
                "SELECT user_id, source_quotation_id, batch_number, product, origin, quotation_date, "
                "source_filename, quantity_tons, supplier_price_usd_per_ton, "
                "supplier_quote_basis, pricing_inputs, pricing_result "
                "FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            original_batch = connection.execute(
                "SELECT batch_number FROM cotton_quotation_summary WHERE row_number = ?",
                (int(fields["quotation_id"]),),
            ).fetchone()[0]
        self.assertEqual(row[1:10], (int(fields["quotation_id"]), "original", "棉花", "苏丹",
                                    "2026-11-23", "quote.xlsx", 100, 1150, "FOB"))
        self.assertEqual(json.loads(row[10]), pricing)
        self.assertEqual(json.loads(row[11])["supplier_evaluation"]["cif_usd_per_ton"], 1200)
        self.assertIsNotNone(row[0])
        self.assertEqual(original_batch, "original")
        self.assertEqual(self.request("/api/orders", "POST", body(fields), headers).code, 409)
        quotations = json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"]
        self.assertTrue(next(q for q in quotations if q["row_number"] == int(fields["quotation_id"]))["ordered"])
        manual = {**fields, "quotation_id": ""}
        manual_response = self.request("/api/orders", "POST", body(manual), headers)
        self.assertEqual(manual_response.code, 201)
        manual_id = json.loads(manual_response.read())["order"]["id"]
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            manual_row = connection.execute(
                "SELECT source_quotation_id, quotation_date FROM orders ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(manual_row[0])
        self.assertEqual(manual_row[1], "2026-11-23")
        manual_quote = next(q for q in json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"] if q["row_number"] == manual_row[0])
        self.assertTrue(manual_quote["ordered"])
        self.assertTrue(manual_quote["editable"])
        self.assertEqual(manual_quote["net_weight_tons"], 100)
        self.assertEqual(manual_quote["batch_number"], "000123")
        orders = json.loads(self.request("/api/orders").read())["orders"]
        self.assertEqual([order["id"] for order in orders], [manual_id, order_id])
        self.assertEqual(orders[1]["batch_number"], "original")
        self.assertEqual(orders[1]["product"], "棉花")
        self.assertEqual(orders[1]["origin"], "苏丹")
        self.assertEqual(orders[1]["source_filename"], "quote.xlsx")
        self.assertEqual(orders[1]["supplier_id"], supplier_id)
        self.assertEqual(
            orders[1]["supplier_company_name"], "Test Cotton Export Company"
        )
        self.assertEqual(orders[1]["pricing_inputs"], pricing)
        self.assertEqual(orders[1]["delivery_date"], "2026-12-10")
        self.assertEqual(orders[1]["pricing_result"]["supplier_evaluation"]["cif_usd_per_ton"], 1200)
        self.assertNotIn("user_id", orders[1])
        documents_path = f"/api/orders/{order_id}/documents"
        self.assertEqual(
            json.loads(self.request(documents_path).read()),
            {
                "documents": [],
                "related_documents": [],
                "waybills": [],
                "has_intermediary": False,
            },
        )
        generated = self.request(
            f"{documents_path}/purchase_contract", "POST"
        )
        self.assertEqual(generated.code, 201)
        generated_document = json.loads(generated.read())["document"]
        self.assertEqual(generated_document["document_type"], "purchase_contract")
        self.assertEqual(
            generated_document["filename"],
            f"purchase-contract-{order_id}.pdf",
        )
        self.assertEqual(
            self.request(
                f"{documents_path}/purchase_contract", "POST"
            ).code,
            200,
        )
        documents = json.loads(self.request(documents_path).read())["documents"]
        self.assertEqual(len(documents), 1)
        boundary = "order-document-boundary"
        related_body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="invoice.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + b"%PDF related document" + (
            f"\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="origin.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode() + b"PNG related document" + f"\r\n--{boundary}--\r\n".encode()
        related_analysis = {
            "category": "bill_of_lading",
            "category_label": "bill_of_lading",
            "fields": {
                "document_number": "BL-2026-001",
                "BL_NUMBER": "BL-2026-001",
                "BL_NET_WEIGHT": "100000",
                "WEIGHT_UNIT": "KGS",
                "DEPARTURE_DATE": "2026-11-01",
                "ARRIVAL_DATE": "2026-12-01",
                "PACK_NUMBER": "500",
                "PACK_UNIT": "BALES",
                "VOYAGE_NUMBER": "V001",
                "COO_INOVICE_NUMBER": "INV-88",
                "COO_INVOICE_DATE": "2026-10-28",
            },
            "text": "BILL OF LADING\nB/L No: BL-2026-001",
            "waybill": {
                "BL_NUMBER": "BL-2026-001",
                "BL_NET_WEIGHT": "100000",
                "WEIGHT_UNIT": "KGS",
                "DEPARTURE_DATE": "2026-11-01",
                "ARRIVAL_DATE": "2026-12-01",
                "PACK_NUMBER": "500",
                "PACK_UNIT": "BALES",
                "VOYAGE_NUMBER": "V001",
                "COO_INOVICE_NUMBER": "INV-88",
                "COO_INVOICE_DATE": "2026-10-28",
            },
            "matched": True,
            "source": "pdf",
            "page_count": 1,
        }
        class ImmediateThread:
            def __init__(self, target, args, daemon):
                self.target = target
                self.args = args
            def start(self):
                self.target(*self.args)

        with patch.object(
            api_server,
            "analyze_pdf_shipping_document",
            return_value=related_analysis,
        ), patch.object(
            api_server,
            "analyze_image_shipping_document",
            return_value=related_analysis,
        ), patch.object(api_server.threading, "Thread", ImmediateThread):
            related = self.request(
                f"{documents_path}/related_documents",
                "POST",
                related_body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        self.assertEqual(related.code, 201)
        uploaded_documents = json.loads(related.read())["documents"]
        self.assertEqual(len(uploaded_documents), 2)
        related_document = next(
            item for item in uploaded_documents
            if item["filename"] == "invoice.pdf"
        )
        self.assertEqual(related_document["filename"], "invoice.pdf")
        related_document = next(
            item
            for item in json.loads(
                self.request(documents_path).read()
            )["related_documents"]
            if item["filename"] == "invoice.pdf"
        )
        self.assertEqual(related_document["category"], "bill_of_lading")
        self.assertEqual(
            related_document["analysis"]["fields"]["document_number"],
            "BL-2026-001",
        )
        waybill = next(
            item
            for item in json.loads(
                self.request(documents_path).read()
            )["waybills"]
            if item["related_document_id"] == related_document["id"]
        )
        self.assertEqual(waybill["BL_NET_WEIGHT"], "100000")
        all_waybills = json.loads(self.request("/api/waybills").read())["waybills"]
        self.assertEqual(len(all_waybills), 2)
        self.assertTrue(all(
            item["order_id"] == order_id for item in all_waybills
        ))
        self.assertTrue(any(
            item["BL_NUMBER"] == "BL-2026-001" for item in all_waybills
        ))
        self.assertTrue(all(
            item["product"] == "棉花" and item["origin"] == "苏丹"
            for item in all_waybills
        ))
        changed_waybill = {**related_analysis["waybill"], "VOYAGE_NUMBER": "V002"}
        self.assertEqual(
            self.request(
                f"/api/orders/{order_id}/related-documents/"
                f"{related_document['id']}/waybill",
                "PATCH",
                json.dumps(changed_waybill).encode(),
                headers,
            ).code,
            200,
        )
        self.assertEqual(
            self.request(
                f"/api/orders/{order_id}/related-documents/{related_document['id']}",
                "PATCH",
                json.dumps({
                    "summary": "提单核验完成",
                    "recognized_text": "edited OCR content",
                }).encode(),
                headers,
            ).code,
            200,
        )
        reviewed = next(
            item
            for item in json.loads(
                self.request(f"/api/orders/{order_id}/documents").read()
            )["related_documents"]
            if item["id"] == related_document["id"]
        )
        self.assertEqual(reviewed["summary"], "提单核验完成")
        self.assertEqual(reviewed["recognized_text"], "edited OCR content")
        origin_document = next(
            item for item in uploaded_documents
            if item["filename"] == "origin.png"
        )
        self.assertEqual(
            self.request(
                f"/api/orders/{order_id}/related-documents/"
                f"{origin_document['id']}",
                "DELETE",
            ).code,
            200,
        )
        downloaded_related = self.request(
            f"/api/orders/{order_id}/related-documents/"
            f"{related_document['id']}/content"
        )
        self.assertEqual(downloaded_related.read(), b"%PDF related document")
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM order_documents "
                    "WHERE order_id = ?",
                    (order_id,),
                ).fetchone()[:1],
                (1,),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM order_related_documents "
                    "WHERE order_id = ?",
                    (order_id,),
                ).fetchone()[0],
                1,
            )
        contract = self.request(
            f"{documents_path}/purchase_contract/content"
        )
        self.assertEqual(contract.code, 200)
        self.assertEqual(contract.headers.get_content_type(), "application/pdf")
        self.assertIn(
            f"purchase-contract-{order_id}.pdf",
            contract.headers["Content-Disposition"],
        )
        contract_pdf = contract.read()
        self.assertTrue(contract_pdf.startswith(b"%PDF"))
        contract_text = "".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(contract_pdf)).pages
        )
        self.assertIn("SALES CONTRACT", contract_text)
        self.assertIn(f"PC-{order_id:06d}", contract_text)
        self.assertIn("Test Cotton Export Company", contract_text)
        self.assertIn("Khartoum, Sudan", contract_text)
        self.assertIn("Buyer Company Ltd.", contract_text)
        self.assertIn("Shanghai, China", contract_text)
        self.assertLess(
            contract_text.count("The buyer hereby confirmed"),
            2,
        )
        self.assertIn("115,000.00", contract_text)
        self.assertNotIn("{quantity}", contract_text)
        self.assertEqual(self.request("/api/logout", "POST").code, 200)
        other_user = {
            "mode": "register", "username": "other_user",
            "password": "SecretPass123", "confirm_password": "SecretPass123",
        }
        self.assertEqual(self.request("/api/auth", "POST", body(other_user), headers).code, 201)
        other_supplier = self.request(
            "/api/suppliers",
            "POST",
            body({
                "short_name": "Other",
                "company_name": "Other Supplier Co.",
                "company_address": "Other address",
                "bank_account": "",
            }),
            headers,
        )
        other_supplier_id = str(json.loads(other_supplier.read())["supplier"]["id"])
        other_fields = {**fields, "supplier_id": other_supplier_id}
        other_manual = {**manual, "supplier_id": other_supplier_id}
        self.assertEqual(json.loads(self.request("/api/orders").read()), {"orders": []})
        self.assertEqual(json.loads(self.request("/api/waybills").read()), {"waybills": []})
        self.assertEqual(self.request(documents_path).code, 404)
        self.assertEqual(
            self.request(
                f"{documents_path}/purchase_contract/content"
            ).code,
            404,
        )
        stored_order = orders[1]
        update_fields = {key: value for key, value in fields.items() if key != "quotation_id"}
        for name in (*order_manager.quotation_manager.QUOTATION_TYPES, *order_manager.SOURCE_FIELDS):
            value = stored_order[name]
            update_fields[f"quotation_{name}"] = "" if value is None else str(value)
        self.assertEqual(self.request(f"/api/orders/{order_id}").code, 404)
        self.assertEqual(self.request(f"/api/orders/{order_id}", "PATCH", body(update_fields), headers).code, 404)
        self.assertEqual(self.request(f"/api/orders/{order_id}", "DELETE").code, 404)
        other_manual_quote = next(q for q in json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"] if q["row_number"] == manual_row[0])
        self.assertFalse(other_manual_quote["editable"])
        self.assertEqual(self.request(f"/api/cotton-quotations/{manual_row[0]}", "PATCH", body({"batch_number": "other"}), headers).code, 404)
        self.assertEqual(
            self.request("/api/orders", "POST", body(other_fields), headers).code,
            409,
        )
        self.assertEqual(
            self.request("/api/orders", "POST", body(other_manual), headers).code,
            201,
        )
        self.assertEqual(len(json.loads(self.request("/api/orders").read())["orders"]), 1)
        self.assertEqual(self.request("/api/logout", "POST").code, 200)
        self.assertEqual(self.request("/api/auth", "POST", body({
            "mode": "login", "username": "api_test_user", "password": "SecretPass123",
        }), headers).code, 200)
        self.assertEqual(
            [order["id"] for order in json.loads(self.request("/api/orders").read())["orders"]],
            [manual_id, order_id],
        )
        self.assertEqual(json.loads(self.request(f"/api/orders/{order_id}").read())["order"]["id"], order_id)
        intermediary = self.request(
            "/api/suppliers",
            "POST",
            body({
                "short_name": "Broker",
                "company_name": "Cotton Intermediary Ltd.",
                "company_address": "Khartoum, Sudan",
                "bank_account": "Broker account",
            }),
            headers,
        )
        intermediary_id = str(json.loads(intermediary.read())["supplier"]["id"])
        edited = {
            **update_fields, "quotation_batch_number": "snapshot-edited",
            "quotation_quotation_date": "2026-11-25", "delivery_date": "2026-12-20",
            "intermediary_id": intermediary_id,
            "pricing_supplier_quote_usd_per_ton": "1200",
            "pricing_quantity_tons": "125",
        }
        self.assertEqual(self.request(f"/api/orders/{order_id}", "PATCH", body({**edited, "delivery_date": ""}), headers).code, 400)
        self.assertEqual(self.request(f"/api/orders/{order_id}", "PATCH", body(edited), headers).code, 200)
        edited_order = json.loads(self.request(f"/api/orders/{order_id}").read())["order"]
        self.assertEqual((edited_order["batch_number"], edited_order["delivery_date"], edited_order["quantity_tons"]), ("snapshot-edited", "2026-12-20", 125))
        self.assertEqual(edited_order["pricing_result"]["supplier_evaluation"]["cif_usd_per_ton"], 1250)
        remaining_documents = json.loads(
            self.request(f"/api/orders/{order_id}/documents").read()
        )["documents"]
        self.assertEqual(
            [item["document_type"] for item in remaining_documents],
            [],
        )
        related_documents = json.loads(
            self.request(f"/api/orders/{order_id}/documents").read()
        )["related_documents"]
        self.assertEqual(len(related_documents), 1)
        linked_generation = self.request(
            f"{documents_path}/purchase_contract", "POST"
        )
        self.assertEqual(linked_generation.code, 201)
        linked_documents = json.loads(linked_generation.read())["documents"]
        self.assertEqual(
            {item["document_type"] for item in linked_documents},
            {
                "supplier_intermediary_contract",
                "intermediary_user_contract",
            },
        )
        linked_pdf = self.request(
            f"{documents_path}/supplier_intermediary_contract/content"
        ).read()
        linked_text = "".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(linked_pdf)).pages
        )
        self.assertIn("Test Cotton Export Company", linked_text)
        self.assertIn("Cotton Intermediary Ltd.", linked_text)
        buyer_pdf = self.request(
            f"{documents_path}/intermediary_user_contract/content"
        ).read()
        buyer_text = "".join(
            page.extract_text() or ""
            for page in PdfReader(io.BytesIO(buyer_pdf)).pages
        )
        self.assertIn("Cotton Intermediary Ltd.", buyer_text)
        self.assertIn("Buyer Company Ltd.", buyer_text)
        original_quote = next(q for q in json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"] if q["row_number"] == int(fields["quotation_id"]))
        self.assertEqual(original_quote["batch_number"], "original")
        self.assertEqual(original_quote["quotation_date"], "2026-11-23")
        self.assertEqual(self.request(
            f"/api/cotton-quotations/{manual_row[0]}", "PATCH",
            body({"batch_number": "owner-edited"}), headers,
        ).code, 200)
        db_manager.initialize_database()
        manual_quote = next(q for q in json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"] if q["row_number"] == manual_row[0])
        self.assertEqual(manual_quote["batch_number"], "owner-edited")
        self.assertTrue(manual_quote["ordered"])
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            ids = []
            for net, standard in ((0, 90), (None, None)):
                ids.append(connection.execute(
                    "INSERT INTO cotton_quotation_summary (net_weight_tons, standard_weight_tons) VALUES (?, ?)",
                    (net, standard),
                ).lastrowid)
        fallback = {**fields, "quotation_id": str(ids[0])}
        self.assertEqual(self.request("/api/orders", "POST", body(fallback), headers).code, 400)
        self.assertEqual(self.request("/api/orders", "POST", body({
            **fallback, "pricing_quantity_tons": "90",
        }), headers).code, 201)
        no_weight = {**fields, "quotation_id": str(ids[1]), "pricing_quantity_tons": "77"}
        self.assertEqual(self.request("/api/orders", "POST", body(no_weight), headers).code, 201)
        new_order = json.loads(self.request("/api/orders").read())["orders"][0]
        self.assertEqual(new_order["quantity_tons"], 77)
        self.assertIsNone(new_order["net_weight_tons"])
        self.assertEqual(new_order["quotation_date"], "2026-11-23")
        quote_without_date = next(q for q in json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"] if q["row_number"] == ids[1])
        self.assertIsNone(quote_without_date["quotation_date"])
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            before = connection.execute("SELECT COUNT(*) FROM cotton_quotation_summary").fetchone()[0]
            connection.execute(
                "CREATE TRIGGER reject_test_order BEFORE INSERT ON orders "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        self.assertEqual(self.request("/api/orders", "POST", body(manual), headers).code, 500)
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cotton_quotation_summary").fetchone()[0], before)
            connection.execute("DROP TRIGGER reject_test_order")
            race_id = connection.execute(
                "INSERT INTO cotton_quotation_summary (batch_number) VALUES (?)", ("race",)
            ).lastrowid
        race_fields = {**fields, "quotation_id": str(race_id)}
        barrier = threading.Barrier(2)
        def place_order():
            barrier.wait(timeout=5)
            try:
                order_manager.create_order(1, race_fields)
            except db_manager.QuotationAlreadyOrdered:
                return "duplicate"
            return "created"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: place_order(), range(2)))
        self.assertCountEqual(results, ["created", "duplicate"])
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM orders WHERE source_quotation_id = ?", (race_id,)
            ).fetchone()[0], 1)
        self.assertEqual(self.request(f"/api/orders/{order_id}", "DELETE").code, 200)
        self.assertEqual(self.request(f"/api/orders/{order_id}").code, 404)
        with closing(sqlite3.connect(db_manager.DATABASE_PATH)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM order_documents WHERE order_id = ?",
                    (order_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM order_related_documents "
                    "WHERE order_id = ?",
                    (order_id,),
                ).fetchone()[0],
                0,
            )
        released = next(q for q in json.loads(self.request("/api/cotton-quotations").read())["sheets"]["quotation_summary"] if q["row_number"] == int(fields["quotation_id"]))
        self.assertFalse(released["ordered"])
        self.assertEqual(self.request("/api/orders", "POST", body(fields), headers).code, 201)

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

    def test_user_profile_and_password_can_be_updated(self):
        headers = {"Content-Type": "application/json"}
        self.assertEqual(self.request("/api/user-profile").code, 401)
        self.assertEqual(self.register().code, 201)
        profile = json.loads(self.request("/api/user-profile").read())["profile"]
        self.assertEqual(profile["username"], "api_test_user")
        self.assertIsNone(profile["company_name"])
        values = {
            "company_name": "Sudan Cotton Trading Ltd.",
            "company_address": "Shanghai, China",
            "foreign_bank_account": "Bank / SWIFT / USD 123456",
            "cny_bank_account": "Bank / CNY 654321",
        }
        response = self.request(
            "/api/user-profile",
            "PATCH",
            json.dumps(values).encode(),
            headers,
        )
        self.assertEqual(response.code, 200)
        self.assertEqual(
            {
                key: value
                for key, value in json.loads(
                    self.request("/api/user-profile").read()
                )["profile"].items()
                if key in values
            },
            values,
        )
        invalid_password = {
            "current_password": "WrongPass123",
            "new_password": "NewSecretPass456",
            "confirm_password": "NewSecretPass456",
        }
        self.assertEqual(
            self.request(
                "/api/user-password",
                "PATCH",
                json.dumps(invalid_password).encode(),
                headers,
            ).code,
            401,
        )
        password = {
            **invalid_password,
            "current_password": "SecretPass123",
        }
        self.assertEqual(
            self.request(
                "/api/user-password",
                "PATCH",
                json.dumps(password).encode(),
                headers,
            ).code,
            200,
        )
        self.assertEqual(self.request("/api/logout", "POST").code, 200)
        old_login = {
            "mode": "login",
            "username": "api_test_user",
            "password": "SecretPass123",
        }
        self.assertEqual(
            self.request(
                "/api/auth",
                "POST",
                json.dumps(old_login).encode(),
                headers,
            ).code,
            401,
        )
        self.assertEqual(
            self.request(
                "/api/auth",
                "POST",
                json.dumps(
                    {**old_login, "password": "NewSecretPass456"}
                ).encode(),
                headers,
            ).code,
            200,
        )

    def test_pdf_shipping_document_classification(self):
        analysis = pdf_analysis.analyze_shipping_document_text(
            """BILL OF LADING
B/L No.: BL2026-001
Vessel: MV COTTON STAR
Port of Loading: Port Sudan
Port of Discharge: Qingdao
Consignee: Buyer Company Ltd.
"""
        )
        self.assertEqual(analysis["category"], "bill_of_lading")
        self.assertTrue(analysis["matched"])
        self.assertEqual(analysis["fields"]["document_number"], "BL2026-001")
        self.assertEqual(analysis["fields"]["port_of_loading"], "Port Sudan")
        waybill_text = pdf_analysis.analyze_shipping_document_text(
            """BILL OF LADING
B/L No: BL-2026-777
Net Weight: 98500 KGS
Departure Date: 2026-11-02
Arrival Date: 2026-12-03
No. of Packages: 480 BALES
Voyage No: V777
Invoice No: INV-77
Invoice Date: 2026-10-20
"""
        )
        self.assertEqual(waybill_text["waybill"]["BL_NUMBER"], "BL-2026-777")
        self.assertEqual(waybill_text["waybill"]["BL_NET_WEIGHT"], "98500")
        self.assertEqual(waybill_text["waybill"]["PACK_UNIT"], "BALES")

    def test_image_only_pdf_uses_paddle_ocr(self):
        pdf_bytes = b"%PDF-image-only"
        with patch.object(
            pdf_analysis,
            "extract_pdf_text",
            return_value=(2, ""),
        ), patch.object(
            image_analysis,
            "extract_document_ocr_text",
            return_value=(
                "PHYTOSANITARY CERTIFICATE\n"
                "Certificate No: PSC-2026-11"
            ),
        ):
            analysis = pdf_analysis.analyze_pdf_shipping_document(pdf_bytes)
        self.assertEqual(analysis["category"], "phytosanitary_certificate")
        self.assertEqual(analysis["text_source"], "ocr")

    def test_image_shipping_document_classification(self):
        text = (
            "CERTIFICATE OF ORIGIN\n"
            "Certificate No.: COO-2026-88\n"
            "Country of Origin: Sudan"
        )
        with patch.object(
            image_analysis, "extract_document_ocr_text", return_value=text
        ):
            analysis = image_analysis.analyze_image_shipping_document(
                "origin.png", b"png-content", "en"
            )
        self.assertEqual(analysis["category"], "certificate_of_origin")
        self.assertEqual(analysis["fields"]["document_number"], "COO-2026-88")
        self.assertEqual(analysis["fields"]["country_of_origin"], "Sudan")

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

        def fake_public_html(url, encoding="utf-8"):
            if url == api_server.outcall_api.CC_INDEX_PRICES_URL:
                return (
                    "<div class='nd_cotton_block'><div class='nd_block_t'>"
                    "CC Index 3128B</div><div class='nd_block_d'>2026-11-20"
                    "</div><div class='nd_block_p'><span>16881</span> 元/吨"
                    "</div></div>"
                    "<div class='nd_cotton_block'><div class='nd_block_t'>"
                    "CC Index 2227B</div><div class='nd_block_d'>2026-11-20"
                    "</div><div class='nd_block_p'><span>15360</span> 元/吨"
                    "</div></div>"
                    "<div class='nd_cotton_block mgr0'><div class='nd_block_t'>"
                    "CC Index 2129B</div><div class='nd_block_d'>2026-11-20"
                    "</div><div class='nd_block_p'><span>17189</span> 元/吨"
                    "</div></div>"
                )
            return (
                "<table><tr data-currency='美元'>"
                "<td>美元</td><td>666.30</td><td>666.30</td>"
                "<td>669.11</td><td>669.11</td><td>671.31</td>"
                "<td>2026/11/20 09:55:53</td><td>09:55:53</td>"
                "</tr></table>"
            )

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
                side_effect=fake_public_html,
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
                "cc_index_3128b": 16881.0,
                "cc_index_2227b": 15360.0,
                "cc_index_2129b": 17189.0,
                "usd_cny": 6.6911,
            },
        )
        self.assertEqual(
            result["asset_currencies"],
            {
                "ice_cotton": "US¢",
                "zce_cotton": "CNY",
                "cc_index_3128b": "CNY",
                "cc_index_2227b": "CNY",
                "cc_index_2129b": "CNY",
                "usd_cny": "CNY",
            },
        )
        self.assertEqual(
            result["asset_dates"],
            {
                "cc_index_3128b": "2026-11-20",
                "cc_index_2227b": "2026-11-20",
                "cc_index_2129b": "2026-11-20",
            },
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
        self.assertEqual(fetch_html.call_count, 2)

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

    def test_public_market_prices_remain_available_when_cc_index_fails(self):
        def fake_public_json(url):
            if "gold-api.com" in url:
                return {"price": 2345.67}
            if "coingecko.com" in url:
                return {"bitcoin": {"usd": 68000.25}, "ethereum": {"usd": 3500.5}}
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 79.96}}]}}

        def fake_public_html(url, encoding="utf-8"):
            if url == api_server.outcall_api.CC_INDEX_PRICES_URL:
                raise URLError("CC Index unavailable")
            return (
                "<table><tr data-currency='美元'>"
                "<td>美元</td><td>666.30</td><td>666.30</td>"
                "<td>669.11</td><td>669.11</td><td>671.31</td>"
                "<td>2026/11/20 09:55:53</td><td>09:55:53</td>"
                "</tr></table>"
            )

        with patch.object(
            api_server.outcall_api, "fetch_public_json", side_effect=fake_public_json
        ), patch.object(
            api_server.outcall_api,
            "fetch_public_text",
            return_value=(
                'var hq_str_nf_CF0="棉花连续,094409,15560.000,15625.000,'
                '15460.000,0.000,15470.000,15475.000,15475.000";'
            ),
        ), patch.object(
            api_server.outcall_api,
            "fetch_public_html",
            side_effect=fake_public_html,
        ):
            result = api_server.outcall_api.get_market_prices()

        self.assertEqual(
            set(result["prices"]),
            {"gold", "btc", "eth", "ice_cotton", "zce_cotton", "usd_cny"},
        )
        self.assertEqual(result["asset_dates"], {})

    def test_sudan_cotton_pricing_returns_target_and_quote_evaluation(self):
        fields = {
            "direct_buyer_bid_cny_per_ton": "",
            "buyer_bid_vat_inclusive": "true",
            "domestic_benchmark_cny_per_ton": "15360",
            "benchmark_vat_inclusive": "true",
            "quality_discount_cny_per_ton": "500",
            "quality_discount_basis": "gross",
            "domestic_sales_vat_percent": "9",
            "fx_cny_per_usd": "6.7",
            "duty_percent": "0",
            "import_vat_percent": "9",
            "import_vat_recoverable_percent": "100",
            "quota_cost_cny_per_ton": "4200",
            "quota_recoverable_vat_cny_per_ton": "0",
            "other_nonrecoverable_cost_cny_per_ton": "500",
            "other_recoverable_vat_cny_per_ton": "0",
            "target_profit_cny_per_ton": "300",
            "supplier_quote_usd_per_ton": "1150",
            "supplier_quote_basis": "FOB",
            "ocean_freight_usd_per_ton": "40",
            "insurance_usd_per_ton": "5",
            "other_cif_cost_usd_per_ton": "5",
            "quantity_tons": "100",
        }
        response = self.request(
            "/api/sudan-cotton-pricing",
            "POST",
            json.dumps(fields).encode(),
            {"Content-Type": "application/json"},
        )

        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        net_sales = 14860 / 1.09
        self.assertAlmostEqual(
            result["max_target_cif_cny_per_ton"], net_sales - 5000
        )
        self.assertAlmostEqual(
            result["max_target_cif_usd_per_ton"], (net_sales - 5000) / 6.7
        )
        self.assertAlmostEqual(
            result["max_target_cif_us_cents_per_lb"],
            result["max_target_cif_usd_per_ton"] / 22.0462,
        )
        self.assertAlmostEqual(
            result["break_even_cif_cny_per_ton"], net_sales - 4700
        )
        self.assertAlmostEqual(
            result["supplier_evaluation"]["profit_cny_per_ton"],
            net_sales - 4200 - 500 - 1200 * 6.7,
        )
        self.assertEqual(
            result["supplier_evaluation"]["supplier_quote_usd_per_ton"], 1150
        )
        self.assertEqual(
            result["supplier_evaluation"]["cif_usd_per_ton"], 1200
        )
        self.assertAlmostEqual(
            result["supplier_evaluation"]["cif_us_cents_per_lb"],
            1200 / 22.0462,
        )
        self.assertAlmostEqual(
            result["supplier_evaluation"]["import_vat_paid_cny_per_ton"],
            1200 * 6.7 * 0.09,
        )
        self.assertAlmostEqual(
            result["supplier_evaluation"]["profit_for_quantity_cny"],
            result["supplier_evaluation"]["profit_cny_per_ton"] * 100,
        )
        self.assertEqual(len(result["scenarios"]["supplier_quote"]), 5)

        fields["direct_buyer_bid_cny_per_ton"] = "16000"
        response = self.request(
            "/api/sudan-cotton-pricing",
            "POST",
            json.dumps(fields).encode(),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        actual_bid_net = 16000 / 1.09
        self.assertAlmostEqual(
            result["max_target_cif_cny_per_ton"], actual_bid_net - 5000
        )
        self.assertAlmostEqual(
            result["scenarios"]["domestic_price"][1]["max_target_cif_usd_per_ton"],
            (actual_bid_net - 5000) / 6.7,
        )

        fields["direct_buyer_bid_cny_per_ton"] = ""
        fields["quality_discount_basis"] = "net"
        fields["duty_percent"] = "5"
        fields["import_vat_recoverable_percent"] = "50"
        fields["supplier_quote_usd_per_ton"] = ""
        response = self.request(
            "/api/sudan-cotton-pricing",
            "POST",
            json.dumps(fields).encode(),
            {"Content-Type": "application/json"},
        )
        self.assertEqual(response.code, 200)
        result = json.loads(response.read())
        cost_factor = 1 + 0.05 + (1 - 0.5) * 0.09 * (1 + 0.05)
        self.assertAlmostEqual(
            result["max_target_cif_cny_per_ton"],
            (15360 / 1.09 - 500 - 5000) / cost_factor,
        )

    def test_sudan_cotton_pricing_rejects_invalid_recoverability(self):
        fields = {
            "direct_buyer_bid_cny_per_ton": "",
            "buyer_bid_vat_inclusive": "true",
            "domestic_benchmark_cny_per_ton": "15360",
            "benchmark_vat_inclusive": "true",
            "quality_discount_cny_per_ton": "500",
            "quality_discount_basis": "gross",
            "domestic_sales_vat_percent": "9",
            "fx_cny_per_usd": "6.7",
            "duty_percent": "0",
            "import_vat_percent": "9",
            "import_vat_recoverable_percent": "101",
            "quota_cost_cny_per_ton": "4200",
            "quota_recoverable_vat_cny_per_ton": "0",
            "other_nonrecoverable_cost_cny_per_ton": "500",
            "other_recoverable_vat_cny_per_ton": "0",
            "target_profit_cny_per_ton": "300",
            "supplier_quote_usd_per_ton": "",
            "supplier_quote_basis": "CIF",
            "ocean_freight_usd_per_ton": "0",
            "insurance_usd_per_ton": "0",
            "other_cif_cost_usd_per_ton": "0",
            "quantity_tons": "",
        }
        response = self.request(
            "/api/sudan-cotton-pricing?locale=en",
            "POST",
            json.dumps(fields).encode(),
            {"Content-Type": "application/json"},
        )

        self.assertEqual(response.code, 400)
        error = json.loads(response.read())["error"]
        self.assertEqual(error["code"], "invalid_pricing_input")
        self.assertIn("recoverable shares", error["message"])

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
