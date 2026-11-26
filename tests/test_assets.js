const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "..", "frontend", "app.js"), "utf8");

function element() {
	return {
		children: [], dataset: {}, textContent: "", href: "",
		append(...items) { this.children.push(...items); },
		replaceChildren(...items) { this.children = items; },
	};
}

test("assets page renders every user order and waybill", async () => {
	const elements = new Map([
		["assets-orders-list", element()],
		["assets-waybills-list", element()],
	]);
	const document = {
		documentElement: { lang: "zh-CN" },
		body: { dataset: {} },
		addEventListener() {},
		createElement: element,
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	const requests = [];
	const context = vm.createContext({
		document,
		Intl,
		App: {
			t(key, values = {}) { return values.id ? `${key}${values.id}` : key; },
			async api(url) {
				requests.push(url);
				if (url === "/api/orders") return {
					orders: [
						{ id: 2, created_at: "2026-11-02", batch_number: "B2",
							product: "棉花", origin: "苏丹",
							quantity_tons: 20, supplier_price_usd_per_ton: 1100,
							supplier_quote_basis: "CIF" },
						{ id: 1, created_at: "2026-11-01", batch_number: "B1",
							product: "棉花", origin: "新疆",
							quantity_tons: 10, supplier_price_usd_per_ton: 1000,
							supplier_quote_basis: "FOB" },
					],
				};
				if (url === "/api/waybills") return {
					waybills: [
						{ order_id: 2, product: "棉花", origin: "苏丹",
							BL_NUMBER: "BL-2", BL_NET_WEIGHT: "20000",
							WEIGHT_UNIT: "KGS", DEPARTURE_DATE: "2026-11-10",
							ARRIVAL_DATE: "2026-12-01", VOYAGE_NUMBER: "V2" },
						{ order_id: 1, product: "棉花", origin: "新疆",
							BL_NUMBER: "BL-1", BL_NET_WEIGHT: "10000",
							WEIGHT_UNIT: "KGS", DEPARTURE_DATE: "2026-11-09",
							ARRIVAL_DATE: "2026-11-30", VOYAGE_NUMBER: "V1" },
					],
				};
				throw new Error(`Unexpected API request: ${url}`);
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(
		`initAuthenticatedPage = async () => true;
		initMarketBoard = () => {};
		handleUnauthorized = (error) => { throw error; };`,
		context,
	);

	await vm.runInContext("initAssetsPage()", context);

	assert.deepEqual(requests.sort(), ["/api/orders", "/api/waybills"]);
	const ordersTable = elements.get("assets-orders-list").children[0].children[0];
	const waybillsTable = elements.get("assets-waybills-list").children[0].children[0];
	assert.equal(ordersTable.children[1].children.length, 2);
	assert.equal(waybillsTable.children[1].children.length, 2);
	assert.equal(ordersTable.children[1].children[0].children[7].children[0].href, "order_detail.html?id=2");
	assert.equal(waybillsTable.children[1].children[0].children[9].children[0].href, "order_docs.html?id=2");
});
