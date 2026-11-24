const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const frontend = path.join(__dirname, "..", "frontend");
const app = fs.readFileSync(path.join(frontend, "app.js"), "utf8");
const userInfo = fs.readFileSync(path.join(frontend, "user_info.js"), "utf8");

function element() {
	return {
		children: [], events: {}, textContent: "", value: "", href: "", disabled: false,
		append(...items) { this.children.push(...items); },
		replaceChildren(...items) { this.children = items; },
		addEventListener(name, callback) { this.events[name] = callback; },
		reset() {},
	};
}

test("authenticated username links to profile and profile form saves values", async () => {
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
	const calls = [];
	const window = { document, location: { href: "" } };
	const context = vm.createContext({
		document, window, location: { search: "" }, FormData: class {
			entries() { return []; }
		},
		App: {
			t(key, values = {}) { return values.username ? `${key}:${values.username}` : key; },
			setNotice() {},
			async api(path, options = {}) {
				calls.push({ path, options });
				if (path === "/api/session") {
					return { authenticated: true, user: { username: "tester" } };
				}
				if (!options.method) {
					return {
						profile: {
							username: "tester",
							company_name: "Existing Company",
							company_address: "",
							foreign_bank_account: "",
							cny_bank_account: "",
						},
					};
				}
				return { ok: true };
			},
		},
	});
	vm.runInContext(app, context);
	vm.runInContext(userInfo, context);
	assert.equal(
		await vm.runInContext("initAuthenticatedPage('user_info.html')", context),
		true,
	);
	assert.equal(elements.get("username").children[0].href, "user_info.html");
	assert.equal(elements.get("username").children[0].textContent, "site.logged_in:tester");
	await vm.runInContext("initUserInfoPage()", context);
	elements.get("profile-company-name").value = "Updated Company";
	await elements.get("profile-form").events.submit({ preventDefault() {} });
	const payload = JSON.parse(
		calls.find((call) => call.path === "/api/user-profile" && call.options.method === "PATCH")
			.options.body,
	);
	assert.equal(payload.company_name, "Updated Company");
	assert.equal(elements.get("profile-status").textContent, "profile.saved");
});
