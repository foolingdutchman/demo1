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
	}
	assert.ok(
		html.indexOf('id="market-title"') < html.indexOf('id="trade-products-title"'),
	);
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
