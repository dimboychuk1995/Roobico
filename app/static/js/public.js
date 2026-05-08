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
		"payments_page",
		"payments_per_page",
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

		// When the user is searching and hasn't explicitly touched date controls,
		// strip default date params so the backend auto-expands to all_time.
		if (qValue && !form._dateUserTouched) {
			formData.delete("date_preset");
			formData.delete("date_from");
			formData.delete("date_to");
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

	// Find the search input inside `root` whose owning form matches the
	// given action path + tab. Used to pair the live focused input with its
	// counterpart in a freshly fetched document so we can transplant it.
	function findSearchInputInRoot(root, actionPath, tabValue) {
		if (!root) return null;
		var inputs = root.querySelectorAll('form[method="get"] input[name="q"]');
		for (var i = 0; i < inputs.length; i += 1) {
			var candidate = inputs[i];
			var form = candidate.form || candidate.closest("form");
			if (!form) continue;
			if (getFormActionPath(form) !== actionPath) continue;
			if (getFormTabValue(form) !== tabValue) continue;
			return candidate;
		}
		return null;
	}

	function replaceMainContent(html) {
		var parser = new DOMParser();
		var doc = parser.parseFromString(html, "text/html");
		var newMainCol = doc.querySelector(".app-main-col");
		var currentMainCol = document.querySelector(".app-main-col");

		if (!newMainCol || !currentMainCol) {
			return false;
		}

		// Inject any new <link rel="stylesheet"> from the fetched page's <head>
		var cssPromises = [];
		var newLinks = doc.querySelectorAll('head link[rel="stylesheet"]');
		for (var li = 0; li < newLinks.length; li += 1) {
			var href = newLinks[li].getAttribute("href");
			if (!href) continue;
			var hrefBase = href.split("?")[0];
			var exists = false;
			var currentLinks = document.querySelectorAll('head link[rel="stylesheet"]');
			for (var ci = 0; ci < currentLinks.length; ci += 1) {
				var existingHref = (currentLinks[ci].getAttribute("href") || "").split("?")[0];
				if (existingHref === hrefBase) { exists = true; break; }
			}
			if (!exists) {
				var link = document.createElement("link");
				link.rel = "stylesheet";
				link.href = href;
				cssPromises.push(new Promise(function (resolve) {
					link.onload = resolve;
					link.onerror = resolve;
				}));
				document.head.appendChild(link);
			}
		}

		function finishReplace() {
			// --- Preserve focused search input across the swap ---------------
			// If the user is focused in an <input name="q">, we transplant the
			// exact DOM node into the new tree. To keep native focus + caret,
			// the input must NEVER leave the document during the swap. We
			// park it in a hidden, in-document holder, perform the content
			// swap, then move it into its new place — focus survives because
			// the element is always attached to the live document.
			var liveInput = null;
			var liveInputActionPath = "";
			var liveInputTabValue = "";
			var holder = null;
			// Capture caret state BEFORE any DOM mutation. Used as a fallback
			// if some inline initializer (Bootstrap, modal, flatpickr…) steals
			// focus during script re-execution.
			var savedSelectionStart = null;
			var savedSelectionEnd = null;
			var savedSelectionDirection = null;
			var active = document.activeElement;
			if (active && active.tagName === "INPUT" && active.name === "q") {
				var liveForm = active.form || active.closest("form");
				if (liveForm && currentMainCol.contains(active)) {
					liveInput = active;
					liveInputActionPath = getFormActionPath(liveForm);
					liveInputTabValue = getFormTabValue(liveForm);
					try {
						savedSelectionStart = liveInput.selectionStart;
						savedSelectionEnd = liveInput.selectionEnd;
						savedSelectionDirection = liveInput.selectionDirection;
					} catch (e) { /* non-text input */ }
					holder = document.createElement("div");
					holder.setAttribute("aria-hidden", "true");
					holder.style.cssText =
						"position:absolute;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;";
					document.body.appendChild(holder);
					// Moving a focused element between in-document parents
					// preserves focus and selection in all major browsers.
					holder.appendChild(liveInput);
				}
			}

			// --- Preserve scroll position -----------------------------------
			var scrollX = window.scrollX || window.pageXOffset || 0;
			var scrollY = window.scrollY || window.pageYOffset || 0;

			// --- Swap content -----------------------------------------------
			// Use replaceChildren with the imported (detached) nodes so the
			// transplanted live input keeps its identity. innerHTML would
			// re-parse and destroy any preserved nodes.
			var imported = document.importNode(newMainCol, true);
			var importedChildren = [];
			while (imported.firstChild) {
				importedChildren.push(imported.firstChild);
				imported.removeChild(imported.firstChild);
			}
			currentMainCol.replaceChildren.apply(currentMainCol, importedChildren);

			// Now move the live input into its new place inside the freshly
			// attached tree, replacing the server-rendered counterpart.
			if (liveInput) {
				var newCounterpart = findSearchInputInRoot(
					currentMainCol,
					liveInputActionPath,
					liveInputTabValue
				);
				if (newCounterpart && newCounterpart.parentNode) {
					// Mirror server-rendered `value` attribute (defaultValue)
					// without touching the live `value` property — preserves
					// any keystrokes the user typed during the in-flight fetch.
					var serverValueAttr = newCounterpart.getAttribute("value");
					if (serverValueAttr === null) {
						liveInput.removeAttribute("value");
					} else {
						liveInput.setAttribute("value", serverValueAttr);
					}
					newCounterpart.parentNode.replaceChild(liveInput, newCounterpart);
				}
				if (holder && holder.parentNode) {
					holder.parentNode.removeChild(holder);
				}
			}

			// Re-execute inline scripts so per-page initializers run again.
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

			// Restore scroll BEFORE notifying so listeners don't see a jump.
			window.scrollTo(scrollX, scrollY);

			window.dispatchEvent(new CustomEvent("roobico:content-replaced"));
			bindAutoSearchForms();

			// --- Robust focus restoration -----------------------------------
			// Some inline page initializers (Bootstrap dropdowns/modals, the
			// flatpickr autoFocus reset, focus()-based widgets) steal focus
			// either synchronously or via microtask / rAF / setTimeout(0).
			// Re-assert focus + caret position multiple times to win every
			// race. Each attempt is a no-op if focus is already on the input.
			if (liveInput && liveInput.isConnected) {
				var attemptsLeft = 4;

				function ensureFocus() {
					if (!liveInput.isConnected) return;
					if (document.activeElement !== liveInput) {
						try {
							liveInput.focus({ preventScroll: true });
						} catch (e) { /* no-op */ }
					}
					if (
						savedSelectionStart != null &&
						savedSelectionEnd != null &&
						document.activeElement === liveInput
					) {
						var maxPos = (liveInput.value || "").length;
						var s = Math.min(savedSelectionStart, maxPos);
						var e2 = Math.min(savedSelectionEnd, maxPos);
						try {
							liveInput.setSelectionRange(s, e2, savedSelectionDirection || "none");
						} catch (e) { /* no-op */ }
					}
					attemptsLeft -= 1;
					if (attemptsLeft > 0 && document.activeElement !== liveInput) {
						(window.requestAnimationFrame || setTimeout)(ensureFocus);
					}
				}

				// Sync attempt — beats any same-tick focus stealer that ran
				// before this point.
				ensureFocus();
				// Microtask — beats queueMicrotask focus stealers.
				Promise.resolve().then(ensureFocus);
				// Next animation frame — beats rAF stealers.
				if (typeof window.requestAnimationFrame === "function") {
					window.requestAnimationFrame(ensureFocus);
				}
				// Macrotask — beats setTimeout(0) stealers (e.g. Bootstrap).
				setTimeout(ensureFocus, 0);
			}
		}

		if (cssPromises.length > 0) {
			Promise.all(cssPromises).then(finishReplace);
		} else {
			finishReplace();
		}
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
		if (/^\/parts(\/|$)/.test(url.pathname)) return false;
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

		if (activeSearchController) {
			activeSearchController.abort();
		}
		activeSearchController = new AbortController();

		// Show a subtle loading hint only if the request is slow (>400ms).
		// This avoids the dim "page refresh" flash on every keystroke.
		var loadingTimer = setTimeout(function () {
			document.body.classList.add("is-search-loading");
		}, 400);

		try {
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

			// Update URL BEFORE replacing content so that any code triggered
			// by the "content-replaced" event (e.g. loadPaymentsData) reads
			// the correct window.location.search with q / date params.
			var hash = window.location.hash || "";
			window.history.replaceState({}, "", url.pathname + url.search + hash);

			var replaced = replaceMainContent(html);
			if (!replaced) {
				window.location.assign(url.toString());
				return;
			}
			// Focus + caret are preserved by replaceMainContent which
			// transplants the live <input> node into the new tree.
		} catch (error) {
			if (error && error.name === "AbortError") {
				return;
			}
			window.location.assign(url.toString());
		} finally {
			clearTimeout(loadingTimer);
			document.body.classList.remove("is-search-loading");
		}
	}

	function pad2(value) {
		return String(value).padStart(2, "0");
	}

	function toYmd(date) {
		return date.getFullYear() + "-" + pad2(date.getMonth() + 1) + "-" + pad2(date.getDate());
	}

	function cloneDate(date) {
		return new Date(date.getFullYear(), date.getMonth(), date.getDate());
	}

	function startOfWeekMonday(date) {
		var d = cloneDate(date);
		var day = d.getDay();
		var diff = (day + 6) % 7;
		d.setDate(d.getDate() - diff);
		return d;
	}

	function startOfMonth(date) {
		return new Date(date.getFullYear(), date.getMonth(), 1);
	}

	function startOfQuarter(date) {
		var month = date.getMonth();
		var quarterStartMonth = Math.floor(month / 3) * 3;
		return new Date(date.getFullYear(), quarterStartMonth, 1);
	}

	function startOfYear(date) {
		return new Date(date.getFullYear(), 0, 1);
	}

	function updateDateInputValue(input, ymd) {
		if (!input) return;
		input.value = ymd || "";
		if (input._flatpickr) {
			input._flatpickr.setDate(ymd || null, false, "Y-m-d");
		}
	}

	function applyDatePresetToForm(form, presetValue) {
		if (!form) return;
		var fromInput = form.querySelector('input[name="date_from"]');
		var toInput = form.querySelector('input[name="date_to"]');
		if (!fromInput || !toInput) return;

		var preset = String(presetValue || "").trim().toLowerCase();
		if (!preset || preset === "custom") {
			return;
		}

		if (preset === "all_time") {
			updateDateInputValue(fromInput, "");
			updateDateInputValue(toInput, "");
			return;
		}

		var today = new Date();
		today = new Date(today.getFullYear(), today.getMonth(), today.getDate());
		var fromDate = null;
		var toDate = cloneDate(today);

		if (preset === "today") {
			fromDate = cloneDate(today);
			toDate = cloneDate(today);
		} else if (preset === "yesterday") {
			fromDate = cloneDate(today);
			fromDate.setDate(fromDate.getDate() - 1);
			toDate = cloneDate(fromDate);
		} else if (preset === "this_week") {
			fromDate = startOfWeekMonday(today);
		} else if (preset === "last_week") {
			var thisWeekStart = startOfWeekMonday(today);
			fromDate = cloneDate(thisWeekStart);
			fromDate.setDate(fromDate.getDate() - 7);
			toDate = cloneDate(thisWeekStart);
			toDate.setDate(toDate.getDate() - 1);
		} else if (preset === "this_month") {
			fromDate = startOfMonth(today);
		} else if (preset === "last_month") {
			var thisMonthStart = startOfMonth(today);
			toDate = cloneDate(thisMonthStart);
			toDate.setDate(toDate.getDate() - 1);
			fromDate = startOfMonth(toDate);
		} else if (preset === "this_quarter") {
			fromDate = startOfQuarter(today);
		} else if (preset === "last_quarter") {
			var thisQuarterStart = startOfQuarter(today);
			toDate = cloneDate(thisQuarterStart);
			toDate.setDate(toDate.getDate() - 1);
			fromDate = startOfQuarter(toDate);
		} else if (preset === "this_year") {
			fromDate = startOfYear(today);
		} else if (preset === "last_year") {
			var thisYearStart = startOfYear(today);
			toDate = cloneDate(thisYearStart);
			toDate.setDate(toDate.getDate() - 1);
			fromDate = startOfYear(toDate);
		}

		updateDateInputValue(fromInput, fromDate ? toYmd(fromDate) : "");
		updateDateInputValue(toInput, toDate ? toYmd(toDate) : "");
	}

	function setupAutoSearch(form) {
		if (form.dataset.autoSearchBound === "1") {
			return;
		}
		form.dataset.autoSearchBound = "1";

		var input = form.querySelector('input[name="q"]');
		if (!input) {
			return;
		}

		var actionPath = getFormActionPath(form);
		var useAjaxSearch = !/^\/parts(\/|$)/.test(actionPath);

		// Track whether the user explicitly touched date controls during
		// this form session.  If the URL already carries date params the
		// user (or a prior search) set them deliberately – treat as touched.
		var _urlParams = new URLSearchParams(window.location.search);
		form._dateUserTouched = _urlParams.has("date_preset") || _urlParams.has("date_from") || _urlParams.has("date_to");

		// Resolve the live form at fire time. After an AJAX search swap, the
		// `<input>` node is transplanted into a freshly rendered form, so the
		// form node we captured in this closure becomes detached. Always read
		// from `input.form` to operate on the current live form.
		function currentForm() {
			return input.form || input.closest("form") || form;
		}

		function submitIfChanged() {
			var liveForm = currentForm();
			var nextSignature = buildFormSignature(liveForm, input);
			if (nextSignature === (input.dataset.lastSearchSignature || "")) {
				return;
			}
			input.dataset.lastSearchSignature = nextSignature;
			if (useAjaxSearch) {
				runSearch(liveForm, input);
				return;
			}

			window.location.assign(buildSearchUrl(liveForm, input).toString());
		}

		// Form-level listeners (submit/change) are bound only to this form
		// node. The form gets replaced on every AJAX swap, so re-bind here
		// each time `setupAutoSearch` is called for a fresh form.
		form.addEventListener("submit", function (event) {
			if (!useAjaxSearch) {
				return;
			}

			event.preventDefault();
			if (input._inputDebounceTimer) {
				clearTimeout(input._inputDebounceTimer);
				input._inputDebounceTimer = null;
			}
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
			var liveForm = currentForm();
			if (target.name === "date_preset") {
				liveForm._dateUserTouched = true;
				applyDatePresetToForm(liveForm, target.value);
			}
			if (target.name === "date_from" || target.name === "date_to") {
				liveForm._dateUserTouched = true;
				var presetSelect = liveForm.querySelector('select[name="date_preset"]');
				if (presetSelect && presetSelect.value !== "custom") {
					presetSelect.value = "custom";
				}
			}
			submitIfChanged();
		});

		// Input-level listener: bind ONCE per input lifetime. The input node
		// is preserved across AJAX swaps (transplanted into the new form), so
		// we must not attach duplicate listeners.
		if (input.dataset.autoSearchInputBound === "1") {
			// Initialize signature for the new form so a re-bind doesn't
			// trigger an immediate refetch.
			input.dataset.lastSearchSignature = buildFormSignature(currentForm(), input);
			return;
		}
		input.dataset.autoSearchInputBound = "1";
		input.dataset.lastSearchSignature = buildFormSignature(currentForm(), input);

		var INPUT_DEBOUNCE_MS = useAjaxSearch ? 250 : 450;
		input.addEventListener("input", function () {
			if (input._inputDebounceTimer) {
				clearTimeout(input._inputDebounceTimer);
			}
			input._inputDebounceTimer = setTimeout(function () {
				input._inputDebounceTimer = null;
				submitIfChanged();
			}, INPUT_DEBOUNCE_MS);
		});
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

	function bindDateInputPickerOpen() {
		if (document.body.dataset.datePickerOpenBound === "1") {
			return;
		}
		document.body.dataset.datePickerOpenBound = "1";

		function tryOpenDatePicker(input) {
			if (!(input instanceof HTMLInputElement)) return;
			if (input.type !== "date") return;
			if (input.disabled || input.readOnly) return;

			if (typeof input.showPicker === "function") {
				try {
					input.showPicker();
				} catch (e) {
					// Some browsers block showPicker in specific contexts.
				}
			}
		}

		document.addEventListener("click", function (event) {
			var target = event && event.target;
			if (!(target instanceof HTMLInputElement)) return;
			if (target.type !== "date") return;
			tryOpenDatePicker(target);
		});

		document.addEventListener("keydown", function (event) {
			var target = event && event.target;
			if (!(target instanceof HTMLInputElement)) return;
			if (target.type !== "date") return;

			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				tryOpenDatePicker(target);
			}
		});
	}

	function initDatePickers(root) {
		if (typeof window.flatpickr !== "function") {
			return;
		}

		var scope = (root && root.querySelectorAll) ? root : document;
		var dateInputs = scope.querySelectorAll('input[type="date"]:not([data-no-flatpickr])');

		for (var i = 0; i < dateInputs.length; i += 1) {
			var input = dateInputs[i];
			if (input.dataset.flatpickrBound === "1") {
				continue;
			}

			input.dataset.flatpickrBound = "1";
			input.classList.add("ss-date-input");
			input.type = "text";
			input.setAttribute("autocomplete", "off");

			window.flatpickr(input, {
				dateFormat: "Y-m-d",
				altInput: true,
				altFormat: "m/d/Y",
				allowInput: true,
				clickOpens: true,
				disableMobile: true,
				monthSelectorType: "static",
				altInputClass: "form-control ss-date-input",
				prevArrow: "<span aria-hidden=\"true\">&#x2039;</span>",
				nextArrow: "<span aria-hidden=\"true\">&#x203A;</span>",
			});
		}
	}

	window.initDatePickers = initDatePickers;
	window.applyDatePresetToForm = applyDatePresetToForm;
	window.dispatchEvent(new CustomEvent("roobico:public-ready"));

	document.addEventListener("DOMContentLoaded", function () {
		bindAutoSearchForms();
		bindSidebarNavigation();
		bindGlobalInputConstraints();
		bindDateInputPickerOpen();
		initDatePickers(document);
		updateSidebarActiveState(window.location.pathname);
	});

	window.addEventListener("roobico:content-replaced", function () {
		initDatePickers(document);
	});
})();

