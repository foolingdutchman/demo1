const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "app.js"),
	"utf8",
);

const html = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "assets.html"),
	"utf8",
);
const publicHtml = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "index.html"),
	"utf8",
);
const productDetailHtml = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "product.html"),
	"utf8",
);
const financialDetailHtml = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "financial_assets_marketing_detail.html"),
	"utf8",
);
const messages = JSON.parse(fs.readFileSync(
	path.join(__dirname, "..", "locales", "messages.json"),
	"utf8",
));

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
	assert.match(trade, /data-market-value="sesame"/);
	assert.match(trade, /data-i18n="trade\.peanut"/);
	assert.match(trade, /data-market-value="peanut"/);
	assert.match(trade, /data-i18n="trade\.peanut_meal"/);
	assert.match(trade, /data-market-value="peanut_meal"/);
	for (const product of ["sesame", "peanut", "peanut_meal"]) {
		assert.match(
			trade,
			new RegExp(`href="/product\\.html\\?product=${product}&amp;return=assets"`),
		);
		assert.match(trade, new RegExp(`data-market-derived="import_${product}"`));
		assert.match(publicHtml, new RegExp(`data-market-derived="import_${product}"`));
	}
	assert.match(productDetailHtml, /id="market-import-product-panel"/);
	assert.match(productDetailHtml, /id="market-import-product-customs-cost"/);
	assert.ok(
		html.indexOf('id="market-title"') < html.indexOf('id="trade-products-title"'),
	);
});

test("product purchase forms use the live benchmark and exclude source and quota inputs", () => {
	for (const page of [productDetailHtml, financialDetailHtml]) {
		const panel = page.match(
			/<section class="derived-panel" id="product-pricing-panel"[\s\S]*?<\/section>/,
		)?.[0] || "";
		assert.ok(panel);
		assert.match(panel, /data-product-pricing-field="domestic_benchmark_cny_per_ton"[^>]*readonly/);
		assert.doesNotMatch(panel, /sudan-benchmark-source|quota_cost/);
	}
	for (const locale of ["zh-CN", "en"]) {
		for (const key of [
			"market.product_pricing_title",
			"market.product_pricing_intro",
			"market.product_pricing_vat_label",
			"market.product_pricing_vat_rate",
			"market.product_dual_price",
			"market.product_pricing_no_positive_cif",
			"market.product_pricing_invalid_input",
			"market.product_pricing_unavailable",
			"market.product_pricing_risks",
		]) {
			assert.ok(messages[locale][key], `${locale} missing ${key}`);
		}
	}
});

test("public home mirrors market and trade product sections", () => {
	for (const page of [html, publicHtml]) {
		assert.match(page, /data-market-value="gold"/);
		assert.match(page, /data-market-value="cc_index_3128b"/);
		assert.doesNotMatch(
			page.match(/<section class="market"[\s\S]*?<\/section>/)?.[0] || "",
			/data-market-value="zce_cotton"/,
		);
		assert.match(page, /data-market-value="zce_cotton"/);
		assert.match(page, /data-market-value="sesame"/);
		assert.match(page, /data-market-value="peanut"/);
		assert.match(page, /data-market-value="peanut_meal"/);
	}
	assert.match(publicHtml, /<title>贸易通<\/title>/);
	assert.equal(
		(publicHtml.match(/data-login-required/g) || []).length,
		12,
	);
});

test("home market board renders equivalent import prices from market data", async () => {
	const products = ["sesame", "peanut", "peanut_meal"];
	const values = new Map();
	const dates = new Map();
	const elements = new Map();
	for (const product of products) {
		values.set(product, { dataset: { marketValue: product }, textContent: "" });
		values.set(`import_${product}`, {
			dataset: { marketDerived: `import_${product}` },
			textContent: "",
		});
		dates.set(product, { dataset: { marketDate: product }, textContent: "" });
	}
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		querySelectorAll(selector) {
			if (selector === "[data-market-value], [data-market-derived]") {
				return [...values.values()];
			}
			if (selector === "[data-market-date]") return [...dates.values()];
			if (selector === "[data-market-value]") {
				return [...values.values()].filter((value) => value.dataset.marketValue);
			}
			if (selector === '[data-market-derived="import_cotton"]') return [];
			return [];
		},
		querySelector(selector) {
			const match = selector.match(/data-market-(value|date|derived)="([^"]+)"/);
			if (!match) return null;
			if (match[1] === "date") return dates.get(match[2]);
			return values.get(match[2]);
		},
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, {});
			return elements.get(id);
		},
	};
	const context = vm.createContext({
		document,
		Intl,
		App: {
			t(key) { return key; },
			async api(url) {
				assert.equal(url, "/api/market-prices");
				return {
					prices: {
						sesame: 14118,
						peanut: 7971,
						peanut_meal: 3150,
						usd_cny: 6.6911,
					},
					derived_prices: {
						import_sesame: (14118 - 250) / 1.09 / 6.6911,
						import_peanut: (7971 - 250) / 1.09 / 6.6911,
						import_peanut_meal: (3150 - 250) / 6.6911,
					},
					updated_at: "2026-11-27T00:00:00Z",
				};
			},
		},
	});
	vm.runInContext(app, context);
	await vm.runInContext("loadMarketPrices()", context);

	assert.deepEqual(
		products.map((product) => values.get(`import_${product}`).textContent),
		[
			"USD 1,901.47",
			"USD 1,058.64",
			"USD 433.41",
		],
	);
});

test("public links require login and preserve the requested destination", async () => {
	const link = {
		href: "/product/cotton?return=index",
		events: {},
		getAttribute(name) { return name === "href" ? this.href : null; },
		addEventListener(name, callback) { this.events[name] = callback; },
	};
	let authenticated = false;
	const window = { location: { href: "" } };
	const context = vm.createContext({
		document: {
			addEventListener() {},
			querySelectorAll() { return [link]; },
		},
		window,
		App: {
			setNotice(message) { throw new Error(message); },
			async api(path) {
				assert.equal(path, "/api/session");
				return { authenticated };
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext("initPublicProtectedLinks()", context);
	await link.events.click({ preventDefault() {} });
	assert.equal(
		window.location.href,
		`login.html?next=${encodeURIComponent(link.href)}`,
	);
	authenticated = true;
	await link.events.click({ preventDefault() {} });
	assert.equal(window.location.href, link.href);
});
