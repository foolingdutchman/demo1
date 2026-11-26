from contextlib import closing
from pathlib import Path
import json
import logging
import sqlite3

from openpyxl import load_workbook

import excel_analysis
import quotation_manager


DATABASE_PATH = Path(__file__).with_name("uploads.db")
COTTON_QUOTATION_PATH = Path(__file__).with_name("quotations.xlsx")
DatabaseError = sqlite3.Error
PRODUCT_PRICE_TABLES = {
    "sesame": "sesame_daily_prices",
    "peanut": "peanut_daily_prices",
    "peanut_meal": "peanut_meal_daily_prices",
}


def initialize_database():
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                company_name TEXT,
                company_address TEXT,
                foreign_bank_account TEXT,
                cny_bank_account TEXT
            )"""
        )
        user_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
        for column in (
            "company_name",
            "company_address",
            "foreign_bank_account",
            "cny_bank_account",
        ):
            if column not in user_columns:
                connection.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")
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
                content BLOB NOT NULL,
                ai_analysis TEXT,
                ai_model TEXT,
                ai_analyzed_at TEXT
            )"""
        )
        upload_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(uploads)")
        }
        migrations = {
            "user_id": "INTEGER REFERENCES users(id)",
            "ai_analysis": "TEXT",
            "ai_model": "TEXT",
            "ai_analyzed_at": "TEXT",
        }
        for column, definition in migrations.items():
            if column not in upload_columns:
                connection.execute(
                    f"ALTER TABLE uploads ADD COLUMN {column} {definition}"
                )
        initialize_cotton_quotations(connection)
        initialize_product_price_tables(connection)
        initialize_orders(connection)
        initialize_order_documents(connection)
        initialize_suppliers(connection)
        initialize_order_related_documents(connection)
        initialize_waybills(connection)


def initialize_product_price_tables(connection):
    for table in PRODUCT_PRICE_TABLES.values():
        connection.execute(
            f"""CREATE TABLE IF NOT EXISTS {table} (
                fetched_date TEXT PRIMARY KEY,
                quote_date TEXT NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )


def get_daily_product_price(product, fetched_date):
    table = PRODUCT_PRICE_TABLES.get(product)
    if table is None:
        raise ValueError("Invalid market product")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        initialize_product_price_tables(connection)
        row = connection.execute(
            f"""SELECT quote_date, price, currency
                FROM {table}
                WHERE fetched_date = ?""",
            (fetched_date,),
        ).fetchone()
    if row is None:
        return None
    return {"date": row[0], "price": row[1], "currency": row[2]}


def save_daily_product_price(product, fetched_date, quote_date, price, currency):
    table = PRODUCT_PRICE_TABLES.get(product)
    if table is None:
        raise ValueError("Invalid market product")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        initialize_product_price_tables(connection)
        connection.execute(
            f"""INSERT INTO {table} (fetched_date, quote_date, price, currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fetched_date) DO UPDATE SET
                    quote_date = excluded.quote_date,
                    price = excluded.price,
                    currency = excluded.currency,
                    updated_at = CURRENT_TIMESTAMP""",
            (fetched_date, quote_date, price, currency),
        )


def initialize_orders(connection):
    quotation_column_types = {
        column: kind
        for config in excel_analysis.QUOTATION_SHEETS
        for column, _header, kind in config["columns"]
    }
    quotation_columns = ", ".join(
        f"{column} {kind}" for column, kind in quotation_column_types.items()
    )
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_quotation_id INTEGER,
            source_quotation_product TEXT,
            {quotation_columns},
            quotation_date TEXT,
            delivery_date TEXT,
            source_filename TEXT,
            source_sheet_name TEXT,
            benchmark_source TEXT NOT NULL,
            quantity_tons REAL NOT NULL,
            supplier_price_usd_per_ton REAL NOT NULL,
            supplier_quote_basis TEXT NOT NULL,
            pricing_inputs TEXT NOT NULL,
            pricing_result TEXT NOT NULL
        )"""
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(orders)")}
    for column, definition in {
        "source_quotation_id": "INTEGER",
        "source_quotation_product": "TEXT",
        "delivery_date": "TEXT",
        "supplier_id": "INTEGER",
        "intermediary_id": "INTEGER",
    }.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")
            columns.add(column)
    for column, kind in quotation_column_types.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE orders ADD COLUMN {column} {kind}")
            columns.add(column)
    connection.execute(
        """UPDATE orders
           SET origin = COALESCE(origin, (
                   SELECT q.origin FROM cotton_quotation_summary AS q
                   WHERE q.row_number = orders.source_quotation_id
               )),
               product = COALESCE(product, (
                   SELECT q.product FROM cotton_quotation_summary AS q
                   WHERE q.row_number = orders.source_quotation_id
               ))
           WHERE source_quotation_id IS NOT NULL
             AND (origin IS NULL OR product IS NULL)"""
    )


def initialize_suppliers(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            short_name TEXT NOT NULL,
            company_name TEXT NOT NULL,
            company_address TEXT NOT NULL,
            bank_account TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def initialize_order_documents(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS order_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            content BLOB NOT NULL,
            UNIQUE(order_id, document_type)
        )"""
    )


def initialize_order_related_documents(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS order_related_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            category TEXT,
            analysis_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            content BLOB NOT NULL
        )"""
    )
    columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(order_related_documents)"
        )
    }
    for column, definition in {
        "analysis_status": "TEXT NOT NULL DEFAULT 'pending'",
        "summary": "TEXT",
        "recognized_text": "TEXT",
    }.items():
        if column not in columns:
            connection.execute(
                f"ALTER TABLE order_related_documents ADD COLUMN {column} {definition}"
            )
    connection.execute(
        """INSERT INTO order_related_documents
           (order_id, user_id, filename, content_type, size_bytes, category,
            analysis_json, analysis_status, recognized_text, content, created_at)
           SELECT order_id, user_id, filename, content_type, size_bytes,
                  NULL, NULL, 'pending', NULL, content, created_at
           FROM order_documents
           WHERE document_type = 'related_documents'"""
    )
    connection.execute(
        "DELETE FROM order_documents WHERE document_type = 'related_documents'"
    )


