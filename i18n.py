import json
from pathlib import Path


_CATALOG_PATH = Path(__file__).with_name("locales") / "messages.json"
with _CATALOG_PATH.open(encoding="utf-8") as catalog_file:
    _CATALOG = json.load(catalog_file)

SUPPORTED_LOCALES = ("zh-CN", "en")


def get_locale(accept_language="", preferred=None):
    if preferred in SUPPORTED_LOCALES:
        return preferred
    languages = []
    for language in accept_language.lower().split(","):
        parts = language.strip().split(";")
        language_code = parts[0].strip().replace("_", "-")
        quality = 1.0
        if len(parts) > 1 and parts[1].strip().startswith("q="):
            try:
                quality = float(parts[1].strip()[2:])
            except ValueError:
                quality = 0.0
        if language_code.startswith("zh"):
            languages.append((quality, "zh-CN"))
        elif language_code.startswith("en"):
            languages.append((quality, "en"))
    languages = [language for language in languages if language[0] > 0]
    if languages:
        return max(languages, key=lambda item: item[0])[1]
    return "zh-CN"


def translate(locale, key, **values):
    language = locale if locale in SUPPORTED_LOCALES else "zh-CN"
    return _CATALOG[language][key].format(**values)
