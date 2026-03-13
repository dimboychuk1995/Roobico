// Auto-search over server data without full page reload.
(function () {
	var pageInputNames = [
		"page",
		"per_page",
		"parts_page",
		"parts_per_page",
		"orders_page",
		"orders_per_page",
		"cores_page",
		"cores_per_page",
		"estimates_page",
		"estimates_per_page",
	];
	var activeSearchController = null;
	var activeNavigationController = null;

	function getFormActionPath(form) {
		var action = form.getAttribute("action") || window.location.pathname;
		return new URL(action, window.location.origin).pathname;
	}

	function getFormTabValue(form) {
		var tabInput = form.querySelector('input[name="tab"]');
		return tabInput ? String(tabInput.value || "") : "";
	}

	function captureInputFocusState(form, input) {
		var activeElement = document.activeElement;
		var isFocused = activeElement === input;

		return {
			actionPath: getFormActionPath(form),
			tabValue: getFormTabValue(form),
			shouldRestoreFocus: isFocused,
			selectionStart: isFocused ? input.selectionStart : null,
			selectionEnd: isFocused ? input.selectionEnd : null,
			selectionDirection: isFocused ? input.selectionDirection : null,
		};
	}

	function findMatchingSearchInput(focusState) {
		var forms = document.querySelectorAll('form[method="get"]');
		for (var i = 0; i < forms.length; i += 1) {
			var form = forms[i];
			var input = form.querySelector('input[name="q"]');
			if (!input) {
				continue;
			}

			if (getFormActionPath(form) !== focusState.actionPath) {
				continue;
			}

			if (getFormTabValue(form) !== focusState.tabValue) {
				continue;
			}

			return input;
		}

		return null;
	}

	function restoreInputFocusState(focusState) {
		if (!focusState || !focusState.shouldRestoreFocus) {
			return;
		}

		var input = findMatchingSearchInput(focusState);
		if (!input) {
			return;
		}

		input.focus({ preventScroll: true });

		if (
			typeof focusState.selectionStart === "number" &&
			typeof focusState.selectionEnd === "number"
		) {
			input.setSelectionRange(
				focusState.selectionStart,
				focusState.selectionEnd,
				focusState.selectionDirection || "none"
			);
		}
	}

	function buildSearchUrl(form, input) {
		var url = new URL(getFormActionPath(form), window.location.origin);
		var formData = new FormData(form);
		var qValue = (input.value || "").trim();

		for (var i = 0; i < pageInputNames.length; i += 1) {
			formData.delete(pageInputNames[i]);
		}

		if (qValue) {
			formData.set("q", qValue);
		} else {
			formData.delete("q");
		}

		var params = new URLSearchParams();
		formData.forEach(function (value, key) {
			params.append(key, String(value));
		});
		url.search = params.toString();
		return url;
	}

	function buildFormSignature(form, input) {
		var url = buildSearchUrl(form, input);
		return url.pathname + "?" + url.searchParams.toString();
	}

	function replaceMainContent(html) {
		var parser = new DOMParser();
		var doc = parser.parseFromString(html, "text/html");
		var newMainCol = doc.querySelector(".app-main-col");
		var currentMainCol = document.querySelector(".app-main-col");

		if (!newMainCol || !currentMainCol) {
			return false;
		}

		currentMainCol.innerHTML = newMainCol.innerHTML;

		var scripts = currentMainCol.querySelectorAll("script");
		for (var i = 0; i < scripts.length; i += 1) {
			var oldScript = scripts[i];
			var newScript = document.createElement("script");
			for (var a = 0; a < oldScript.attributes.length; a += 1) {
				var attr = oldScript.attributes[a];
				newScript.setAttribute(attr.name, attr.value);
			}
			newScript.text = oldScript.text || oldScript.textContent || "";
			oldScript.parentNode.replaceChild(newScript, oldScript);
		}

		if (doc && typeof doc.title === "string" && doc.title) {
			document.title = doc.title;
		}

		window.dispatchEvent(new CustomEvent("smallshop:content-replaced"));
		bindAutoSearchForms();
		return true;
	}

	function updateSidebarActiveState(pathname) {
		function normalizePath(path) {
			var value = String(path || "").trim();
			if (!value) return "/";
			if (value.length > 1) {
				value = value.replace(/\/+$/, "");
			}
			return value || "/";
		}

		var currentPath = normalizePath(pathname);
		var links = document.querySelectorAll(".app-sidebar-link");
		for (var i = 0; i < links.length; i += 1) {
			var link = links[i];
			var href = link.getAttribute("href") || "";
			if (!href) continue;
			var linkUrl;
			try {
				linkUrl = new URL(href, window.location.origin);
			} catch (e) {
				continue;
			}

			var isActive = normalizePath(linkUrl.pathname) === currentPath;
			link.classList.toggle("active", isActive);
			if (isActive) {
				link.setAttribute("aria-current", "page");
			} else {
				link.removeAttribute("aria-current");
			}
		}
	}

	function shouldHandleSidebarNavigation(anchor, url) {
		if (!anchor || !url) return false;
		if (!anchor.classList.contains("app-sidebar-link")) return false;
		if (anchor.target && anchor.target !== "_self") return false;
		if (anchor.hasAttribute("download")) return false;
		if (url.origin !== window.location.origin) return false;
		if (url.pathname === window.location.pathname && url.search === window.location.search) return false;
		return true;
	}

	async function runSidebarNavigation(url, shouldPushHistory) {
		if (activeNavigationController) {
			activeNavigationController.abort();
		}
		activeNavigationController = new AbortController();

		try {
			document.body.classList.add("is-search-loading");
			var response = await fetch(url.toString(), {
				method: "GET",
				headers: {
					"X-Requested-With": "XMLHttpRequest",
					"Accept": "text/html",
				},
				signal: activeNavigationController.signal,
				credentials: "same-origin",
			});

			if (!response.ok) {
				throw new Error("Navigation request failed");
			}

			var html = await response.text();
			var replaced = replaceMainContent(html);
			if (!replaced) {
				window.location.assign(url.toString());
				return;
			}

			updateSidebarActiveState(url.pathname);
			if (shouldPushHistory) {
				window.history.pushState({}, "", url.pathname + url.search + url.hash);
			}
			window.scrollTo({ top: 0, left: 0, behavior: "auto" });
		} catch (error) {
			if (error && error.name === "AbortError") {
				return;
			}
			window.location.assign(url.toString());
		} finally {
			document.body.classList.remove("is-search-loading");
		}
	}

	function bindSidebarNavigation() {
		if (document.body.dataset.sidebarNavBound === "1") {
			return;
		}
		document.body.dataset.sidebarNavBound = "1";

		document.addEventListener("click", function (event) {
			var anchor = event.target && event.target.closest ? event.target.closest("a.app-sidebar-link") : null;
			if (!anchor) return;
			if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
				return;
			}

			var url;
			try {
				url = new URL(anchor.href, window.location.origin);
			} catch (e) {
				return;
			}

			if (!shouldHandleSidebarNavigation(anchor, url)) {
				return;
			}

			event.preventDefault();
			runSidebarNavigation(url, true);
		});

		window.addEventListener("popstate", function () {
			var links = document.querySelectorAll(".app-sidebar-link");
			var hasSidebar = links && links.length > 0;
			if (!hasSidebar) return;

			var url = new URL(window.location.href);
			runSidebarNavigation(url, false);
		});
	}

	async function runSearch(form, input) {
		var url = buildSearchUrl(form, input);
		var focusState = captureInputFocusState(form, input);

		if (activeSearchController) {
			activeSearchController.abort();
		}
		activeSearchController = new AbortController();

		try {
			document.body.classList.add("is-search-loading");
			var response = await fetch(url.toString(), {
				method: "GET",
				headers: {
					"X-Requested-With": "XMLHttpRequest",
					"Accept": "text/html",
				},
				signal: activeSearchController.signal,
				credentials: "same-origin",
			});

			if (!response.ok) {
				throw new Error("Search request failed");
			}

			var html = await response.text();
			var replaced = replaceMainContent(html);
			if (!replaced) {
				window.location.assign(url.toString());
				return;
			}

			var hash = window.location.hash || "";
			window.history.replaceState({}, "", url.pathname + url.search + hash);
			restoreInputFocusState(focusState);
		} catch (error) {
			if (error && error.name === "AbortError") {
				return;
			}
			window.location.assign(url.toString());
		} finally {
			document.body.classList.remove("is-search-loading");
		}
	}

	function setupAutoSearch(form) {
		if (form.dataset.autoSearchBound === "1") {
			return;
		}

		var input = form.querySelector('input[name="q"]');
		if (!input) {
			return;
		}

		form.dataset.autoSearchBound = "1";
		var lastSubmittedSignature = buildFormSignature(form, input);
		var actionPath = getFormActionPath(form);
		var useAjaxSearch = !/^\/parts(\/|$)/.test(actionPath);

		ensureSearchButton(form, input);

		function submitIfChanged() {
			var nextSignature = buildFormSignature(form, input);
			if (nextSignature === lastSubmittedSignature) {
				return;
			}
			lastSubmittedSignature = nextSignature;
			if (useAjaxSearch) {
				runSearch(form, input);
				return;
			}

			window.location.assign(buildSearchUrl(form, input).toString());
		}

		form.addEventListener("submit", function (event) {
			if (!useAjaxSearch) {
				return;
			}

			event.preventDefault();
			submitIfChanged();
		});

		form.addEventListener("change", function (event) {
			var target = event && event.target;
			if (!target || !target.name) {
				return;
			}
			if (target.name === "q") {
				return;
			}
			if (target.name === "date_preset") {
				var dateFromInput = form.querySelector('input[name="date_from"]');
				var dateToInput = form.querySelector('input[name="date_to"]');
				if (dateFromInput) {
					dateFromInput.value = "";
				}
				if (dateToInput) {
					dateToInput.value = "";
				}
			}
			if (target.name === "date_from" || target.name === "date_to") {
				var presetSelect = form.querySelector('select[name="date_preset"]');
				if (presetSelect && presetSelect.value !== "custom") {
					presetSelect.value = "custom";
				}
			}
		});
	}

	function ensureSearchButton(form, input) {
		if (!form || !input) return;

		var existingSubmit = form.querySelector('button[type="submit"], input[type="submit"]');
		if (existingSubmit) {
			return;
		}

		var btn = document.createElement("button");
		btn.type = "submit";
		btn.className = "btn btn-sm btn-primary";
		btn.textContent = "Search";

		var resetControl = form.querySelector('a.btn, button[type="reset"], input[type="reset"]');
		if (resetControl) {
			var resetParent = resetControl.parentElement;
			if (resetParent) {
				btn.classList.add("me-2");
				resetParent.insertBefore(btn, resetControl);
				return;
			}
		}

		var wrapper = document.createElement("div");
		wrapper.className = "col-auto smallshop-search-btn-wrap";
		wrapper.appendChild(btn);

		var inputContainer = input.closest(".col-12, .col-auto, .col-md-8, .col-lg-6") || input.parentElement;
		if (inputContainer && inputContainer.parentNode) {
			if (inputContainer.nextSibling) {
				inputContainer.parentNode.insertBefore(wrapper, inputContainer.nextSibling);
			} else {
				inputContainer.parentNode.appendChild(wrapper);
			}
			return;
		}

		form.appendChild(wrapper);
	}

	function bindAutoSearchForms() {
		var forms = document.querySelectorAll('form[method="get"]');
		for (var i = 0; i < forms.length; i += 1) {
			if (forms[i].querySelector('input[name="q"]')) {
				setupAutoSearch(forms[i]);
			}
		}
	}

	function countStepPrecision(step) {
		if (!step || step === "any") return null;
		var raw = String(step);
		if (raw.indexOf(".") === -1) return 0;
		return raw.split(".")[1].length;
	}

	function sanitizeNumericString(raw, allowNegative, allowDecimal) {
		var value = String(raw || "").replace(/,/g, "").trim();
		if (!value) return "";

		var out = "";
		var hasDot = false;
		var hasSign = false;
		for (var i = 0; i < value.length; i += 1) {
			var ch = value.charAt(i);
			if (ch >= "0" && ch <= "9") {
				out += ch;
				continue;
			}
			if (allowDecimal && ch === "." && !hasDot) {
				out += ch;
				hasDot = true;
				continue;
			}
			if (allowNegative && ch === "-" && !hasSign && out.length === 0) {
				out += ch;
				hasSign = true;
			}
		}

		if (out === "-" || out === "." || out === "-.") return "";
		return out;
	}

	function clampAndFormatNumberInput(input) {
		if (!input || input.type !== "number") return;
		if (!input.value) return;

		var parsed = Number(input.value);
		if (!Number.isFinite(parsed)) {
			input.value = "";
			return;
		}

		var minAttr = input.getAttribute("min");
		var maxAttr = input.getAttribute("max");
		var min = minAttr !== null && minAttr !== "" ? Number(minAttr) : null;
		var max = maxAttr !== null && maxAttr !== "" ? Number(maxAttr) : null;
		if (Number.isFinite(min) && parsed < min) parsed = min;
		if (Number.isFinite(max) && parsed > max) parsed = max;

		var precision = countStepPrecision(input.getAttribute("step"));
		if (precision === 0) {
			parsed = Math.round(parsed);
			input.value = String(parsed);
			return;
		}

		if (typeof precision === "number" && precision > 0) {
			var factor = Math.pow(10, precision);
			parsed = Math.round(parsed * factor) / factor;
			input.value = String(parsed);
			return;
		}

		input.value = String(parsed);
	}

	function sanitizeNumericLikeInput(input) {
		if (!input) return;

		if (input.type === "number") {
			var step = input.getAttribute("step");
			var isInteger = step === "1" || step === "1.0" || step === "01";
			var minAttr = input.getAttribute("min");
			var min = minAttr !== null && minAttr !== "" ? Number(minAttr) : null;
			var allowNegative = !Number.isFinite(min) || min < 0;
			var sanitized = sanitizeNumericString(input.value, allowNegative, !isInteger);
			if (sanitized !== input.value) {
				input.value = sanitized;
			}
			return;
		}

		if (input.type === "tel" || /phone/i.test(input.name || "") || /phone/i.test(input.id || "")) {
			var tel = String(input.value || "").replace(/[^0-9+()\-\s]/g, "");
			if (tel !== input.value) input.value = tel;
			return;
		}

		var mode = String(input.getAttribute("inputmode") || "").toLowerCase();
		if (mode === "numeric") {
			var numericOnly = sanitizeNumericString(input.value, false, false);
			if (numericOnly !== input.value) input.value = numericOnly;
			return;
		}

		if (mode === "decimal") {
			var decimalOnly = sanitizeNumericString(input.value, false, true);
			if (decimalOnly !== input.value) input.value = decimalOnly;
		}
	}

	function bindGlobalInputConstraints() {
		if (document.body.dataset.globalInputConstraintsBound === "1") {
			return;
		}
		document.body.dataset.globalInputConstraintsBound = "1";

		document.addEventListener("input", function (event) {
			var target = event && event.target;
			if (!(target instanceof HTMLInputElement)) return;
			sanitizeNumericLikeInput(target);
		});

		document.addEventListener("blur", function (event) {
			var target = event && event.target;
			if (!(target instanceof HTMLInputElement)) return;
			if (target.type === "number") {
				clampAndFormatNumberInput(target);
			}
		}, true);

		document.addEventListener("submit", function (event) {
			var form = event && event.target;
			if (!(form instanceof HTMLFormElement)) return;

			var inputs = form.querySelectorAll("input");
			for (var i = 0; i < inputs.length; i += 1) {
				sanitizeNumericLikeInput(inputs[i]);
				if (inputs[i].type === "number") {
					clampAndFormatNumberInput(inputs[i]);
				}
			}
		}, true);
	}

	document.addEventListener("DOMContentLoaded", function () {
		bindAutoSearchForms();
		bindSidebarNavigation();
		bindGlobalInputConstraints();
		updateSidebarActiveState(window.location.pathname);
	});
})();
