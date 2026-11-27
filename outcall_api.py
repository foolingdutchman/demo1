from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import json
import logging
import math
import re
from urllib.error import URLError
from urllib.request import Request, urlopen

import db_manager
import products_pricing


GOLD_PRICE_API_URL = "https://api.gold-api.com/price/XAU"
CRYPTO_PRICES_API_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin%2Cethereum&vs_currencies=usd"
)
ICE_COTTON_PRICE_API_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/CT%3DF"
    "?range=1d&interval=1m"
)
ZCE_COTTON_PRICE_API_URL = "https://hq.sinajs.cn/list=nf_CF0"
CC_INDEX_PRICES_URL = "https://www.cottonchina.org.cn/"
SESAME_PRICE_URL = "https://www.baiinfo.com/youliao/zhima"
PEANUT_PRICE_URL = "https://www.baiinfo.com/youliao/huasheng"
PEANUT_MEAL_PRICE_URL = "https://www.baiinfo.com/youliao/huashengpo"
BOC_EXCHANGE_RATES_API_URL = "https://www.boc.cn/sourcedb/whpj/"
SINA_REFERER_URL = "https://finance.sina.com.cn/"
ZCE_COTTON_HISTORY_API_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
    "var%20_gvHistoryData/InnerFuturesNewService.getDailyKLine"
    "?symbol=CF0"
)
YAHOO_HISTORY_API_URL_TEMPLATE = (
    "https://query1.finance.yahoo.com/v8/finance/chart/"
    "{symbol}?range=1mo&interval=1d"
)
YAHOO_HISTORY_SYMBOLS = {
    "gold": "GC%3DF",
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "ice_cotton": "CT%3DF",
    "usd_cny": "CNY%3DX",
}
PUBLIC_API_TIMEOUT_SECONDS = 8
COTTON_CUSTOMS_COST_CNY = 100
IMPORT_VAT_FACTOR = 1.09
US_CENTS_PER_METRIC_TON_CONVERSION = 22.0462
COTTON_CLEARANCE_COST_US_CENTS = 1.5
COTTON_TARIFF_AMOUNT_CNY = 3800
MARKET_CURRENCIES = {
    "gold": "USD",
    "btc": "USD",
    "eth": "USD",
    "ice_cotton": "US¢",
    "zce_cotton": "CNY",
    "cc_index_3128b": "CNY",
    "cc_index_2227b": "CNY",
    "cc_index_2129b": "CNY",
    "usd_cny": "CNY",
    "sesame": "CNY",
    "peanut": "CNY",
    "peanut_meal": "CNY",
}
MARKET_HISTORY_ASSETS = set(YAHOO_HISTORY_SYMBOLS) | {"zce_cotton"}
CC_INDEX_ASSETS = {
    "CC Index 3128B": "cc_index_3128b",
    "CC Index 2227B": "cc_index_2227b",
    "CC Index 2129B": "cc_index_2129b",
}


class MarketDataUnavailable(Exception):
    pass


class InvalidMarketAsset(Exception):
    pass


class BankOfChinaRateParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._is_usd_row = False
        self._in_cell = False
        self._cell_parts = []
        self.usd_cells = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._is_usd_row = dict(attrs).get("data-currency") == "美元"
            if self._is_usd_row:
                self.usd_cells = []
        elif tag == "td" and self._is_usd_row:
            self._in_cell = True
            self._cell_parts = []

    def handle_data(self, data):
        if self._in_cell:
            self._cell_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._in_cell:
            self.usd_cells.append("".join(self._cell_parts).strip())
            self._in_cell = False
        elif tag == "tr":
            self._is_usd_row = False


def fetch_public_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "demo1-market-board/1.0",
        },
    )
    with urlopen(request, timeout=PUBLIC_API_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_public_text(url):
    request = Request(
        url,
        headers={
            "Accept": "text/plain",
            "User-Agent": "Mozilla/5.0 (compatible; demo1-market-board/1.0)",
            "Referer": SINA_REFERER_URL,
        },
    )
    with urlopen(request, timeout=PUBLIC_API_TIMEOUT_SECONDS) as response:
        return response.read().decode("gbk")


def fetch_public_html(url, encoding="utf-8"):
    request = Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "Mozilla/5.0 (compatible; demo1-market-board/1.0)",
        },
    )
    with urlopen(request, timeout=PUBLIC_API_TIMEOUT_SECONDS) as response:
        return response.read().decode(encoding)