def initialize_waybills(connection):
    connection.execute(
        """CREATE TABLE IF NOT EXISTS waybills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            related_document_id INTEGER NOT NULL UNIQUE,
            BL_NUMBER TEXT,
            BL_NET_WEIGHT TEXT,
            WEIGHT_UNIT TEXT,
            DEPARTURE_DATE TEXT,
            ARRIVAL_DATE TEXT,
            PACK_NUMBER TEXT,
            PACK_UNIT TEXT,
            VOYAGE_NUMBER TEXT,
            COO_INOVICE_NUMBER TEXT,
            COO_INVOICE_DATE TEXT,
            product TEXT,
            origin TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(waybills)")
    }
    for column in ("product", "origin"):
        if column not in columns:
            connection.execute(f"ALTER TABLE waybills ADD COLUMN {column} TEXT")
    connection.execute(
        """UPDATE waybills
           SET product = COALESCE(product, (
                   SELECT o.product FROM orders AS o WHERE o.id = waybills.order_id
               )),
               origin = COALESCE(origin, (
                   SELECT o.origin FROM orders AS o WHERE o.id = waybills.order_id
               ))
           WHERE product IS NULL OR origin IS NULL"""
    )


def initialize_cotton_quotations(connection):
    template_path = COTTON_QUOTATION_PATH
    connection.execute("DROP TABLE IF EXISTS cotton_quotation_prices")
    connection.execute("DROP TABLE IF EXISTS cotton_quotation_strength")
    for sheet_config in excel_analysis.QUOTATION_SHEETS:
        column_definitions = ", ".join(
            f"{column} {column_type}"
            for column, _header, column_type in sheet_config["columns"]
        )
        connection.execute(
            f"""CREATE TABLE IF NOT EXISTS {sheet_config["table"]} (
                row_number INTEGER PRIMARY KEY,
                {column_definitions},
                quotation_date TEXT,
                source_upload_id INTEGER,
                source_sheet_name TEXT,
                source_row_number INTEGER,
                source_filename TEXT
            )"""
        )

    for sheet_config in excel_analysis.QUOTATION_SHEETS:
        table = sheet_config["table"]
        table_columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        source_migrations = {
            "quotation_date": "TEXT",
            "source_upload_id": "INTEGER",
            "source_sheet_name": "TEXT",
            "source_row_number": "INTEGER",
            "source_filename": "TEXT",
            "owner_user_id": "INTEGER",
        }
        for column, definition in source_migrations.items():
            if column not in table_columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        for column, _header, kind in sheet_config["columns"]:
            if column not in table_columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    summary_table = excel_analysis.COTTON_QUOTATION_SHEETS[0]["table"]
    backfill_cotton_quotation_dates(connection)
    for sheet_config in excel_analysis.QUOTATION_SHEETS:
        connection.execute(
            f"""UPDATE {sheet_config['table']}
                SET source_sheet_name = COALESCE(source_sheet_name, ?),
                    source_row_number = COALESCE(source_row_number, row_number),
                    source_filename = COALESCE(source_filename, ?)
                WHERE owner_user_id IS NULL""",
            (sheet_config["worksheet"], template_path.name),
        )
        connection.execute(
            f"""CREATE UNIQUE INDEX IF NOT EXISTS {sheet_config['key']}_quotation_source_row
                ON {sheet_config['table']}
                (source_upload_id, source_sheet_name, source_row_number)"""
        )

    if all(connection.execute(
        f"SELECT EXISTS (SELECT 1 FROM {sheet_config['table']})"
    ).fetchone()[0] for sheet_config in excel_analysis.QUOTATION_SHEETS):
        return
    if not template_path.is_file():
        logging.warning("Cotton quotation workbook is missing: %s", template_path)
        return

    workbook = load_workbook(template_path, read_only=True, data_only=True)
    try:
        for sheet_config in excel_analysis.QUOTATION_SHEETS:
            worksheet_name = sheet_config["worksheet"]
            if worksheet_name not in workbook.sheetnames:
                continue
            worksheet = workbook[worksheet_name]
            headers = next(
                worksheet.iter_rows(min_row=1, max_row=1, values_only=True),
                None,
            )
            columns = tuple(
                column for column, _header, _column_type in sheet_config["columns"]
            )
            field_by_header = {
                excel_analysis.normalize_cotton_quotation_header(header): column
                for column, header, _column_type in sheet_config["columns"]
            }
            header_indexes = {
                field_by_header[excel_analysis.normalize_cotton_quotation_header(header)]: index
                for index, header in enumerate(headers or ())
                if excel_analysis.normalize_cotton_quotation_header(header) in field_by_header
            }
            if "processor" not in header_indexes or "origin" not in header_indexes:
                logging.warning("Could not map quotation worksheet %s", worksheet_name)
                continue
            if connection.execute(
                f"SELECT EXISTS (SELECT 1 FROM {sheet_config['table']})"
            ).fetchone()[0]:
                continue
            stored_columns = (
                "row_number",
                *columns,
                "source_upload_id",
                "source_sheet_name",
                "source_row_number",
                "source_filename",
            )
            placeholders = ", ".join("?" for _ in stored_columns)
            insert_sql = (
                f"INSERT INTO {sheet_config['table']} "
                f"({', '.join(stored_columns)}) VALUES ({placeholders})"
            )
            records = []
            for row_number, row in enumerate(
                worksheet.iter_rows(min_row=2, values_only=True), start=1
            ):
                if all(value is None for value in row):
                    continue
                values = [
                    row[header_indexes[column]] if column in header_indexes else None
                    for column in columns
                ]
                if "product" in header_indexes:
                    values[columns.index("product")] = row[header_indexes["product"]]
                elif "product" in columns:
                    values[columns.index("product")] = sheet_config.get("product", "")
                batch_index = columns.index("batch_number") if "batch_number" in columns else None
                if batch_index is not None and values[batch_index] is not None:
                    values[batch_index] = str(values[batch_index])
                records.append(
                    (
                        row_number,
                        *values,
                        None,
                        worksheet_name,
                        row_number,
                        template_path.name,
                    )
                )
            connection.executemany(insert_sql, records)
    finally:
        workbook.close()


def reset_trade_data():
    quotation_tables = {
        sheet_config["table"] for sheet_config in excel_analysis.QUOTATION_SHEETS
    } | {"cotton_quotation_prices", "cotton_quotation_strength"}
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        for table in (
            "waybills",
            "order_related_documents",
            "order_documents",
            "orders",
            "uploads",
        ):
            connection.execute(f"DELETE FROM {table}")
        for table in quotation_tables:
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        for table in (
            "waybills",
            "order_related_documents",
            "order_documents",
            "orders",
            "uploads",
            *quotation_tables,
        ):
            connection.execute(
                "DELETE FROM sqlite_sequence WHERE name = ?", (table,)
            )
        initialize_cotton_quotations(connection)

def backfill_cotton_quotation_dates(connection):
    table = excel_analysis.COTTON_QUOTATION_SHEETS[0]["table"]
    connection.execute(
        f"""UPDATE {table}
            SET quotation_date = (
                SELECT date(uploaded_at) FROM uploads
                WHERE uploads.id = {table}.source_upload_id
            )
            WHERE quotation_date IS NULL
              AND EXISTS (
                  SELECT 1 FROM uploads
                  WHERE uploads.id = {table}.source_upload_id
                    AND date(uploaded_at) IS NOT NULL
              )"""
    )


def get_current_user(token_hash, now):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT users.id, users.username FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (token_hash, now),
        ).fetchone()


def create_session(token_hash, user_id, expires_at, now):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def get_user_auth_record(username):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            "SELECT id, username, password_salt, password_hash "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()


def create_user(username, salt, password_hash):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            "INSERT INTO users (username, password_salt, password_hash) "
            "VALUES (?, ?, ?)",
            (username, sqlite3.Binary(salt), sqlite3.Binary(password_hash)),
        )
        return cursor.lastrowid


def get_user_profile(user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute(
            """SELECT username, company_name, company_address,
                      foreign_bank_account, cny_bank_account
               FROM users WHERE id = ?""",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(zip(
        (
            "username",
            "company_name",
            "company_address",
            "foreign_bank_account",
            "cny_bank_account",
        ),
        row,
    ))


def update_user_profile(user_id, fields):
    columns = (
        "company_name",
        "company_address",
        "foreign_bank_account",
        "cny_bank_account",
    )
    if fields.keys() != set(columns):
        raise ValueError("Invalid profile fields")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            f"UPDATE users SET {', '.join(f'{column} = ?' for column in columns)} "
            "WHERE id = ?",
            (*(fields[column] for column in columns), user_id),
        )
        return cursor.rowcount


def update_user_password(user_id, salt, password_hash):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
            (sqlite3.Binary(salt), sqlite3.Binary(password_hash), user_id),
        )
        return cursor.rowcount


def list_suppliers(user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        rows = connection.execute(
            """SELECT id, short_name, company_name, company_address,
                      bank_account, created_at
               FROM suppliers
               WHERE user_id = ?
               ORDER BY short_name COLLATE NOCASE, id""",
            (user_id,),
        ).fetchall()
    return [
        dict(zip(
            (
                "id",
                "short_name",
                "company_name",
                "company_address",
                "bank_account",
                "created_at",
            ),
            row,
        ))
        for row in rows
    ]


def create_supplier(user_id, fields):
    expected = {
        "short_name",
        "company_name",
        "company_address",
        "bank_account",
    }
    if fields.keys() != expected:
        raise ValueError("Invalid supplier fields")
    if not all(fields[key].strip() for key in (
        "short_name", "company_name", "company_address"
    )):
        raise ValueError("Required supplier fields are missing")
    if any(len(value) > 2000 for value in fields.values()):
        raise ValueError("Supplier fields are too long")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            """INSERT INTO suppliers
               (user_id, short_name, company_name, company_address, bank_account)
               VALUES (?, ?, ?, ?, ?)""",
            (
                user_id,
                fields["short_name"].strip(),
                fields["company_name"].strip(),
                fields["company_address"].strip(),
                fields["bank_account"].strip() or None,
            ),
        )
        supplier_id = cursor.lastrowid
        row = connection.execute(
            """SELECT id, short_name, company_name, company_address,
                      bank_account, created_at
               FROM suppliers WHERE id = ? AND user_id = ?""",
            (supplier_id, user_id),
        ).fetchone()
    return dict(zip(
        (
            "id",
            "short_name",
            "company_name",
            "company_address",
            "bank_account",
            "created_at",
        ),
        row,
    ))


def supplier_belongs_to_user(user_id, supplier_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            "SELECT 1 FROM suppliers WHERE id = ? AND user_id = ?",
            (supplier_id, user_id),
        ).fetchone() is not None


def delete_session(token_hash):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
        )


def get_cotton_quotations(user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        sheets = {}
        for sheet_config in excel_analysis.QUOTATION_SHEETS:
            columns = (
                "row_number",
                *(
                    column
                    for column, _header, _column_type in sheet_config["columns"]
                ),
                "quotation_date",
                "source_filename",
                "source_sheet_name",
            )
            cursor = connection.execute(
                f"""SELECT {', '.join(f'q.{column}' for column in columns)},
                           CASE WHEN u.user_id = ? OR q.owner_user_id = ? THEN 1 ELSE 0 END,
                           EXISTS (
                               SELECT 1 FROM orders AS o
                               WHERE o.source_quotation_id = q.row_number
                                 AND o.source_quotation_product = ?
                           )
                    FROM {sheet_config["table"]} AS q
                    LEFT JOIN uploads AS u ON u.id = q.source_upload_id
                    ORDER BY q.quotation_date IS NULL, q.quotation_date DESC,
                             q.row_number DESC""",
                (user_id, user_id, {
                    "quotation_summary": "cotton",
                    "sesame": "sesame",
                    "peanut": "peanut",
                    "peanut_meal": "peanut_meal",
                }[sheet_config["key"]]),
            )
            sheets[sheet_config["key"]] = [
                {**dict(zip(columns, row[:-2])), "editable": bool(row[-2]), "ordered": bool(row[-1])}
                for row in cursor.fetchall()
            ]
        return sheets


def update_cotton_quotation(row_number, user_id, values):
    allowed = {
        column for column, _header, _kind
        in excel_analysis.COTTON_QUOTATION_SHEETS[0]["columns"]
    } | {"quotation_date"}
    if not values or not values.keys() <= allowed:
        raise ValueError("Invalid quotation fields")
    assignments = ", ".join(f"{column} = ?" for column in values)
    table = excel_analysis.COTTON_QUOTATION_SHEETS[0]["table"]
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            f"""UPDATE {table} SET {assignments}
                WHERE row_number = ? AND (owner_user_id = ? OR EXISTS (
                    SELECT 1 FROM uploads
                    WHERE uploads.id = {table}.source_upload_id AND uploads.user_id = ?
                ))""",
            (*values.values(), row_number, user_id, user_id),
        )
        return cursor.rowcount


def list_orders(user_id, order_id=None):
    quotation_columns = list(dict.fromkeys(
        column
        for config in excel_analysis.QUOTATION_SHEETS
        for column, _header, _kind in config["columns"]
    ))
    columns = (
        "id", "created_at", "source_quotation_id", "source_quotation_product",
        *quotation_columns,
        "quotation_date", "delivery_date", "source_filename", "source_sheet_name", "benchmark_source",
        "quantity_tons", "supplier_price_usd_per_ton", "supplier_quote_basis",
        "supplier_id", "intermediary_id", "pricing_inputs", "pricing_result",
    )
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        where = "user_id = ?" if order_id is None else "user_id = ? AND id = ?"
        params = (user_id,) if order_id is None else (user_id, order_id)
        rows = connection.execute(
            f"""SELECT {', '.join(f'o.{column}' for column in columns)},
                       s.short_name, s.company_name, s.company_address, s.bank_account,
                       i.short_name, i.company_name, i.company_address, i.bank_account
                FROM orders AS o
                LEFT JOIN suppliers AS s ON s.id = o.supplier_id
                LEFT JOIN suppliers AS i ON i.id = o.intermediary_id
                WHERE {'o.user_id = ?' if order_id is None else 'o.user_id = ? AND o.id = ?'}
                ORDER BY o.id DESC""",
            params,
        ).fetchall()
    orders = []
    for row in rows:
        order = dict(zip(
            (
                *columns,
                "supplier_short_name",
                "supplier_company_name",
                "supplier_address",
                "supplier_bank_account",
                "intermediary_short_name",
                "intermediary_company_name",
                "intermediary_address",
                "intermediary_bank_account",
            ),
            row,
        ))
        order["product_type"] = order["source_quotation_product"]
        order["pricing_inputs"] = json.loads(order["pricing_inputs"])
        order["pricing_result"] = json.loads(order["pricing_result"])
        orders.append(order)
    return orders


def list_order_documents(user_id, order_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        rows = connection.execute(
            """SELECT id, document_type, filename, content_type, size_bytes, created_at
               FROM order_documents
               WHERE user_id = ? AND order_id = ?
               ORDER BY id DESC""",
            (user_id, order_id),
        ).fetchall()
    return [
        dict(zip(
            ("id", "document_type", "filename", "content_type", "size_bytes", "created_at"),
            row,
        ))
        for row in rows
    ]


def list_order_related_documents(user_id, order_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        rows = connection.execute(
            """SELECT id, filename, content_type, size_bytes, category,
                      analysis_json, analysis_status, summary, recognized_text,
                      created_at
               FROM order_related_documents
               WHERE user_id = ? AND order_id = ?
               ORDER BY id DESC""",
            (user_id, order_id),
        ).fetchall()
    documents = []
    for row in rows:
        document = dict(zip(
            (
                "id",
                "filename",
                "content_type",
                "size_bytes",
                "category",
                "analysis_json",
                "analysis_status",
                "summary",
                "recognized_text",
                "created_at",
            ),
            row,
        ))
        try:
            document["analysis"] = (
                json.loads(document.pop("analysis_json"))
                if document["analysis_json"]
                else None
            )
        except json.JSONDecodeError:
            document["analysis"] = None
            document.pop("analysis_json", None)
        documents.append(document)
    return documents


WAYBILL_FIELDS = (
    "BL_NUMBER",
    "BL_NET_WEIGHT",
    "WEIGHT_UNIT",
    "DEPARTURE_DATE",
    "ARRIVAL_DATE",
    "PACK_NUMBER",
    "PACK_UNIT",
    "VOYAGE_NUMBER",
    "COO_INOVICE_NUMBER",
    "COO_INVOICE_DATE",
)


def upsert_waybill(user_id, order_id, related_document_id, fields):
    if fields.keys() - set(WAYBILL_FIELDS):
        raise ValueError("Invalid waybill fields")
    values = {field: str(fields.get(field) or "").strip() for field in WAYBILL_FIELDS}
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        owned = connection.execute(
            """SELECT o.product, o.origin
               FROM order_related_documents AS d
               JOIN orders AS o ON o.id = d.order_id
               WHERE d.id = ? AND d.order_id = ? AND d.user_id = ?""",
            (related_document_id, order_id, user_id),
        ).fetchone()
        if owned is None:
            return None
        existing = connection.execute(
            "SELECT id FROM waybills WHERE related_document_id = ?",
            (related_document_id,),
        ).fetchone()
        if existing:
            connection.execute(
                f"""UPDATE waybills
                    SET {', '.join(f'{field} = ?' for field in WAYBILL_FIELDS)},
                        product = ?, origin = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?""",
                (
                    *(values[field] for field in WAYBILL_FIELDS),
                    owned[0], owned[1], existing[0],
                ),
            )
            waybill_id = existing[0]
        else:
            cursor = connection.execute(
                f"""INSERT INTO waybills
                    (order_id, related_document_id, {', '.join(WAYBILL_FIELDS)},
                     product, origin)
                    VALUES (?, ?, {', '.join('?' for _ in WAYBILL_FIELDS)}, ?, ?)""",
                (
                    order_id,
                    related_document_id,
                    *(values[field] for field in WAYBILL_FIELDS),
                    owned[0], owned[1],
                ),
            )
            waybill_id = cursor.lastrowid
        return get_waybill(connection, waybill_id)


def get_waybill(connection, waybill_id):
    row = connection.execute(
        f"""SELECT id, related_document_id, product, origin, {', '.join(WAYBILL_FIELDS)},
                   created_at, updated_at
           FROM waybills WHERE id = ?""",
        (waybill_id,),
    ).fetchone()
    return dict(zip(
        (
            "id",
            "related_document_id",
            "product",
            "origin",
            *WAYBILL_FIELDS,
            "created_at",
            "updated_at",
        ),
        row,
    )) if row else None


def list_waybills(user_id, order_id=None):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        conditions = ["d.user_id = ?"]
        parameters = [user_id]
        if order_id is not None:
            conditions.append("w.order_id = ?")
            parameters.append(order_id)
        rows = connection.execute(
            f"""SELECT w.id, w.order_id, w.related_document_id,
                       o.product, o.origin,
                       {', '.join(f'w.{field}' for field in WAYBILL_FIELDS)},
                       w.created_at, w.updated_at
                FROM waybills AS w
                JOIN order_related_documents AS d
                  ON d.id = w.related_document_id
                JOIN orders AS o ON o.id = w.order_id
                WHERE {' AND '.join(conditions)}
                ORDER BY w.id DESC""",
            parameters,
        ).fetchall()
    return [
        dict(zip(
            (
                "id",
                "order_id",
                "related_document_id",
                "product",
                "origin",
                *WAYBILL_FIELDS,
                "created_at",
                "updated_at",
            ),
            row,
        ))
        for row in rows
    ]


def create_order_related_document(
    user_id, order_id, filename, content_type, content, analysis=None
):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            """INSERT INTO order_related_documents
               (order_id, user_id, filename, content_type, size_bytes,
                category, analysis_json, analysis_status, recognized_text, content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id,
                user_id,
                filename,
                content_type,
                len(content),
                analysis.get("category") if analysis else None,
                json.dumps(analysis, ensure_ascii=False, allow_nan=False)
                if analysis else None,
                "complete" if analysis else "pending",
                analysis.get("text") if analysis else None,
                sqlite3.Binary(content),
            ),
        )
        document_id = cursor.lastrowid
        row = connection.execute(
            """SELECT id, filename, content_type, size_bytes, category,
                      analysis_json, analysis_status, summary, recognized_text,
                      created_at
               FROM order_related_documents
               WHERE id = ? AND user_id = ? AND order_id = ?""",
            (document_id, user_id, order_id),
        ).fetchone()
    document = dict(zip(
        (
            "id",
            "filename",
            "content_type",
            "size_bytes",
            "category",
            "analysis_json",
            "analysis_status",
            "summary",
            "recognized_text",
            "created_at",
        ),
        row,
    ))
    raw_analysis = document.pop("analysis_json")
    document["analysis"] = json.loads(raw_analysis) if raw_analysis else None
    return document


