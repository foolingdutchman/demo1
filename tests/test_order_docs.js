const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontend = path.join(__dirname, "..", "frontend");
const app = fs.readFileSync(path.join(frontend, "app.js"), "utf8");
const docs = fs.readFileSync(path.join(frontend, "order_docs.js"), "utf8");
const page = fs.readFileSync(path.join(frontend, "order_docs.html"), "utf8");

function element() {
	return {
		children: [], events: {}, textContent: "", className: "", href: "", hidden: false,
		append(...items) { this.children.push(...items); },
		replaceChildren(...items) { this.children = items; },
		addEventListener(name, callback) { this.events[name] = callback; },
	};
}

test("order documents lists categories, generates, and downloads purchase contract", async () => {
	assert.equal((page.match(/class="document-row"/g) || []).length, 4);
	assert.doesNotMatch(page, /<button[^>]+disabled/);
	const elements = new Map();
	const document = {
		documentElement: { lang: "zh-CN" },
		addEventListener() {},
		createElement: element,
		getElementById(id) {
			if (!elements.has(id)) elements.set(id, element());
			return elements.get(id);
		},
	};
	const window = { document, location: { href: "" }, FormData: class {} };
	const calls = [];
	const downloads = [];
	let stored = [];
	const context = vm.createContext({
		document, window, location: { search: "?id=5" }, URLSearchParams,
		App: {
			t(key, values = {}) { return values.filename ? `${key}:${values.filename}` : key; },
			setNotice() {},
			async api(path, options = {}) {
				calls.push({ path, options });
				if (options.method === "POST") {
					stored = [{
						id: 1,
						document_type: "purchase_contract",
						filename: "purchase-contract-5.pdf",
						created_at: "2026-11-24 03:00:00",
					}];
					return { document: stored[0] };
				}
				return { documents: stored };
			},
			async download(path) { downloads.push(path); },
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(docs, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initOrderDocumentsPage()", context);
	const list = elements.get("order-docs-list");
	assert.equal(list.children.length, 4);
	assert.equal(list.children[0].children[2].children[0].textContent, "order.contract");
	assert.equal(
		list.children[1].children[2].children[1].textContent,
		"order.document.upload",
	);
	assert.equal(list.children[2].children[1].children[0].textContent, "order.document.empty");
	await list.children[0].children[2].children[0].events.click();
	assert.equal(calls[1].options.method, "POST");
	assert.equal(list.children[0].children[1].children[0].textContent, "purchase-contract-5.pdf");
	await list.children[0].children[2].children[0].events.click();
	assert.deepEqual(
		downloads,
		["/api/orders/5/documents/purchase_contract/content"],
	);
});