def parse_cc_index_prices(page_html):
    quotes = {}
    blocks = re.finditer(
        r"<div\s+class=['\"]nd_cotton_block(?:\s+mgr0)?['\"]>"
        r"(.*?)(?=<div\s+class=['\"]nd_cotton_block|\Z)",
        page_html,
        re.I | re.S,
    )
    for match in blocks:
        block = match.group(1)
        name_match = re.search(
            r"<div\s+class=['\"]nd_block_t['\"]>(.*?)</div>",
            block,
            re.I | re.S,
        )
        if name_match is None:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", name_match.group(1))).strip()
        asset = CC_INDEX_ASSETS.get(name)
        if asset is None:
            continue
        date_match = re.search(
            r"<div\s+class=['\"]nd_block_d['\"]>(.*?)</div>",
            block,
            re.I | re.S,
        )
        price_match = re.search(
            r"<div\s+class=['\"]nd_block_p['\"]>\s*<span[^>]*>"
            r"\s*([\d,]+(?:\.\d+)?)",
            block,
            re.I | re.S,
        )
        if date_match is None or price_match is None:
            raise ValueError(f"CC Index quote is missing for {name}")
        quote_date = html.unescape(
            re.sub(r"<[^>]+>", "", date_match.group(1))
        ).strip()
        datetime.strptime(quote_date, "%Y-%m-%d")
        price = float(price_match.group(1).replace(",", ""))
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"CC Index quote is invalid for {name}")
        quotes[asset] = {"price": price, "date": quote_date}

    if set(quotes) != set(CC_INDEX_ASSETS.values()):
        raise ValueError("CC Index quotes were not all returned")
    return quotes


def get_cc_index_prices():
    fetched_date = datetime.now().date().isoformat()
    quotes = {}
    missing = []
    for asset in CC_INDEX_ASSETS.values():
        try:
            quote = db_manager.get_daily_product_price(asset, fetched_date)
        except db_manager.DatabaseError as error:
            logging.warning(
                "Could not read %s daily price cache: %s",
                asset,
                type(error).__name__,
            )
            quote = None
        if quote is None:
            missing.append(asset)
        else:
            quotes[asset] = quote
    if not missing:
        return quotes

    fetched = parse_cc_index_prices(
        fetch_public_html(CC_INDEX_PRICES_URL, encoding="gbk")
    )
    for asset, quote in fetched.items():
        quotes[asset] = quote
        try:
            db_manager.save_daily_product_price(
                asset,
                fetched_date,
                quote["date"],
                quote["price"],
                "CNY",
            )
        except db_manager.DatabaseError as error:
            logging.warning(
                "Could not save %s daily price cache: %s",
                asset,
                type(error).__name__,
            )
    return quotes


def parse_sesame_price(page_html, today=None):
    text = html.unescape(page_html)
    match = re.search(
        r"(\d{1,2})月(\d{1,2})日.{0,80}?国产芝麻均价为\s*"
        r"([\d,]+(?:\.\d+)?)\s*元/吨",
        text,
        re.S,
    )
    if match is None:
        raise ValueError("Baiinfo sesame quote was not found")
    price = float(match.group(3).replace(",", ""))
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Baiinfo sesame quote is invalid")
    today = today or datetime.now().date()
    month, day = map(int, match.group(1, 2))
    year = today.year
    quote_date = datetime(year, month, day).date()
    if quote_date > today and (quote_date - today).days > 7:
        quote_date = datetime(year - 1, month, day).date()
    return {"price": price, "date": quote_date.isoformat()}


def get_sesame_price():
    return get_cached_product_price(
        "sesame",
        lambda: parse_sesame_price(fetch_public_html(SESAME_PRICE_URL)),
    )


def parse_peanut_price(page_html, today=None):
    text = html.unescape(page_html)
    match = re.search(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日.{0,120}?"
        r"(?:国内|国产)花生(?:通货[米籽]?)?均价(?:为)?\s*"
        r"([\d,]+(?:\.\d+)?)\s*元/吨",
        text,
        re.S,
    )
    if match is None:
        raise ValueError("Baiinfo peanut quote was not found")
    price = float(match.group(4).replace(",", ""))
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Baiinfo peanut quote is invalid")
    today = today or datetime.now().date()
    year = int(match.group(1)) if match.group(1) else today.year
    month, day = map(int, match.group(2, 3))
    quote_date = datetime(year, month, day).date()
    if not match.group(1) and quote_date > today and (quote_date - today).days > 7:
        quote_date = datetime(year - 1, month, day).date()
    return {"price": price, "date": quote_date.isoformat()}


def get_peanut_price():
    return get_cached_product_price(
        "peanut",
        lambda: parse_peanut_price(fetch_public_html(PEANUT_PRICE_URL)),
    )