def update_order_related_analysis(document_id, user_id, order_id, analysis):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            """UPDATE order_related_documents
               SET category = ?, analysis_json = ?, analysis_status = 'complete',
                   recognized_text = COALESCE(recognized_text, ?)
               WHERE id = ? AND user_id = ? AND order_id = ?""",
            (
                analysis.get("category"),
                json.dumps(analysis, ensure_ascii=False, allow_nan=False),
                analysis.get("text"),
                document_id,
                user_id,
                order_id,
            ),
        )
        return cursor.rowcount


def update_order_related_review(
    document_id, user_id, order_id, summary, recognized_text
):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            """UPDATE order_related_documents
               SET summary = ?, recognized_text = ?
               WHERE id = ? AND user_id = ? AND order_id = ?""",
            (summary, recognized_text, document_id, user_id, order_id),
        )
        return cursor.rowcount


def delete_order_related_document(user_id, order_id, document_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute(
            "DELETE FROM waybills WHERE related_document_id = ? AND order_id = ?",
            (document_id, order_id),
        )
        cursor = connection.execute(
            """DELETE FROM order_related_documents
               WHERE id = ? AND user_id = ? AND order_id = ?""",
            (document_id, user_id, order_id),
        )
        return cursor.rowcount


def get_order_related_document_bytes(user_id, order_id, document_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT filename, content_type, content
               FROM order_related_documents
               WHERE id = ? AND user_id = ? AND order_id = ?""",
            (document_id, user_id, order_id),
        ).fetchone()


def get_order_related_document(user_id, order_id, document_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT filename, content_type, content
               FROM order_related_documents
               WHERE id = ? AND user_id = ? AND order_id = ?""",
            (document_id, user_id, order_id),
        ).fetchone()


def get_order_document(user_id, order_id, document_type):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT filename, content_type, content
               FROM order_documents
               WHERE user_id = ? AND order_id = ? AND document_type = ?""",
            (user_id, order_id, document_type),
        ).fetchone()


def create_order_document(
    user_id, order_id, document_type, filename, content_type, content
):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT OR IGNORE INTO order_documents
               (user_id, order_id, document_type, filename, content_type, size_bytes, content)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                order_id,
                document_type,
                filename,
                content_type,
                len(content),
                content,
            ),
        )
        row = connection.execute(
            """SELECT id, document_type, filename, content_type, size_bytes, created_at
               FROM order_documents
               WHERE user_id = ? AND order_id = ? AND document_type = ?""",
            (user_id, order_id, document_type),
        ).fetchone()
    return dict(zip(
        ("id", "document_type", "filename", "content_type", "size_bytes", "created_at"),
        row,
    ))


def create_order_documents(user_id, order_id, documents):
    stored = []
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        for document_type, filename, content_type, content in documents:
            connection.execute(
                """INSERT OR IGNORE INTO order_documents
                   (user_id, order_id, document_type, filename, content_type,
                    size_bytes, content)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    order_id,
                    document_type,
                    filename,
                    content_type,
                    len(content),
                    content,
                ),
            )
        rows = connection.execute(
            """SELECT id, document_type, filename, content_type, size_bytes, created_at
               FROM order_documents
               WHERE user_id = ? AND order_id = ?""",
            (user_id, order_id),
        ).fetchall()
        stored = [
            dict(zip(
                ("id", "document_type", "filename", "content_type", "size_bytes", "created_at"),
                row,
            ))
            for row in rows
        ]
    return stored


