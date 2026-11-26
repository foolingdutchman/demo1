const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const html = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "assets.html"),
	"utf8",
);

test("assets separates cotton from financial prices and lists trade products", () => {
	const market = html.match(
		/<section class="market"[\s\S]*?<\/section>/,
	)?.[0] || "";
	const trade = html.match(
		/<section class="trade-products"[\s\S]*?<\/section>/,
	)?.[0] || "";

	assert.match(market, /data-market-value="ice_cotton"/);
	assert.match(market, /data-market-value="cc_index_3128b"/);
	assert.doesNotMatch(market, /data-market-value="zce_cotton"/);
	assert.match(trade, /data-market-value="zce_cotton"/);
	assert.match(trade, /href="\/product\/cotton\?return=assets"/);
	assert.match(trade, /data-i18n="trade\.sesame"/);
	assert.match(trade, /data-i18n="trade\.peanut"/);
	assert.match(trade, /data-i18n="trade\.peanut_meal"/);
	for (const product of ["sesame", "peanut", "peanut_meal"]) {
		assert.match(
			trade,
			new RegExp(`href="/product\\.html\\?product=${product}&amp;return=assets"`),
		);
	}
	assert.ok(
		html.indexOf('id="market-title"') < html.indexOf('id="trade-products-title"'),
	);
});
