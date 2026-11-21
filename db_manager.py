from contextlib import closing
from pathlib import Path
import logging
import sqlite3

from openpyxl import load_workbook

import excel_analysis


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
        "source_upload_id": "INTEGER",
        "source_sheet_name": "TEXT",
        "source_row_number": "INTEGER",
        "source_filename": "TEXT",
    }
    for column, definition in source_migrations.items():
        if column not in table_columns:
            connection.execute(
                f"ALTER TABLE {summary_table} ADD COLUMN {column} {definition}"
            )
    connection.execute(
        f"""UPDATE {summary_table}
            SET source_sheet_name = COALESCE(source_sheet_name, ?),
                source_row_number = COALESCE(source_row_number, row_number),
                source_filename = COALESCE(source_filename, ?)""",
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


def delete_session(token_hash):
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        connection.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (token_hash,)
        )


def get_cotton_quotations():
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        sheets = {}
        for sheet_config in excel_analysis.COTTON_QUOTATION_SHEETS:
            columns = (
                *(
                    column
                    for column, _header, _column_type in sheet_config["columns"]
                ),
                "source_filename",
                "source_sheet_name",
            )
            cursor = connection.execute(
                f"""SELECT {', '.join(columns)}
                    FROM {sheet_config["table"]}
                    ORDER BY row_number"""
            )
            sheets[sheet_config["key"]] = [
                dict(zip(columns, row)) for row in cursor.fetchall()
            ]
        return sheets


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

        first_row_number = connection.execute(
            f"SELECT COALESCE(MAX(row_number), 0) + 1 FROM {table}"
        ).fetchone()[0]
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
            f"INSERT INTO {table} ({', '.join(stored_columns)}) "
            f"VALUES ({placeholders})"
        )
        connection.executemany(
            insert_sql,
            [
                (
                    first_row_number + index,
                    *(values[column] for column in columns),
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