def create_order(user_id, product, supplier_id, intermediary_id, quotation_id, quotation,
                 benchmark_source, delivery_date, pricing_inputs, pricing_result,
                 quantity_tons, supplier_price, quote_basis):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        quote_config = quotation_manager.quotation_config(product)
        if quote_config is None:
            raise ValueError("Invalid product")
        allowed = {
            column for column, _header, _kind in quote_config["columns"]
        } | {"quotation_date", "source_filename", "source_sheet_name"}
        if quotation.keys() != allowed:
            raise ValueError("Invalid order quotation fields")
        for party_id in (supplier_id, intermediary_id):
            if party_id is not None and not connection.execute(
                "SELECT 1 FROM suppliers WHERE id = ? AND user_id = ?",
                (party_id, user_id),
            ).fetchone():
                raise ValueError("Supplier not found")
        if supplier_id == intermediary_id:
            raise ValueError("Supplier and intermediary must differ")
        if quotation_id is not None:
            quote_columns = tuple(quotation)
            row = connection.execute(
                f"SELECT {', '.join(quote_columns)} FROM {quote_config['table']} WHERE row_number = ?",
                (quotation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Quotation not found")
            if connection.execute(
                """SELECT 1 FROM orders
                   WHERE source_quotation_id = ? AND source_quotation_product = ?""",
                (quotation_id, product),
            ).fetchone():
                raise QuotationAlreadyOrdered("Quotation already ordered")
            submitted_date = quotation["quotation_date"]
            quotation = dict(zip(quote_columns, row))
            quotation["quotation_date"] = quotation["quotation_date"] or submitted_date
            quotation_manager.validate_fields({"quotation_date": quotation["quotation_date"]})
            weight = next(
                (quotation[name] for name in ("net_weight_tons", "standard_weight_tons")
                 if isinstance(quotation[name], (int, float)) and quotation[name] > 0),
                None,
            )
            if weight is not None and quantity_tons != weight:
                raise ValueError("Quantity must match quotation weight")
        else:
            quotation["net_weight_tons"] = quantity_tons
            quote_columns = tuple(quotation)
            cursor = connection.execute(
                f"INSERT INTO {quote_config['table']} ({', '.join(quote_columns)}, owner_user_id) "
                f"VALUES ({', '.join('?' for _ in quote_columns)}, ?)",
                (*quotation.values(), user_id),
            )
            quotation_id = cursor.lastrowid
        columns = (
            "user_id", "source_quotation_id", "source_quotation_product",
            *quotation.keys(), "benchmark_source", "delivery_date",
            "supplier_id", "intermediary_id", "pricing_inputs", "pricing_result", "quantity_tons",
            "supplier_price_usd_per_ton", "supplier_quote_basis",
        )
        cursor = connection.execute(
            f"INSERT INTO orders ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            (
                user_id, quotation_id, product, *quotation.values(), benchmark_source, delivery_date,
                supplier_id, intermediary_id, pricing_inputs, pricing_result, quantity_tons,
                supplier_price, quote_basis,
            ),
        )
        return cursor.lastrowid


def update_order(user_id, order_id, product, supplier_id, intermediary_id, quotation,
                 benchmark_source, delivery_date, pricing_inputs, pricing_result,
                 quantity_tons, supplier_price, quote_basis):
    columns = (
        "source_quotation_product", *quotation.keys(), "benchmark_source", "delivery_date", "supplier_id", "intermediary_id", "pricing_inputs",
        "pricing_result", "quantity_tons", "supplier_price_usd_per_ton", "supplier_quote_basis",
    )
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            f"UPDATE orders SET {', '.join(f'{column} = ?' for column in columns)} "
            "WHERE id = ? AND user_id = ?",
            (product, *quotation.values(), benchmark_source, delivery_date, supplier_id, intermediary_id, pricing_inputs,
             pricing_result, quantity_tons, supplier_price, quote_basis, order_id, user_id),
        )
        purchase_contract_deleted = 0
        if cursor.rowcount:
            connection.execute(
                """UPDATE waybills
                   SET product = ?, origin = ?
                   WHERE order_id = ?""",
                (quotation.get("product"), quotation.get("origin"), order_id),
            )
            deletion = connection.execute(
                """DELETE FROM order_documents
                   WHERE order_id = ? AND user_id = ?
                     AND document_type IN (
                        'purchase_contract',
                        'supplier_intermediary_contract',
                        'intermediary_user_contract'
                     )""",
                (order_id, user_id),
            )
            purchase_contract_deleted = deletion.rowcount
        return {
            "updated": cursor.rowcount,
            "purchase_contract_deleted": purchase_contract_deleted,
        }