/**
 * Universal table sorting — server-side + client-side fallback.
 *
 * Server-side mode (paginated tables):
 *   Add class "sortable" to the <table>.
 *   Add data-sort-field="mongo_field" to each sortable <th>.
 *   Clicking a <th> navigates to the current URL with sort_by & sort_dir params.
 *   The active sort column is highlighted from URL params on page load.
 *
 * Client-side mode (non-paginated / JS-rendered tables):
 *   Add class "sortable" to the <table>.
 *   Do NOT add data-sort-field to <th> elements.
 *   Works the same as before — sorts DOM rows in place.
 *
 * Columns with class "no-sort" are excluded in both modes.
 * Override cell sort key with data-sort-value="..." on <td>.
 */
(function () {
  "use strict";

  var CURRENCY_RE = /^\s*\$?\s*-?[\d,]+\.?\d*\s*$/;
  var NUMBER_RE   = /^\s*-?[\d,]+\.?\d*\s*$/;
  var DATE_RE     = /^\s*(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\s*$/;
  var ISO_DATE_RE = /^\s*(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})/;

  function stripCurrency(s) { return s.replace(/[$,\s]/g, ""); }

  function parseNum(s) {
    var v = parseFloat(stripCurrency(s));
    return isNaN(v) ? null : v;
  }

  function parseDate(s) {
    if (!s || !s.trim()) return null;
    var m;
    m = ISO_DATE_RE.exec(s);
    if (m) return new Date(+m[1], +m[2] - 1, +m[3]).getTime();
    m = DATE_RE.exec(s);
    if (m) {
      var yr = +m[3];
      if (yr < 100) yr += 2000;
      return new Date(yr, +m[1] - 1, +m[2]).getTime();
    }
    return null;
  }

  function cellValue(td) {
    if (td.hasAttribute("data-sort-value")) return td.getAttribute("data-sort-value");
    return (td.textContent || "").trim();
  }

  function getUrlParam(name) {
    var params = new URLSearchParams(window.location.search);
    return params.get(name) || "";
  }

  function hasServerSort(headerRow) {
    var ths = headerRow.cells;
    for (var i = 0; i < ths.length; i++) {
      if (ths[i].hasAttribute("data-sort-field") && !ths[i].classList.contains("no-sort")) {
        return true;
      }
    }
    return false;
  }

  function navigateWithSort(field, dir) {
    var params = new URLSearchParams(window.location.search);
    params.set("sort_by", field);
    params.set("sort_dir", dir);
    params.delete("page");
    var keysToDelete = [];
    params.forEach(function (_, k) {
      if (/_page$/.test(k)) keysToDelete.push(k);
    });
    for (var i = 0; i < keysToDelete.length; i++) {
      params.delete(keysToDelete[i]);
    }
    window.location.search = params.toString();
  }

  function sortTable(table, colIdx, asc) {
    var tbody = table.tBodies[0];
    if (!tbody) return;

    var rows = Array.prototype.slice.call(tbody.rows);
    if (rows.length === 0) return;

    var type = "text";
    for (var i = 0; i < rows.length && i < 20; i++) {
      var cell = rows[i].cells[colIdx];
      if (!cell) continue;
      var val = cellValue(cell);
      if (!val || val === "-" || val === "—" || val === "N/A") continue;
      if (CURRENCY_RE.test(val) || NUMBER_RE.test(val)) { type = "number"; break; }
      if (parseDate(val) !== null) { type = "date"; break; }
      break;
    }

    rows.sort(function (a, b) {
      var va = cellValue(a.cells[colIdx] || { textContent: "" });
      var vb = cellValue(b.cells[colIdx] || { textContent: "" });
      var cmp = 0;
      if (type === "number") {
        var na = parseNum(va), nb = parseNum(vb);
        if (na === null && nb === null) cmp = 0;
        else if (na === null) cmp = 1;
        else if (nb === null) cmp = -1;
        else cmp = na - nb;
      } else if (type === "date") {
        var da = parseDate(va), db = parseDate(vb);
        if (da === null && db === null) cmp = 0;
        else if (da === null) cmp = 1;
        else if (db === null) cmp = -1;
        else cmp = da - db;
      } else {
        va = va.toLowerCase();
        vb = vb.toLowerCase();
        cmp = va < vb ? -1 : va > vb ? 1 : 0;
      }
      return asc ? cmp : -cmp;
    });

    for (var j = 0; j < rows.length; j++) {
      tbody.appendChild(rows[j]);
    }
  }

  function initTable(table) {
    if (table._tableSortInit) return;
    table._tableSortInit = true;

    var thead = table.tHead;
    if (!thead) return;
    var headerRow = thead.rows[0];
    if (!headerRow) return;

    var ths = headerRow.cells;
    var serverMode = hasServerSort(headerRow);

    var activeSortBy = serverMode ? getUrlParam("sort_by") : "";
    var activeSortDir = serverMode ? getUrlParam("sort_dir") : "";

    for (var i = 0; i < ths.length; i++) {
      (function (th, idx) {
        if (th.classList.contains("no-sort")) return;

        var field = th.getAttribute("data-sort-field") || "";

        if (serverMode && !field) return;

        th.classList.add("sortable-th");
        th.style.cursor = "pointer";
        th.style.userSelect = "none";
        th.setAttribute("title", "Click to sort");

        var indicator = document.createElement("span");
        indicator.className = "sort-indicator";
        indicator.textContent = "";
        th.appendChild(indicator);

        if (serverMode && field === activeSortBy && activeSortDir) {
          var isAsc = activeSortDir === "asc";
          th.setAttribute("data-sort-dir", activeSortDir);
          th.classList.add(isAsc ? "sort-asc" : "sort-desc");
          indicator.textContent = isAsc ? " ▲" : " ▼";
        }

        th.addEventListener("click", function () {
          var curDir = th.getAttribute("data-sort-dir");
          var asc = curDir !== "asc";

          if (serverMode) {
            navigateWithSort(field, asc ? "asc" : "desc");
            return;
          }

          for (var k = 0; k < ths.length; k++) {
            ths[k].removeAttribute("data-sort-dir");
            ths[k].classList.remove("sort-asc", "sort-desc");
            var ind = ths[k].querySelector(".sort-indicator");
            if (ind) ind.textContent = "";
          }
          th.setAttribute("data-sort-dir", asc ? "asc" : "desc");
          th.classList.add(asc ? "sort-asc" : "sort-desc");
          indicator.textContent = asc ? " ▲" : " ▼";
          sortTable(table, idx, asc);
        });
      })(ths[i], i);
    }
  }

  window.TableSort = {
    init: function (tableOrSelector) {
      if (typeof tableOrSelector === "string") {
        var tables = document.querySelectorAll(tableOrSelector);
        for (var i = 0; i < tables.length; i++) initTable(tables[i]);
      } else if (tableOrSelector && tableOrSelector.tagName === "TABLE") {
        initTable(tableOrSelector);
      }
    },
    refresh: function (table) {
      if (table) {
        table._tableSortInit = false;
        initTable(table);
      }
    }
  };

  function autoInit() {
    var tables = document.querySelectorAll("table.sortable");
    for (var i = 0; i < tables.length; i++) initTable(tables[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoInit);
  } else {
    autoInit();
  }

  window.addEventListener("roobico:content-replaced", autoInit);

  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.tagName === "TABLE" && node.classList.contains("sortable")) {
            initTable(node);
          }
          var nested = node.querySelectorAll ? node.querySelectorAll("table.sortable") : [];
          for (var k = 0; k < nested.length; k++) initTable(nested[k]);
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
})();

