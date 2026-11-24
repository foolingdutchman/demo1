window.App = (() => {
	let messages = {};
	let locale = "zh-CN";

	function translate(key, values = {}) {
		let text = messages[key] || key;
		for (const [name, value] of Object.entries(values)) {
			text = text.replaceAll(`{${name}}`, String(value));
		}
		return text;
	}

	async function api(path, options = {}) {
		const base = window.API_BASE_URL.replace(/\/+$/, "");
		const url = new URL(`${base}${path}`);
		url.searchParams.set("locale", locale);
		const response = await fetch(url, {
			...options,
			credentials: "include",
		});
		if (response.status === 204) {
			return null;
		}
		const data = await response.json();
		if (!response.ok) {
			const error = new Error(data.error?.message || response.statusText);
			error.status = response.status;
			throw error;
		}
		return data;
	}

	async function download(path) {
		const base = window.API_BASE_URL.replace(/\/+$/, "");
		const url = new URL(`${base}${path}`);
		url.searchParams.set("locale", locale);
		const response = await fetch(url, { credentials: "include" });
		if (!response.ok) {
			const data = await response.json();
			const error = new Error(data.error?.message || response.statusText);
			error.status = response.status;
			throw error;
		}
		const blob = await response.blob();
		const disposition = response.headers.get("Content-Disposition") || "";
		const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] || "purchase-contract.pdf";
		const objectUrl = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = objectUrl;
		link.download = filename;
		link.click();
		URL.revokeObjectURL(objectUrl);
		return filename;
	}

	async function init() {
		installDialogStyles();
		const requested = localStorage.getItem("language") || navigator.language || "";
		locale = requested.toLowerCase().startsWith("en") ? "en" : "zh-CN";
		const result = await api(`/api/translations?locale=${encodeURIComponent(locale)}`);
		locale = result.locale;
		messages = result.messages;
		document.documentElement.lang = locale;
		document.querySelectorAll("[data-i18n]").forEach((element) => {
			element.textContent = translate(element.dataset.i18n);
		});
		renderLanguageSwitch();
	}

	function renderLanguageSwitch() {
		const container = document.getElementById("language-switch");
		if (!container) return;
		container.replaceChildren();
		for (const [value, key] of [["zh-CN", "language.chinese"], ["en", "language.english"]]) {
			const button = document.createElement("button");
			button.type = "button";
			button.textContent = translate(key);
			button.setAttribute("aria-pressed", String(locale === value));
			button.addEventListener("click", () => {
				localStorage.setItem("language", value);
				location.reload();
			});
			container.append(button);
		}
	}

	function setNotice(message) {
		const notice = document.getElementById("notice");
		if (!notice) return;
		notice.textContent = message || "";
		notice.classList?.toggle("is-visible", Boolean(message));
	}

	function confirm(message) {
		return new Promise((resolve) => {
			const overlay = document.createElement("div");
			overlay.className = "app-dialog-overlay";
			const dialog = document.createElement("div");
			dialog.className = "app-dialog";
			dialog.setAttribute("role", "alertdialog");
			dialog.setAttribute("aria-modal", "true");
			const icon = document.createElement("div");
			icon.className = "app-dialog-icon";
			icon.textContent = "!";
			const title = document.createElement("h2");
			title.textContent = translate("site.confirm_title");
			const content = document.createElement("p");
			content.textContent = message;
			const actions = document.createElement("div");
			actions.className = "app-dialog-actions";
			const cancel = document.createElement("button");
			cancel.type = "button";
			cancel.className = "app-dialog-cancel";
			cancel.textContent = translate("site.cancel");
			const accept = document.createElement("button");
			accept.type = "button";
			accept.className = "app-dialog-confirm";
			accept.textContent = translate("site.confirm_action");
			const close = (result) => {
				overlay.remove();
				document.removeEventListener("keydown", keydown);
				resolve(result);
			};
			const keydown = (event) => {
				if (event.key === "Escape") close(false);
				if (event.key === "Enter") close(true);
			};
			cancel.addEventListener("click", () => close(false));
			accept.addEventListener("click", () => close(true));
			overlay.addEventListener("click", (event) => {
				if (event.target === overlay) close(false);
			});
			actions.append(cancel, accept);
			dialog.append(icon, title, content, actions);
			overlay.append(dialog);
			document.body.append(overlay);
			document.addEventListener("keydown", keydown);
			accept.focus();
		});
	}

	function installDialogStyles() {
		if (document.getElementById("app-dialog-styles")) return;
		const style = document.createElement("style");
		style.id = "app-dialog-styles";
		style.textContent = `
			#notice.is-visible { padding: 12px 14px; border: 1px solid #f1c7c1; border-radius: 10px; background: #fff4f2; box-shadow: 0 5px 18px #9c2f2510; }
			.app-dialog-overlay { position: fixed; z-index: 10000; inset: 0; display: grid; place-items: center; padding: 20px; background: rgba(20, 31, 48, .48); backdrop-filter: blur(3px); }
			.app-dialog { width: min(460px, 100%); padding: 28px; color: #233047; background: #fff; border-radius: 18px; box-shadow: 0 24px 70px rgba(19, 37, 54, .28); animation: app-dialog-in .16s ease-out; }
			.app-dialog-icon { display: grid; place-items: center; width: 44px; height: 44px; color: #fff; background: #c95445; border-radius: 50%; font-size: 24px; font-weight: 800; }
			.app-dialog h2 { margin: 16px 0 8px; font-size: 22px; }
			.app-dialog p { margin: 0; color: #68758a; line-height: 1.7; }
			.app-dialog-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 24px; }
			.app-dialog-actions button { min-height: 40px; padding: 9px 17px; border-radius: 8px; font: inherit; cursor: pointer; }
			.app-dialog-cancel { color: #46546a; background: #f1f5f4; border: 1px solid #dfe8e5; }
			.app-dialog-confirm { color: #fff; background: #b84639; border: 1px solid #b84639; }
			@keyframes app-dialog-in { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }
			@media (prefers-reduced-motion: reduce) { .app-dialog { animation: none; } }
		`;
		document.head.append(style);
	}

	function assetUrl(path) {
		return `${window.API_BASE_URL.replace(/\/+$/, "")}${path}`;
	}

	return { api, assetUrl, confirm, download, init, setNotice, t: translate };
})();
