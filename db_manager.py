from contextlib import closing
from pathlib import Path
import json
import logging
import sqlite3

from openpyxl import load_workbook

import excel_analysis
import quotation_manager


DATABASE_PATH = Path(__file__).with_name("uploads.db")
COTTON_QUOTATION_PATH = Path(__file__).with_name("cotton_quotation.xlsx.xlsx")
DatabaseError = sqlite3.Error


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
        initialize_orders(connection)
        initialize_order_documents(connection)
        initialize_suppliers(connection)


def initialize_orders(connection):
    quotation_columns = ", ".join(
        f"{column} {kind}"
        for column, _header, kind in excel_analysis.COTTON_QUOTATION_SHEETS[0]["columns"]
    )
    connection.execute(
        f"""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            source_quotation_id INTEGER,
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
    if "delivery_date" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN delivery_date TEXT")
    if "supplier_id" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN supplier_id INTEGER")


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


def initialize_cotton_quotations(connection):
    template_path = COTTON_QUOTATION_PATH
    connection.execute("DROP TABLE IF EXISTS cotton_quotation_prices")
    connection.execute("DROP TABLE IF EXISTS cotton_quotation_strength")
    for sheet_config in excel_analysis.COTTON_QUOTATION_SHEETS:
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

    summary_table = excel_analysis.COTTON_QUOTATION_SHEETS[0]["table"]
    table_columns = {
        row[1] for row in connection.execute(f"PRAGMA table_info({summary_table})")
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
            connection.execute(
                f"ALTER TABLE {summary_table} ADD COLUMN {column} {definition}"
            )
    backfill_cotton_quotation_dates(connection)
    connection.execute(
        f"""UPDATE {summary_table}
            SET source_sheet_name = COALESCE(source_sheet_name, ?),
                source_row_number = COALESCE(source_row_number, row_number),
                source_filename = COALESCE(source_filename, ?)
            WHERE owner_user_id IS NULL""",
        (excel_analysis.COTTON_QUOTATION_SHEETS[0]["worksheet"], template_path.name),
    )
    connection.execute(
        f"""CREATE UNIQUE INDEX IF NOT EXISTS cotton_quotation_source_row
            ON {summary_table}
            (source_upload_id, source_sheet_name, source_row_number)"""
    )

    if connection.execute(
        f"SELECT EXISTS (SELECT 1 FROM {summary_table})"
    ).fetchone()[0]:
        return
    if not template_path.is_file():
        logging.warning("Cotton quotation workbook is missing: %s", template_path)
        return

    workbook = load_workbook(template_path, read_only=True, data_only=True)
    try:
        for sheet_config in excel_analysis.COTTON_QUOTATION_SHEETS:
            worksheet_name = sheet_config["worksheet"]
            if worksheet_name not in workbook.sheetnames:
                raise ValueError(
                    f"Cotton quotation workbook is missing worksheet "
                    f"{worksheet_name!r}"
                )
            worksheet = workbook[worksheet_name]
            headers = next(
                worksheet.iter_rows(min_row=1, max_row=1, values_only=True),
                None,
            )
            expected_headers = tuple(
                header for _column, header, _column_type in sheet_config["columns"]
            )
            if headers != expected_headers:
                raise ValueError(
                    f"Unexpected headers in cotton quotation worksheet "
                    f"{worksheet_name!r}"
                )

            columns = tuple(
                column for column, _header, _column_type in sheet_config["columns"]
            )
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
                values = list(row[:len(columns)])
                if values[1] is not None:
                    values[1] = str(values[1])
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
        for sheet_config in excel_analysis.COTTON_QUOTATION_SHEETS:
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
                           EXISTS (SELECT 1 FROM orders AS o
                                   WHERE o.source_quotation_id = q.row_number)
                    FROM {sheet_config["table"]} AS q
                    LEFT JOIN uploads AS u ON u.id = q.source_upload_id
                    ORDER BY q.quotation_date IS NULL, q.quotation_date DESC,
                             q.row_number DESC""",
                (user_id, user_id),
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
    columns = (
        "id", "created_at", "source_quotation_id",
        *(column for column, _header, _kind in excel_analysis.COTTON_QUOTATION_SHEETS[0]["columns"]),
        "quotation_date", "delivery_date", "source_filename", "source_sheet_name", "benchmark_source",
        "quantity_tons", "supplier_price_usd_per_ton", "supplier_quote_basis",
        "supplier_id", "pricing_inputs", "pricing_result",
    )
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        where = "user_id = ?" if order_id is None else "user_id = ? AND id = ?"
        params = (user_id,) if order_id is None else (user_id, order_id)
        rows = connection.execute(
            f"""SELECT {', '.join(f'o.{column}' for column in columns)},
                       s.short_name, s.company_name, s.company_address, s.bank_account
                FROM orders AS o
                LEFT JOIN suppliers AS s ON s.id = o.supplier_id
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
            ),
            row,
        ))
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


def create_order(user_id, supplier_id, quotation_id, quotation, benchmark_source, delivery_date,
                 pricing_inputs, pricing_result, quantity_tons, supplier_price, quote_basis):
    allowed = {
        column for column, _header, _kind
        in excel_analysis.COTTON_QUOTATION_SHEETS[0]["columns"]
    } | {"quotation_date", "source_filename", "source_sheet_name"}
    if quotation.keys() != allowed:
        raise ValueError("Invalid order quotation fields")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        if supplier_id is not None and not connection.execute(
            "SELECT 1 FROM suppliers WHERE id = ? AND user_id = ?",
            (supplier_id, user_id),
        ).fetchone():
            raise ValueError("Supplier not found")
        if quotation_id is not None:
            quote_columns = tuple(quotation)
            row = connection.execute(
                f"SELECT {', '.join(quote_columns)} FROM cotton_quotation_summary WHERE row_number = ?",
                (quotation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Quotation not found")
            if connection.execute(
                "SELECT 1 FROM orders WHERE source_quotation_id = ?", (quotation_id,),
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
                f"INSERT INTO cotton_quotation_summary ({', '.join(quote_columns)}, owner_user_id) "
                f"VALUES ({', '.join('?' for _ in quote_columns)}, ?)",
                (*quotation.values(), user_id),
            )
            quotation_id = cursor.lastrowid
        columns = (
            "user_id", "source_quotation_id", *quotation.keys(), "benchmark_source", "delivery_date",
            "supplier_id", "pricing_inputs", "pricing_result", "quantity_tons",
            "supplier_price_usd_per_ton", "supplier_quote_basis",
        )
        cursor = connection.execute(
            f"INSERT INTO orders ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            (
                user_id, quotation_id, *quotation.values(), benchmark_source, delivery_date,
                supplier_id, pricing_inputs, pricing_result, quantity_tons, supplier_price,
                quote_basis,
            ),
        )
        return cursor.lastrowid


def update_order(user_id, order_id, supplier_id, quotation, benchmark_source, delivery_date,
                 pricing_inputs, pricing_result, quantity_tons, supplier_price, quote_basis):
    columns = (
        *quotation.keys(), "benchmark_source", "delivery_date", "supplier_id", "pricing_inputs",
        "pricing_result", "quantity_tons", "supplier_price_usd_per_ton", "supplier_quote_basis",
    )
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.execute(
            f"UPDATE orders SET {', '.join(f'{column} = ?' for column in columns)} "
            "WHERE id = ? AND user_id = ?",
            (*quotation.values(), benchmark_source, delivery_date, supplier_id, pricing_inputs,
             pricing_result, quantity_tons, supplier_price, quote_basis, order_id, user_id),
        )
        purchase_contract_deleted = 0
        if cursor.rowcount:
            deletion = connection.execute(
                """DELETE FROM order_documents
                   WHERE order_id = ? AND user_id = ?
                     AND document_type = 'purchase_contract'""",
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
