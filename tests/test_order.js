const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "..", "frontend", "app.js"), "utf8");
const order = fs.readFileSync(path.join(__dirname, "..", "frontend", "order.js"), "utf8");

test("order page locks an existing quote and converts the supplier price", async () => {
	function element() {
		return {
			children: [], events: {}, textContent: "", value: "", hidden: false,
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
			insertBefore(item) { this.children.unshift(item); },
			addEventListener(name, callback) { this.events[name] = callback; },
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
	const values = new Map([["orderDraft", JSON.stringify({
		product: "cotton",
		pricing: { domestic_benchmark_cny_per_ton: "15360", fx_cny_per_usd: "6.7" },
		benchmarkSource: "cc_index_2227b",
	})]]);
	const calls = [];
	const window = { location: { href: "" } };
	const context = vm.createContext({
		document, Intl, window,
		sessionStorage: {
			getItem(key) { return values.get(key) || null; },
			setItem(key, value) { values.set(key, value); },
			removeItem(key) { values.delete(key); },
		},
		App: {
			t(key, args = {}) { return key + (args.id || ""); },
			setNotice() {},
			async api(url, options) {
				calls.push({ url, options });
				if (url === "/api/cotton-quotations") return {
					sheets: {
						quotation_summary: [
						{ row_number: 4, batch_number: "original", origin: "Sudan",
							net_weight_tons: 100, gross_price_cny_per_ton: 17700 },
						{ row_number: 5, batch_number: "standard", quotation_date: "2026-10-10", net_weight_tons: 0, standard_weight_tons: 42 },
						{ row_number: 6, batch_number: "missing", net_weight_tons: 0, standard_weight_tons: null },
						{ row_number: 7, batch_number: "ordered", ordered: true },
						],
						sesame: [
							{ row_number: 2, product: "芝麻", processor: "TUHAMA",
								origin: "SUDAN", net_weight_tons: 100, oil_percent: 50,
								price_usc_per_mt: 1400 },
						],
					},
				};
				if (url === "/api/suppliers") return {
					suppliers: [{
						id: 3,
						short_name: "SUD",
						company_name: "Sudan Cotton Co.",
					}],
				};
				if (url === "/api/sudan-cotton-pricing") return {
					max_target_cif_usd_per_ton: 1200, break_even_cif_usd_per_ton: 1300,
					supplier_evaluation: { cif_usd_per_ton: 1000, profit_cny_per_ton: 400 },
				};
				if (url === "/api/orders") return { order: { id: 8 } };
				throw new Error(`Unexpected API request: ${url}`);
			},
		},
	});

	await test("order list links to details and requires confirmation to delete", async () => {
		function element() {
			return {
				children: [], textContent: "", events: {},
				append(...items) { for (const item of items) item.parent = this; this.children.push(...items); },
				replaceChildren(...items) { this.children = items; },
				addEventListener(name, callback) { this.events[name] = callback; },
				remove() { this.parent.children = this.parent.children.filter((item) => item !== this); },
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
		let data = { orders: [{
			id: 5, created_at: "2026-11-23 01:02:03", batch_number: "<script>",
			quotation_date: "2026-11-23", source_quotation_id: 4, quantity_tons: 100,
			supplier_price_usd_per_ton: 1150, supplier_quote_basis: "FOB",
			benchmark_source: "manual", pricing_inputs: { quantity_tons: "100", buyer_bid_vat_inclusive: "false" },
			pricing_result: { max_target_cif_usd_per_ton: 1200, break_even_cif_usd_per_ton: 1300,
				supplier_evaluation: { cif_usd_per_ton: 1200, profit_cny_per_ton: 400 } },
		}] };
		let confirmed = false;
		const requests = [];
		const context = vm.createContext({
			document, Intl,
			window: {},
			App: {
				async confirm() { return confirmed; },
				t(key, values = {}) { return key + (values.id || ""); },
				setNotice() {},
				async api(url, options) {
					requests.push({ url, options });
					if (options?.method === "DELETE") return { ok: true };
					assert.equal(url, "/api/orders");
					return data;
				},
			},
		});
		vm.runInContext(app, context);
		vm.runInContext(order, context);
		vm.runInContext("initAuthenticatedPage = async () => true", context);
		await vm.runInContext("initOrdersPage()", context);
		const list = elements.get("orders-list");
		const card = list.children[0];
		assert.match(card.children[0].textContent, /order.number5.*<script>/);
		assert.equal(card.children[0].href, "order_detail.html?id=5");
		await card.children[1].events.click();
		assert.equal(requests.length, 1);
		confirmed = true;
		await card.children[1].events.click();
		assert.equal(requests[1].url, "/api/orders/5");
		assert.equal(requests[1].options.method, "DELETE");
		assert.equal(list.children[0].textContent, "order.list_empty");
	});
	vm.runInContext(app, context);
	vm.runInContext(order, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initBookOrderPage()", context);
	const quoteSelect = elements.get("order-quotation");
	let quoteFields = elements.get("order-quotation-fields").children;
	const find = (fields, name) =>
		fields.find((field) => field.children[0].textContent === name).children[1];
	const pricing = elements.get("order-pricing-fields").children;
	assert.equal(pricing.length, 3);
	const quantity = find(pricing, "order.quantity_required");
	let netWeight = find(quoteFields, "market.cotton_column.net_weight_tons");
	quantity.value = "33";
	quantity.events.input();
	assert.equal(netWeight.value, "33");
	assert.equal(netWeight.readOnly, true);
	assert.equal(quoteSelect.children.find((option) => option.value === "7").disabled, true);
	elements.get("order-product").value = "sesame";
	elements.get("order-product").events.change();
	assert.equal(quoteSelect.children.some((option) => option.value === "2"), true);
	quoteSelect.value = "2";
	quoteSelect.events.change();
	assert.equal(find(pricing, "order.price_required_usd").value, "1400");
	assert.equal(
		elements.get("order-quotation-fields").children.some(
			(field) => field.children[0].textContent === "market.cotton_column.oil_percent",
		),
		true,
	);
	assert.equal(
		elements.get("order-quotation-fields").children.some(
			(field) => field.children[0].textContent === "market.cotton_column.micronaire",
		),
		false,
	);
	elements.get("order-product").value = "cotton";
	elements.get("order-product").events.change();
	quoteFields = elements.get("order-quotation-fields").children;
	netWeight = find(quoteFields, "market.cotton_column.net_weight_tons");
	quoteSelect.value = "5";
	quoteSelect.events.change();
	assert.equal(quantity.value, "42");
	assert.equal(quantity.readOnly, true);
	assert.equal(netWeight.value, 0);
	assert.equal(find(quoteFields, "market.cotton_column.quotation_date").readOnly, true);
	quoteSelect.value = "6";
	quoteSelect.events.change();
	assert.equal(quantity.readOnly, false);
	assert.equal(quantity.value, "");
	quantity.value = "77";
	quantity.events.input();
	assert.equal(netWeight.value, 0);
	quoteSelect.value = "4";
	quoteSelect.events.change();
	assert.equal(elements.get("order-product").value, "cotton");
	assert.equal(find(quoteFields, "market.cotton_column.batch_number").value, "original");
	assert.equal(find(quoteFields, "market.cotton_column.batch_number").readOnly, true);
	assert.ok(Math.abs(Number(find(pricing, "order.price_required").value) - 119.83) < 0.01);
	const quoteDate = find(quoteFields, "market.cotton_column.quotation_date");
	assert.equal(quoteDate.required, true);
	assert.equal(quoteDate.readOnly, false);
	quoteDate.value = "2026-11-23";
	elements.get("order-delivery-date").value = "2026-12-10";
	find(pricing, "order.price_required").value = "50";
	assert.equal(find(pricing, "order.quantity_required").value, "100");
	assert.equal(find(pricing, "order.quantity_required").readOnly, true);
	elements.get("order-supplier").value = "3";
	elements.get("order-supplier").events.change();
	assert.equal(
		elements.get("order-intermediary").children.some(
			(option) => option.value === "3",
		),
		false,
	);
	elements.get("order-intermediary").value = "";
	elements.get("order-intermediary").value = "__new__";
	elements.get("order-intermediary").events.change();
	assert.equal(
		window.location.href,
		"supplier.html?return=book_order.html&role=intermediary",
	);
	elements.get("order-intermediary").value = "";
	await elements.get("order-form").events.submit({ preventDefault() {} });
	const payload = JSON.parse(calls.find((call) => call.url === "/api/orders").options.body);
	assert.equal(payload.quotation_id, "4");
	assert.equal(payload.product, "cotton");
	assert.equal(payload.quotation_batch_number, "original");
	assert.equal(payload.quotation_origin, "Sudan");
	assert.equal(payload.quotation_quotation_date, "2026-11-23");
	assert.equal(payload.delivery_date, "2026-12-10");
	assert.equal(payload.supplier_id, "3");
	assert.equal(payload.intermediary_id, "");
	assert.equal(payload.pricing_domestic_benchmark_cny_per_ton, "15360");
	assert.equal(payload.pricing_quantity_tons, "100");
	assert.ok(Math.abs(Number(payload.pricing_supplier_quote_usd_per_ton) - 1102.31) < 1e-8);
	assert.equal(window.location.href, "order_detail.html?id=8");
	assert.equal(values.has("orderDraft"), false);
});

test("order list redirects to login and returns after authentication", async () => {
	function element() {
		return {
			events: {}, value: "", textContent: "",
			addEventListener(name, callback) { this.events[name] = callback; },
			setAttribute() {},
		};
	}
	const elements = new Map();
	const document = {
		addEventListener() {},
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	const window = { location: { href: "" } };
	const context = vm.createContext({
		document, window, location: { search: "?next=order.html" }, URLSearchParams,
		App: {
			t(key) { return key; },
			setNotice(message) { if (message) throw new Error(message); },
			async api(url) {
				if (url === "/api/session") return { authenticated: false };
				if (url === "/api/auth") return {};
				throw new Error(`Unexpected API request: ${url}`);
			},
		},
	});

	await test("draft retains the order-only date for an undated quotation", async () => {
		function element() {
			return {
				children: [], events: {}, textContent: "", value: "",
				append(...items) { this.children.push(...items); },
				replaceChildren(...items) { this.children = items; },
				addEventListener(name, callback) { this.events[name] = callback; },
				remove() {},
			};
		}
		const elements = new Map();
		const document = {
			addEventListener() {},
			createElement: element,
			getElementById(id) {
				if (!elements.has(id)) elements.set(id, element());
				return elements.get(id);
			},
		};
		const context = vm.createContext({
			document, Intl,
			sessionStorage: {
				getItem() { return JSON.stringify({
					product: "cotton", quotationId: "5",
					quotation: { quotation_date: "2026-11-23" },
					deliveryDate: "2026-12-10",
					pricing: { quantity_tons: "100", supplier_quote_usd_per_ton: "1102.31" },
				}); },
			},
			App: {
				t(key) { return key; },
				setNotice(message) { if (message) throw new Error(message); },
				async api(url) {
					if (url === "/api/suppliers") {
						return { suppliers: [] };
					}
					return {
						sheets: {
							quotation_summary: [{ row_number: 5, quotation_date: null }],
						},
					};
				},
			},
		});
		vm.runInContext(app, context);
		vm.runInContext(order, context);
		vm.runInContext("initAuthenticatedPage = async () => true", context);
		await vm.runInContext("initBookOrderPage()", context);
		const quoteDate = elements.get("order-quotation-fields").children
			.find((field) => field.children[0].textContent === "market.cotton_column.quotation_date").children[1];
		assert.equal(quoteDate.value, "2026-11-23");
		assert.equal(quoteDate.readOnly, false);
		assert.equal(elements.get("order-delivery-date").value, "2026-12-10");
		const supplierPrice = elements.get("order-pricing-fields").children
			.find((field) => field.children[0].textContent === "order.price_required").children[1];
		assert.equal(supplierPrice.value, "50");
	});
	vm.runInContext(app, context);
	vm.runInContext(order, context);
	await vm.runInContext("initOrdersPage()", context);
	assert.equal(window.location.href, "login.html?next=order.html");
	await vm.runInContext("initLoginPage()", context);
	await elements.get("auth-form").events.submit({ preventDefault() {} });
	assert.equal(window.location.href, "order.html");
});

test("order page selects the product from the product detail URL", async () => {
	function element() {
		return {
			children: [], events: {}, value: "", textContent: "", hidden: false,
			append(...items) { this.children.push(...items); },
			replaceChildren(...items) { this.children = items; },
			addEventListener(name, callback) { this.events[name] = callback; },
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
	const productSelect = element();
	elements.set("order-product", productSelect);
	productSelect.children = ["", "cotton", "sesame", "peanut", "peanut_meal"].map((value) => {
		const option = element();
		option.value = value;
		option.textContent = value;
		return option;
	});
	const context = vm.createContext({
		document, Intl, window: { location: { search: "?product=sesame" } },
		location: { search: "?product=sesame" },
		sessionStorage: {
			getItem() {
				return JSON.stringify({ product: "cotton", quotationId: "5" });
			},
		},
		App: {
			t(key) { return key; },
			setNotice() {},
			async api(url) {
				if (url === "/api/suppliers") return { suppliers: [] };
				if (url === "/api/cotton-quotations") {
					return { sheets: { sesame: [] } };
				}
				throw new Error(`Unexpected API request: ${url}`);
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(order, context);
	vm.runInContext(
		"initAuthenticatedPage = async (next) => { globalThis.authenticatedNext = next; return true; }",
		context,
	);
	await vm.runInContext("initBookOrderPage()", context);
	assert.equal(context.authenticatedNext, "book_order.html?product=sesame");
	assert.equal(productSelect.value, "sesame");
	assert.ok(
		elements.get("order-quotation-fields").children.some(
			(field) => field.children[0].textContent === "market.cotton_column.oil_percent",
		),
	);
});
