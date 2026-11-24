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
		if (notice) notice.textContent = message || "";
	}

	function assetUrl(path) {
		return `${window.API_BASE_URL.replace(/\/+$/, "")}${path}`;
	}

	return { api, assetUrl, download, init, setNotice, t: translate };
})();
