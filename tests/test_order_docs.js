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
		setAttribute() {},
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
					return { documents: stored, document: stored[0] };
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
		list.children[1].children[2].children[1].children[1].textContent,
		"order.document.upload",
	);
	assert.equal(list.children[2].children[1].children[0].textContent, "order.document.empty");
	await list.children[0].children[2].children[0].events.click();
	assert.equal(calls[1].options.method, "POST");
	assert.equal(
		list.children[0].children[1].children[0].children[0].textContent,
		"purchase-contract-5.pdf",
	);
	await list.children[0].children[2].children[0].events.click();
	assert.deepEqual(
		downloads,
		["/api/orders/5/documents/purchase_contract/content"],
	);
});

test("intermediary documents display two generated contracts", async () => {
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
	const downloads = [];
	let generated = false;
	const generatedDocuments = [
		{
			document_type: "supplier_intermediary_contract",
			filename: "supplier-intermediary-contract-5.pdf",
			created_at: "2026-11-25 04:00:00",
		},
		{
			document_type: "intermediary_user_contract",
			filename: "intermediary-user-contract-5.pdf",
			created_at: "2026-11-25 04:00:00",
		},
	];
	const context = vm.createContext({
		document, window, location: { search: "?id=5" }, URLSearchParams,
		App: {
			t(key) { return key; },
			setNotice() {},
			async api(path, options = {}) {
				if (options.method === "POST") {
					generated = true;
					return { documents: generatedDocuments };
				}
				return {
					documents: generated ? generatedDocuments : [],
					has_intermediary: true,
				};
			},
			async download(path) { downloads.push(path); },
		},
	});

	test("related documents align filename download and delete with upload below", async () => {
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
		const file = {
			id: 7,
			filename: "bill.pdf",
			analysis_status: "complete",
			analysis: { category: "bill_of_lading", fields: {}, text: "B/L" },
			created_at: "2026-11-25",
		};
		const waybill = {
			related_document_id: 7,
			BL_NUMBER: "BL-777",
			BL_NET_WEIGHT: "100000",
			WEIGHT_UNIT: "KGS",
			DEPARTURE_DATE: "",
			ARRIVAL_DATE: "",
			PACK_NUMBER: "500",
			PACK_UNIT: "BALES",
			VOYAGE_NUMBER: "V777",
			COO_INOVICE_NUMBER: "INV-77",
			COO_INVOICE_DATE: "2026-10-20",
		};
		const context = vm.createContext({
			document, window, location: { search: "?id=5" }, URLSearchParams,
			App: {
				t(key) { return key; },
				setNotice() {},
				async confirm() { return false; },
				async api(path, options = {}) {
					if (options.method === "PATCH") return { ok: true };
					return {
						documents: [],
						related_documents: [file],
						waybills: [waybill],
					};
				},
				async download() {},
			},
		});
		vm.runInContext(app, context);
		vm.runInContext(docs, context);
		vm.runInContext("initAuthenticatedPage = async () => true", context);
		await vm.runInContext("initOrderDocumentsPage()", context);
		const relatedActions = elements.get("order-docs-list").children[1].children[2];
		assert.equal(relatedActions.children[0].className, "related-document-actions");
		const fileRow = relatedActions.children[0].children[0];
		assert.equal(fileRow.className, "related-document-row");
		assert.equal(fileRow.children[0].textContent, "bill.pdf");
		assert.equal(fileRow.children[1].textContent, "order.document.download");
		assert.equal(fileRow.children[2].textContent, "order.document.delete");
		assert.equal(relatedActions.children[1].className, "related-upload-row");
		const waybillEditor = elements.get("order-docs-list")
			.children.at(-1).children[1];
		assert.equal(waybillEditor.className, "waybill-editor");
		assert.equal(waybillEditor.children[0].className, "waybill-caption");
		assert.equal(waybillEditor.children[1].className, "waybill-table");
		const table = waybillEditor.children[1];
		const displayRow = table.children[1].children[0];
		assert.equal(displayRow.children.length, 13);
		displayRow.children[12].children[0].events.click();
		assert.ok(displayRow.children[2].children[0]);
		assert.equal(displayRow.children[12].children[0].children[0].textContent, "waybill.confirm");
		assert.equal(displayRow.children[12].children[0].children[1].textContent, "waybill.cancel");
		displayRow.children[12].children[0].children[1].events.click();
		assert.equal(displayRow.children[2].textContent, "BL-777");
		assert.equal(displayRow.children[12].children[0].textContent, "waybill.edit");
	});
	vm.runInContext(app, context);
	vm.runInContext(docs, context);
	vm.runInContext("initAuthenticatedPage = async () => true", context);
	await vm.runInContext("initOrderDocumentsPage()", context);
	const list = elements.get("order-docs-list");
	await list.children[0].children[2].children[0].events.click();
	assert.equal(
		list.children[0].children[1].children[0].children[0].textContent,
		generatedDocuments[0].filename,
	);
	assert.equal(
		list.children[1].children[1].children[0].children[0].textContent,
		generatedDocuments[1].filename,
	);
	await list.children[0].children[2].children[0].events.click();
	await list.children[1].children[2].children[0].events.click();
	assert.deepEqual(downloads, [
		"/api/orders/5/documents/supplier_intermediary_contract/content",
		"/api/orders/5/documents/intermediary_user_contract/content",
	]);
});
