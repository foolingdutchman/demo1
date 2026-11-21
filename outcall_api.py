from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import logging
import math
import re
from urllib.error import URLError
from urllib.request import Request, urlopen


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
    "usd_cny": "CNY",
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


def fetch_public_html(url):
    request = Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": "Mozilla/5.0 (compatible; demo1-market-board/1.0)",
        },
    )
    with urlopen(request, timeout=PUBLIC_API_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def get_market_prices():
    try:
        gold = fetch_public_json(GOLD_PRICE_API_URL)
        crypto = fetch_public_json(CRYPTO_PRICES_API_URL)
        ice_cotton = fetch_public_json(ICE_COTTON_PRICE_API_URL)
        zce_cotton = fetch_public_text(ZCE_COTTON_PRICE_API_URL)
        boc_rates = fetch_public_html(BOC_EXCHANGE_RATES_API_URL)
        ice_result = ice_cotton["chart"]["result"][0]
        ice_price = float(ice_result["meta"]["regularMarketPrice"])
        cotton_quote = re.search(r'hq_str_nf_CF0="([^"]*)"', zce_cotton)
        if cotton_quote is None:
            raise ValueError("Public market API returned an invalid cotton quote")
        cotton_fields = cotton_quote.group(1).split(",")
        if len(cotton_fields) <= 6 or cotton_fields[0] != "棉花连续":
            raise ValueError("Public market API returned an invalid cotton quote")
        rate_parser = BankOfChinaRateParser()
        rate_parser.feed(boc_rates)
        if rate_parser.usd_cells is None or len(rate_parser.usd_cells) <= 3:
            raise ValueError("Bank of China USD exchange rate was not found")
        usd_cny_spot_sell = round(float(rate_parser.usd_cells[3]) / 100, 4)
        prices = {
            "gold": float(gold["price"]),
            "btc": float(crypto["bitcoin"]["usd"]),
            "eth": float(crypto["ethereum"]["usd"]),
            "ice_cotton": ice_price,
            "zce_cotton": float(cotton_fields[6]),
            "usd_cny": usd_cny_spot_sell,
        }
        if any(not math.isfinite(price) or price <= 0 for price in prices.values()):
            raise ValueError("Public market API returned an invalid price")
        import_cotton_price = round(
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
        if not math.isfinite(import_cotton_price) or import_cotton_price <= 0:
            raise ValueError("Calculated import cotton price is invalid")
    except (URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as error:
        logging.exception("Could not retrieve public market prices")
        raise MarketDataUnavailable from error

    return {
        "currency": "USD",
        "asset_currencies": {
            "ice_cotton": "US¢",
            "zce_cotton": "CNY",
            "usd_cny": "CNY",
        },
        "prices": prices,
        "derived_prices": {"import_cotton": import_cotton_price},
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
    if asset not in MARKET_CURRENCIES:
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