def delete_order(user_id, order_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            "DELETE FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
        )
        if cursor.rowcount:
            connection.execute(
                "DELETE FROM order_documents WHERE order_id = ? AND user_id = ?",
                (order_id, user_id),
            )
            connection.execute(
                "DELETE FROM order_related_documents "
                "WHERE order_id = ? AND user_id = ?",
                (order_id, user_id),
            )
            connection.execute(
                "DELETE FROM waybills WHERE order_id = ?",
                (order_id,),
            )
        return cursor.rowcount


class QuotationAlreadyOrdered(ValueError):
    pass


def create_upload(user_id, filename, content_type, file_bytes):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            """INSERT INTO uploads (user_id, filename, content_type, size_bytes, content)
               VALUES (?, ?, ?, ?, ?)""",
            (
                user_id,
                filename,
                content_type,
                len(file_bytes),
                sqlite3.Binary(file_bytes),
            ),
        )
        upload_id = cursor.lastrowid
        uploaded_at = connection.execute(
            "SELECT uploaded_at FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()[0]
        return upload_id, uploaded_at


def get_upload_content(upload_id, user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT filename, content_type, content FROM uploads
               WHERE id = ? AND user_id = ?""",
            (upload_id, user_id),
        ).fetchone()


def get_upload_for_ai(upload_id, user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT filename, content FROM uploads
               WHERE id = ? AND user_id = ?""",
            (upload_id, user_id),
        ).fetchone()


def save_ai_analysis(upload_id, user_id, analysis, model, analyzed_at):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            """UPDATE uploads
               SET ai_analysis = ?, ai_model = ?, ai_analyzed_at = ?
               WHERE id = ? AND user_id = ?""",
            (analysis, model, analyzed_at, upload_id, user_id),
        )
        return cursor.rowcount


