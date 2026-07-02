/*
 * Глобальная CSRF-прошивка. Подключается ПЕРВОЙ в базовых layout'ах.
 *
 * Делает три вещи:
 *   1. window.fetch: добавляет X-CSRFToken ко всем same-origin POST/PUT/PATCH/DELETE.
 *   2. XMLHttpRequest: то же самое (покрывает jQuery $.ajax/$.post).
 *   3. submit-слушатель (capture): дописывает скрытое поле csrf_token в любую
 *      POST-форму, где его нет — страховка для динамически созданных форм.
 *
 * Токен берётся из <meta name="csrf-token">, который рендерят базовые шаблоны.
 */
(function () {
  "use strict";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  var MUTATING = /^(POST|PUT|PATCH|DELETE)$/i;

  function isSameOrigin(url) {
    try {
      return new URL(url, window.location.href).origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  // ── fetch ────────────────────────────────────────────────────────────
  if (window.fetch) {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      var method = init.method || (input && input.method) || "GET";
      var url = typeof input === "string" ? input : (input && input.url) || "";

      if (MUTATING.test(method) && isSameOrigin(url)) {
        if (input instanceof Request && !init.headers) {
          var reqHeaders = new Headers(input.headers);
          if (!reqHeaders.has("X-CSRFToken")) {
            reqHeaders.set("X-CSRFToken", csrfToken());
          }
          input = new Request(input, { headers: reqHeaders });
        } else {
          var headers = new Headers(init.headers || {});
          if (!headers.has("X-CSRFToken")) {
            headers.set("X-CSRFToken", csrfToken());
          }
          init.headers = headers;
        }
      }
      return origFetch.call(this, input, init);
    };
  }

  // ── XMLHttpRequest (jQuery и прямые XHR) ─────────────────────────────
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._csrfRequired = MUTATING.test(method) && isSameOrigin(url);
    return origOpen.apply(this, arguments);
  };

  var origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    if (this._csrfRequired) {
      try {
        this.setRequestHeader("X-CSRFToken", csrfToken());
      } catch (e) {
        /* open() ещё не вызван или заголовок уже выставлен — не критично */
      }
    }
    return origSend.apply(this, arguments);
  };

  // ── Обычные и динамически созданные формы ────────────────────────────
  document.addEventListener(
    "submit",
    function (ev) {
      var form = ev.target;
      if (!form || String(form.method).toLowerCase() !== "post") return;
      if (form.querySelector('input[name="csrf_token"]')) return;
      var field = document.createElement("input");
      field.type = "hidden";
      field.name = "csrf_token";
      field.value = csrfToken();
      form.appendChild(field);
    },
    true
  );
})();
