const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const appScript = fs.readFileSync(
	path.join(__dirname, "..", "frontend", "app.js"),
	"utf8",
);

function createPage(asset, prices) {
	const ids = new Map();
	function element() {
		return { hidden: false, textContent: "", value: "", href: "", addEventListener() {} };
	}
	const quotes = ["cc_index_3128b", "cc_index_2227b", "cc_index_2129b"].map((id) => {
		const value = element();
		const date = element();
		return {
			dataset: { ccIndex: id },
			value,
			date,
			querySelector(selector) {
				return selector === "[data-cc-index-value]" ? value : date;
			},
		};
	});
	const panel = element();
	panel.hidden = true;
	panel.querySelectorAll = () => quotes;
	ids.set("market-cc-index-panel", panel);
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		getElementById(id) {
			if (!ids.has(id)) ids.set(id, element());
			return ids.get(id);
		},
		querySelectorAll(selector) {
			return selector === "[data-cc-index]" ? quotes : [];
		},
		querySelector() { return element(); },
	};
	const context = vm.createContext({
		document,
		location: { search: `?asset=${asset}&return=assets` },
		URLSearchParams,
		Intl,
		App: {
			t(key, values = {}) {
				if (key === "market.history_as_of") return `Date: ${values.date}`;
				if (key === "market.cc_index_tonne") return "tonne";
				if (key === "market.unavailable_short") return "Unavailable";
				return key;
			},
			async api(url) {
				if (url === "/api/market-prices") {
					if (prices instanceof Error) throw prices;
					return prices;
				}
				return {
					currency: "CNY",
					prices: [{ date: "2026-11-20", price: 15470 }],
					updated_at: "2026-11-20T00:00:00Z",
				};
			},
		},
	});
	vm.runInContext(appScript, context);
	vm.runInContext(`
		initSudanCottonPricing = () => {};
		loadCottonQuotations = () => {};
		updateImportCottonPrice = () => {};
		syncSudanBenchmark = () => {};
		queueSudanCottonPricing = () => {};
		renderMarketCandlestickChart = () => {};
	`, context);
	return { context, panel, quotes };
}

const marketPrices = {
	prices: {
		zce_cotton: 15470,
		ice_cotton: 79.96,
		gold: 2345,
		cc_index_3128b: 16881,
		cc_index_2227b: 15360,
		cc_index_2129b: 17189,
	},
	asset_dates: {
		cc_index_3128b: "2026-11-20",
		cc_index_2227b: "2026-11-19",
		cc_index_2129b: "2026-11-18",
	},
	asset_currencies: { zce_cotton: "CNY", ice_cotton: "US¢" },
	currency: "USD",
	updated_at: "2026-11-20T00:00:00Z",
};

test("Sudan supplier quote uses USC per pound at the API boundary", () => {
	const fields = [
		{ dataset: { sudanField: "supplier_quote_usd_per_ton" }, value: "50" },
		{ dataset: { sudanField: "quantity_tons" }, value: "100" },
	];
	const context = vm.createContext({
		document: {
			addEventListener() {},
			querySelectorAll() { return fields; },
		},
	});
	vm.runInContext(appScript, context);
	const result = vm.runInContext("sudanPricingFields()", context);
	assert.ok(Math.abs(Number(result.supplier_quote_usd_per_ton) - 1102.31) < 1e-8);
	assert.equal(result.quantity_tons, "100");
	fields[0].value = "";
	assert.equal(vm.runInContext("sudanPricingFields()", context).supplier_quote_usd_per_ton, "");
});

test("cotton details show all quotes, dates and return-aware links", async () => {
	for (const asset of ["zce_cotton", "ice_cotton", "cc_index_2227b"]) {
		const { context, panel, quotes } = createPage(asset, marketPrices);
		await vm.runInContext("initMarketDetailPage()", context);
		await new Promise(setImmediate);
		assert.equal(panel.hidden, false);
		assert.deepEqual(quotes.map((quote) => quote.value.textContent), [
			"CNY 16,881 / tonne", "CNY 15,360 / tonne", "CNY 17,189 / tonne",
		]);
		assert.deepEqual(quotes.map((quote) => quote.date.textContent), [
			"Date: 2026-11-20", "Date: 2026-11-19", "Date: 2026-11-18",
		]);
		assert.ok(quotes.every((quote) => quote.href.endsWith("&return=assets")));
	}
});

test("missing quotes and failed requests show unavailable without stale dates", async () => {
	for (const prices of [
		{ ...marketPrices, prices: { zce_cotton: 15470 }, asset_dates: marketPrices.asset_dates },
		new Error("Market unavailable"),
	]) {
		const { context, quotes } = createPage("zce_cotton", prices);
		await vm.runInContext("initMarketDetailPage()", context);
		await new Promise(setImmediate);
		assert.ok(quotes.every((quote) => quote.value.textContent === "Unavailable"));
		assert.ok(quotes.every((quote) => quote.date.textContent === ""));
	}
});

test("non-cotton details do not show the cotton index panel", async () => {
	const { context, panel } = createPage("gold", marketPrices);
	await vm.runInContext("initMarketDetailPage()", context);
	assert.equal(panel.hidden, true);
});