def list_uploads(user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT id, filename, content_type, size_bytes, uploaded_at
               FROM uploads WHERE user_id = ?
               ORDER BY uploaded_at DESC, id DESC""",
            (user_id,),
        ).fetchall()


def get_upload_details(upload_id, user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        return connection.execute(
            """SELECT filename, content_type, size_bytes, uploaded_at, content,
                      ai_analysis, ai_model, ai_analyzed_at
               FROM uploads WHERE id = ? AND user_id = ?""",
            (upload_id, user_id),
        ).fetchone()


def import_uploaded_cotton_quotations(upload_id, filename, file_bytes):
    match = excel_analysis.extract_cotton_quotation_rows(file_bytes)
    if match is None:
        return None
    sheet_name, records = match
    table = excel_analysis.COTTON_QUOTATION_SHEETS[0]["table"]
    columns = tuple(
        column
        for column, _header, _column_type
        in excel_analysis.COTTON_QUOTATION_SHEETS[0]["columns"]
    )
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        existing_count = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE source_upload_id = ?",
            (upload_id,),
        ).fetchone()[0]
        if existing_count:
            return {
                "rows_found": len(records),
                "rows_inserted": 0,
                "already_imported": True,
            }

        upload = connection.execute(
            "SELECT date(uploaded_at) FROM uploads WHERE id = ?", (upload_id,)
        ).fetchone()
        if upload is None or upload[0] is None:
            raise sqlite3.IntegrityError("Upload date is missing or invalid")
        quotation_date = upload[0]
        first_row_number = connection.execute(
            f"SELECT COALESCE(MAX(row_number), 0) + 1 FROM {table}"
        ).fetchone()[0]
        stored_columns = (
            "row_number",
            *columns,
            "quotation_date",
            "source_upload_id",
            "source_sheet_name",
            "source_row_number",
            "source_filename",
        )
        placeholders = ", ".join("?" for _ in stored_columns)
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(stored_columns)}) "
            f"VALUES ({placeholders})"
        )
        connection.executemany(
            insert_sql,
            [
                (
                    first_row_number + index,
                    *(values[column] for column in columns),
                    quotation_date,
                    upload_id,
                    sheet_name,
                    source_row_number,
                    filename,
                )
                for index, (source_row_number, values) in enumerate(records)
            ],
        )
    return {
        "rows_found": len(records),
        "rows_inserted": len(records),
        "already_imported": False,
    }


def delete_upload(upload_id, user_id):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            "DELETE FROM uploads WHERE id = ? AND user_id = ?",
            (upload_id, user_id),
        )
        return cursor.rowcount