/* ── Contacts form ── */
(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      if (ch === "&") return "&amp;";
      if (ch === "<") return "&lt;";
      if (ch === ">") return "&gt;";
      if (ch === '"') return "&quot;";
      return "&#39;";
    });
  }

  function getRoots() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-contacts-form]"));
  }

  function getList(root) {
    return root ? root.querySelector("[data-contacts-list]") : null;
  }

  function getRows(root) {
    var list = getList(root);
    return list ? Array.prototype.slice.call(list.querySelectorAll("[data-contact-row]")) : [];
  }

  function buildRowHtml(contact, index, title) {
    var item = contact || {};
    var rowTitle = title || "Contact";
    return (
      '<div class="border rounded p-3" data-contact-row>' +
      '<div class="d-flex align-items-center justify-content-between mb-3">' +
      '<div class="fw-semibold" data-contact-title>' + escapeHtml(rowTitle) + " " + escapeHtml(String(index + 1)) + "</div>" +
      '<button type="button" class="btn btn-sm btn-outline-danger" data-remove-contact>Remove</button>' +
      '</div>' +
      '<div class="row g-3">' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">First Name</label>' +
      '<input type="text" class="form-control" name="contact_first_name" value="' + escapeHtml(item.first_name || "") + '">' +
      '</div>' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">Last Name</label>' +
      '<input type="text" class="form-control" name="contact_last_name" value="' + escapeHtml(item.last_name || "") + '">' +
      '</div>' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">Phone</label>' +
      '<input type="text" class="form-control" name="contact_phone" value="' + escapeHtml(item.phone || "") + '">' +
      '</div>' +
      '<div class="col-12 col-md-3">' +
      '<label class="form-label">Email</label>' +
      '<input type="email" class="form-control" name="contact_email" value="' + escapeHtml(item.email || "") + '">' +
      '</div>' +
      '<div class="col-12">' +
      '<div class="form-check">' +
      '<input class="form-check-input" type="radio" data-contact-main name="contact_main_index" value="' + escapeHtml(String(index)) + '"' + (item.is_main ? ' checked' : '') + '>' +
      '<label class="form-check-label">Main contact</label>' +
      '</div>' +
      '</div>' +
      '</div>' +
      '</div>'
    );
  }

  function normalizeContacts(rawContacts) {
    var list = Array.isArray(rawContacts) ? rawContacts : [];
    var out = list
      .map(function (item) {
        return {
          first_name: String(item && item.first_name || "").trim(),
          last_name: String(item && item.last_name || "").trim(),
          phone: String(item && item.phone || "").trim(),
          email: String(item && item.email || "").trim(),
          is_main: !!(item && item.is_main),
        };
      })
      .filter(function (item) {
        return !!(item.first_name || item.last_name || item.phone || item.email);
      });

    if (!out.length) {
      return [];
    }

    var mainIndex = out.findIndex(function (item) { return item.is_main; });
    if (mainIndex < 0) {
      mainIndex = 0;
    }
    out.forEach(function (item, index) {
      item.is_main = index === mainIndex;
    });
    return out;
  }

  function reindex(root) {
    var rows = getRows(root);
    var title = (root && root.getAttribute("data-contact-title")) || "Contact";
    rows.forEach(function (row, index) {
      var titleEl = row.querySelector("[data-contact-title]");
      var mainInput = row.querySelector("[data-contact-main]");
      if (titleEl) {
        titleEl.textContent = title + " " + String(index + 1);
      }
      if (mainInput) {
        mainInput.value = String(index);
      }
    });

    if (!rows.length) {
      addContact(root, { is_main: true });
      rows = getRows(root);
    }

    var hasChecked = rows.some(function (row) {
      var mainInput = row.querySelector("[data-contact-main]");
      return !!(mainInput && mainInput.checked);
    });

    if (!hasChecked && rows[0]) {
      var firstMainInput = rows[0].querySelector("[data-contact-main]");
      if (firstMainInput) {
        firstMainInput.checked = true;
      }
    }

    rows.forEach(function (row) {
      var removeBtn = row.querySelector("[data-remove-contact]");
      if (removeBtn) {
        removeBtn.disabled = rows.length <= 1;
      }
    });
  }

  function addContact(root, contact) {
    var list = getList(root);
    if (!list) return;
    var rows = getRows(root);
    list.insertAdjacentHTML("beforeend", buildRowHtml(contact || {}, rows.length, root.getAttribute("data-contact-title") || "Contact"));
    reindex(root);
  }

  function getContacts(root) {
    var rows = getRows(root);
    var out = rows.map(function (row) {
      var mainInput = row.querySelector("[data-contact-main]");
      return {
        first_name: String((row.querySelector('[name="contact_first_name"]') || {}).value || "").trim(),
        last_name: String((row.querySelector('[name="contact_last_name"]') || {}).value || "").trim(),
        phone: String((row.querySelector('[name="contact_phone"]') || {}).value || "").trim(),
        email: String((row.querySelector('[name="contact_email"]') || {}).value || "").trim(),
        is_main: !!(mainInput && mainInput.checked),
      };
    });
    return normalizeContacts(out);
  }

  function setContacts(root, contacts) {
    var list = getList(root);
    if (!list) return;
    var normalized = normalizeContacts(contacts);
    list.innerHTML = "";
    if (!normalized.length) {
      normalized = [{ is_main: true }];
    }
    normalized.forEach(function (contact, index) {
      list.insertAdjacentHTML("beforeend", buildRowHtml(contact, index, root.getAttribute("data-contact-title") || "Contact"));
    });
    reindex(root);
  }

  function initRoot(root) {
    if (!root || root.dataset.contactsInitialized === "1") {
      return;
    }
    root.dataset.contactsInitialized = "1";

    root.addEventListener("click", function (event) {
      var addBtn = event.target.closest("[data-add-contact]");
      if (addBtn) {
        addContact(root, {});
        return;
      }

      var removeBtn = event.target.closest("[data-remove-contact]");
      if (!removeBtn) {
        return;
      }

      var row = removeBtn.closest("[data-contact-row]");
      if (row) {
        row.remove();
        reindex(root);
      }
    });

    root.addEventListener("change", function (event) {
      if (event.target && event.target.matches("[data-contact-main]")) {
        reindex(root);
      }
    });

    reindex(root);
  }

  function initAll() {
    getRoots().forEach(initRoot);
  }

  window.RoobicoContacts = {
    init: initRoot,
    initAll: initAll,
    getContacts: getContacts,
    setContacts: setContacts,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }

  window.addEventListener("roobico:content-replaced", initAll);
})();