def parse_peanut_meal_price(page_html, today=None):
    text = html.unescape(page_html)
    match = re.search(
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日.{0,80}?"
        r"花生粕市场主流参考价格为\s*([\d,]+(?:\.\d+)?)\s*元/吨",
        text,
        re.S,
    )
    if match is None:
        raise ValueError("Baiinfo peanut meal quote was not found")
    price = float(match.group(4).replace(",", ""))
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Baiinfo peanut meal quote is invalid")
    today = today or datetime.now().date()
    year = int(match.group(1)) if match.group(1) else today.year
    month, day = map(int, match.group(2, 3))
    quote_date = datetime(year, month, day).date()
    if not match.group(1) and quote_date > today and (quote_date - today).days > 7:
        quote_date = datetime(year - 1, month, day).date()
    return {"price": price, "date": quote_date.isoformat()}


def get_peanut_meal_price():
    return get_cached_product_price(
        "peanut_meal",
        lambda: parse_peanut_meal_price(fetch_public_html(PEANUT_MEAL_PRICE_URL)),
    )


def get_cached_product_price(product, fetcher):
    fetched_date = datetime.now().date().isoformat()
    try:
        cached = db_manager.get_daily_product_price(product, fetched_date)
    except db_manager.DatabaseError as error:
        logging.warning("Could not read %s daily price cache: %s", product, type(error).__name__)
        cached = None
    if cached is not None:
        return cached
    quote = fetcher()
    try:
        db_manager.save_daily_product_price(
            product,
            fetched_date,
            quote["date"],
            quote["price"],
            "CNY",
        )
    except db_manager.DatabaseError as error:
        logging.warning("Could not save %s daily price cache: %s", product, type(error).__name__)
    return quote