test("quotation table displays uploaded dates and leaves missing dates blank", async () => {
	const translations = JSON.parse(fs.readFileSync(
		path.join(__dirname, "..", "locales", "messages.json"), "utf8",
	));
	for (const [locale, label] of [["zh-CN", "报价日期"], ["en", "Quotation date"]]) {
		function element() {
			return {
				children: [], textContent: "", hidden: true,
				append(...items) { this.children.push(...items); },
				replaceChildren(...items) { this.children = items; },
			};
		}
		const elements = new Map([
			["cotton-quotation-status", element()],
			["cotton-quotation-tables", element()],
			["cotton-quotation-panel", element()],
		]);
		const context = vm.createContext({
			document: {
				documentElement: { lang: locale },
				addEventListener() {},
				getElementById(id) { return elements.get(id); },
				createElement: element,
			},
			Intl,
			App: {
				t(key) { return translations[locale][key]; },
				async api(url) {
					if (url === "/api/session") return { authenticated: true };
					return { sheets: { quotation_summary: [
						{ quotation_date: "2025-01-02", batch_number: "001", ordered: true },
						{ quotation_date: null, batch_number: "002" },
					] } };
				},
			},
		});
		vm.runInContext(appScript, context);
		await vm.runInContext("loadCottonQuotations()", context);
		const container = elements.get("cotton-quotation-tables");
		assert.equal(elements.get("cotton-quotation-status").textContent, "");
		assert.equal(elements.get("cotton-quotation-panel").hidden, false);
		const table = container.children[1].children[0];
		assert.equal(table.children[0].children[0].children[1].textContent, label);
		const rows = table.children[1].children;
		assert.equal(rows[0].children[0].textContent, translations[locale]["quotation.yes"]);
		assert.equal(rows[1].children[0].textContent, translations[locale]["quotation.no"]);
		assert.equal(rows[0].children[1].textContent, "2025-01-02");
		assert.equal(rows[1].children[1].textContent, "");
	}
});

test("quotation preview shows only the first five ordered records", async () => {
	function element() {
		return {
			children: [], textContent: "", hidden: true,
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
		};
	}
	const elements = new Map([
		["cotton-quotation-status", element()],
		["cotton-quotation-tables", element()],
		["cotton-quotation-panel", element()],
	]);
	const records = Array.from({ length: 7 }, (_, index) => ({
		quotation_date: `2026-01-0${7 - index}`,
		batch_number: `batch-${index}`,
	}));
	const context = vm.createContext({
		document: {
			documentElement: { lang: "en" },
			addEventListener() {},
			getElementById(id) { return elements.get(id); },
			createElement: element,
		},
		Intl,
		App: {
			t(key) { return key; },
			async api(url) {
				if (url === "/api/session") return { authenticated: true };
				return { sheets: { quotation_summary: records } };
			},
		},
	});
	vm.runInContext(appScript, context);
	await vm.runInContext("loadCottonQuotations()", context);
	const table = elements.get("cotton-quotation-tables").children[1].children[0];
	const rows = table.children[1].children;
	assert.equal(rows.length, 5);
	assert.deepEqual(rows.map((row) => row.children[1].textContent), records.slice(0, 5).map((row) => row.quotation_date));
});

test("quotation management only edits owned rows and persists changed fields", async () => {
	function element() {
		return {
			children: [], textContent: "", value: "", hidden: true, events: {},
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
			addEventListener(name, callback) { this.events[name] = callback; },
			querySelector() { return this.children[0].children[1]; },
			focus() {},
		};
	}
	const elements = new Map();
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		createElement: element,
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	let patch;
	const records = [
		{ row_number: 4, editable: true, quotation_date: "2026-01-01", batch_number: "001" },
		{ row_number: 3, editable: false, quotation_date: null, batch_number: "002" },
	];
	const context = vm.createContext({
		document, Intl,
		App: {
			t(key) { return key; },
			setNotice(message) { elements.get("notice").textContent = message; },
			async api(url, options) {
				if (url === "/api/session") return { authenticated: true, user: { username: "owner" } };
				if (options?.method === "PATCH") {
					patch = { url, body: JSON.parse(options.body) };
					Object.assign(records[0], patch.body);
					return { ok: true };
				}
				return { sheets: { quotation_summary: records } };
			},
		},
	});
	document.getElementById("notice");
	vm.runInContext(appScript, context);
	await vm.runInContext("initQuotationsPage()", context);
	const table = elements.get("quotation-list").children[0].children[0];
	const rows = table.children[1].children;
	assert.equal(rows.length, 2);
	assert.equal(rows[1].children[0].textContent, "quotation.read_only");
	rows[0].children[0].children[0].events.click();
	const fields = elements.get("quotation-edit-fields").children;
	fields[0].children[1].value = "2026-02-01";
	await elements.get("quotation-edit-form").events.submit({ preventDefault() {} });
	assert.equal(patch.url, "/api/cotton-quotations/4");
	assert.deepEqual(patch.body, { quotation_date: "2026-02-01" });
	assert.equal(elements.get("quotation-status").textContent, "quotation.saved");
	assert.equal(elements.get("quotation-edit-form").hidden, true);
});
