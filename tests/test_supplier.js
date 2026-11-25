const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontend = path.join(__dirname, "..", "frontend");
const app = fs.readFileSync(path.join(frontend, "app.js"), "utf8");
const supplier = fs.readFileSync(path.join(frontend, "supplier.js"), "utf8");

function element() {
	return {
		events: {}, disabled: false,
		addEventListener(name, callback) { this.events[name] = callback; },
	};
}

test("supplier form saves supplier and selects it in the order draft", async () => {
	const elements = new Map();
	const document = {
		documentElement: { lang: "en" },
		addEventListener() {},
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	const draft = new Map([["orderDraft", "{}"]]);
	const requests = [];
	const window = { location: { href: "" } };
	const context = vm.createContext({
		document, window, location: { search: "?role=intermediary" }, URLSearchParams,
		FormData: class {
			entries() {
				return [
					["short_name", "SUD"],
					["company_name", "Sudan Cotton Co."],
					["company_address", "Khartoum"],
					["bank_account", ""],
				];
			}
		},
		sessionStorage: {
			getItem(key) { return draft.get(key) || null; },
			setItem(key, value) { draft.set(key, value); },
		},
		App: {
			t(key) { return key; },
			setNotice() {},
			async api(path, options) {
				requests.push({ path, options });
				return { supplier: { id: 9 } };
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(supplier, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initSupplierPage()", context);
	await elements.get("supplier-form").events.submit({ preventDefault() {} });
	assert.equal(requests[0].path, "/api/suppliers");
	assert.equal(JSON.parse(requests[0].options.body).short_name, "SUD");
	assert.equal(JSON.parse(draft.get("orderDraft")).intermediaryId, "9");
	assert.equal(window.location.href, "book_order.html");
});