def get_market_prices():
    prices = {}
    primary_failures = 0
    try:
        gold = fetch_public_json(GOLD_PRICE_API_URL)
        prices["gold"] = float(gold["price"])
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        primary_failures += 1
        logging.warning("Could not retrieve gold price: %s", type(error).__name__)
    try:
        crypto = fetch_public_json(CRYPTO_PRICES_API_URL)
        prices["btc"] = float(crypto["bitcoin"]["usd"])
        prices["eth"] = float(crypto["ethereum"]["usd"])
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        primary_failures += 1
        logging.warning("Could not retrieve crypto prices: %s", type(error).__name__)
    try:
        ice_cotton = fetch_public_json(ICE_COTTON_PRICE_API_URL)
        prices["ice_cotton"] = float(
            ice_cotton["chart"]["result"][0]["meta"]["regularMarketPrice"]
        )
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        primary_failures += 1
        logging.warning("Could not retrieve ICE cotton price: %s", type(error).__name__)
    try:
        zce_cotton = fetch_public_text(ZCE_COTTON_PRICE_API_URL)
        cotton_quote = re.search(r'hq_str_nf_CF0="([^"]*)"', zce_cotton)
        if cotton_quote is None:
            raise ValueError("Public market API returned an invalid cotton quote")
        cotton_fields = cotton_quote.group(1).split(",")
        if len(cotton_fields) <= 6 or cotton_fields[0] != "棉花连续":
            raise ValueError("Public market API returned an invalid cotton quote")
        prices["zce_cotton"] = float(cotton_fields[6])
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        primary_failures += 1
        logging.warning("Could not retrieve ZCE cotton price: %s", type(error).__name__)
    try:
        boc_rates = fetch_public_html(BOC_EXCHANGE_RATES_API_URL)
        rate_parser = BankOfChinaRateParser()
        rate_parser.feed(boc_rates)
        if rate_parser.usd_cells is None or len(rate_parser.usd_cells) <= 3:
            raise ValueError("Bank of China USD exchange rate was not found")
        prices["usd_cny"] = round(float(rate_parser.usd_cells[3]) / 100, 4)
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        primary_failures += 1
        logging.warning("Could not retrieve USD/CNY rate: %s", type(error).__name__)
    try:
        cc_index_quotes = {}
        try:
            cc_index_quotes = get_cc_index_prices()
        except (
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            logging.warning(
                "Could not retrieve CC Index prices: %s",
                type(error).__name__,
            )
        sesame_quote = None
        try:
            sesame_quote = get_sesame_price()
        except (
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            logging.warning(
                "Could not retrieve sesame price: %s",
                type(error).__name__,
            )
        peanut_quote = None
        try:
            peanut_quote = get_peanut_price()
        except (
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            logging.warning(
                "Could not retrieve peanut price: %s",
                type(error).__name__,
            )
        peanut_meal_quote = None
        try:
            peanut_meal_quote = get_peanut_meal_price()
        except (
            URLError,
            TimeoutError,
            OSError,
            ValueError,
            TypeError,
        ) as error:
            logging.warning(
                "Could not retrieve peanut meal price: %s",
                type(error).__name__,
            )
        asset_dates = {}
        for asset, quote in cc_index_quotes.items():
            prices[asset] = quote["price"]
            asset_dates[asset] = quote["date"]
        if sesame_quote is not None:
            prices["sesame"] = sesame_quote["price"]
            asset_dates["sesame"] = sesame_quote["date"]
        if peanut_quote is not None:
            prices["peanut"] = peanut_quote["price"]
            asset_dates["peanut"] = peanut_quote["date"]
        if peanut_meal_quote is not None:
            prices["peanut_meal"] = peanut_meal_quote["price"]
            asset_dates["peanut_meal"] = peanut_meal_quote["date"]
        if primary_failures == 5 and len(prices) == 0:
            raise MarketDataUnavailable
        if any(not math.isfinite(price) or price <= 0 for price in prices.values()):
            raise ValueError("Public market API returned an invalid price")
        import_cotton_price = None
        if "zce_cotton" in prices and "usd_cny" in prices:
            candidate = round(
                (prices["zce_cotton"] - COTTON_CUSTOMS_COST_CNY)
                / IMPORT_VAT_FACTOR
                / prices["usd_cny"]
                / US_CENTS_PER_METRIC_TON_CONVERSION
                - COTTON_TARIFF_AMOUNT_CNY
                / prices["usd_cny"]
                / US_CENTS_PER_METRIC_TON_CONVERSION
                - COTTON_CLEARANCE_COST_US_CENTS,
                2,
            )
            if math.isfinite(candidate) and candidate > 0:
                import_cotton_price = candidate
    except (ValueError, KeyError, TypeError) as error:
        logging.exception("Could not retrieve public market prices")
        raise MarketDataUnavailable from error

    derived_prices = {
        "import_cotton": import_cotton_price,
    }
    for product, vat_factor in (
        ("sesame", 1.09),
        ("peanut", 1.09),
        ("peanut_meal", 1),
    ):
        import_price = None
        if product in prices and "usd_cny" in prices:
            import_price = products_pricing.calculate_same_cost_import_price(
                prices[product],
                prices["usd_cny"],
                vat_factor=vat_factor,
            )
        derived_prices[f"import_{product}"] = (
            round(import_price, 2) if import_price is not None else None
        )

    return {
        "currency": "USD",
        "asset_currencies": {
            "ice_cotton": "US¢",
            "zce_cotton": "CNY",
            "cc_index_3128b": "CNY",
            "cc_index_2227b": "CNY",
            "cc_index_2129b": "CNY",
            "usd_cny": "CNY",
            "sesame": "CNY",
            "peanut": "CNY",
            "peanut_meal": "CNY",
        },
        "asset_dates": asset_dates,
        "prices": prices,
        "derived_prices": derived_prices,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def market_candle(date, open_price, high, low, close):
    try:
        open_price, high, low, close = map(float, (open_price, high, low, close))
    except (TypeError, ValueError):
        return None
    prices = (open_price, high, low, close)
    if (
        not date
        or any(not math.isfinite(price) or price <= 0 for price in prices)
        or high < max(open_price, close)
        or low > min(open_price, close)
        or high < low
    ):
        return None
    return {
        "date": date,
        "price": close,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def yahoo_history_points(payload):
    chart = payload["chart"]["result"][0]
    quote = chart["indicators"]["quote"][0]
    points = []
    for timestamp, open_price, high, low, close in zip(
        chart["timestamp"],
        quote["open"],
        quote["high"],
        quote["low"],
        quote["close"],
    ):
        date = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        point = market_candle(date, open_price, high, low, close)
        if point:
            points.append(point)
    if not points:
        raise ValueError("Historical prices were not returned")
    return points


def get_market_history(asset):
    if asset not in MARKET_HISTORY_ASSETS:
        raise InvalidMarketAsset

    try:
        if asset == "zce_cotton":
            response = fetch_public_text(ZCE_COTTON_HISTORY_API_URL)
            match = re.search(r"_gvHistoryData\((\[.*\])\)", response, re.S)
            if match is None:
                raise ValueError("Historical cotton prices were not returned")
            history = json.loads(match.group(1))
            points = []
            for item in history[-30:]:
                point = market_candle(
                    item.get("d"),
                    item.get("o"),
                    item.get("h"),
                    item.get("l"),
                    item.get("c"),
                )
                if point:
                    points.append(point)
        else:
            payload = fetch_public_json(
                YAHOO_HISTORY_API_URL_TEMPLATE.format(
                    symbol=YAHOO_HISTORY_SYMBOLS[asset]
                )
            )
            points = yahoo_history_points(payload)
        if not points:
            raise ValueError("Historical prices were not returned")
    except (
        URLError,
        TimeoutError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
    ) as error:
        logging.exception("Could not retrieve market history for %s", asset)
        raise MarketDataUnavailable from error

    return {
        "asset": asset,
        "currency": MARKET_CURRENCIES[asset],
        "prices": points,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
