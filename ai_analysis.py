import base64
import codecs
import json
import logging
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from excel_analysis import EXCEL_EXTENSIONS, ExcelAnalysisError, extract_excel_text
from i18n import translate
from image_analysis import IMAGE_MIME_TYPES, is_image_file
from pdf_analysis import PDF_EXTENSIONS, PdfAnalysisError, extract_pdf_text


TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json"}
AI_SUPPORTED_IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
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


def load_ai_configuration():
    global AI_API_BASE_URL
    global AI_API_KEY
    global AI_MODEL
    global AI_MAX_INPUT_CHARS
    global AI_API_TIMEOUT_SECONDS

    AI_API_BASE_URL = os.environ.get(
        "AI_API_BASE_URL", "https://api.minimax.cn/v1"
    ).strip().rstrip("/")
    AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
    AI_MODEL = os.environ.get("AI_MODEL", "MiniMax-M3.1-Flash").strip()
    AI_MAX_INPUT_CHARS = int(os.environ.get("AI_MAX_INPUT_CHARS", "60000"))
    AI_API_TIMEOUT_SECONDS = int(os.environ.get("AI_API_TIMEOUT_SECONDS", "60"))


def decode_text_file(file_bytes):
    if file_bytes.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif file_bytes.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    try:
        return file_bytes.decode(encoding)
    except UnicodeDecodeError:
        return None


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


load_ai_configuration()
