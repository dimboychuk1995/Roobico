/* AI-помощник: панель чата поверх приложения.
   Стримит ответ с /assistant/api/chat (text/plain чанками).
   CSRF-заголовок добавляется автоматически (csrf.js патчит fetch). */
(function () {
  "use strict";

  var GREETING =
    "Hi! I'm the Roobico assistant. Ask me how to do something — " +
    "for example: \"How do I create a work order?\" or \"Where do I see what customers owe?\"";

  var panel, messagesEl, form, input, sendBtn;
  var history = []; // {role: "user"|"assistant", content}
  var busy = false;

  // Состояние живёт в sessionStorage (в рамках вкладки), чтобы чат
  // не сбрасывался при переходах между страницами.
  var STORAGE_KEY = "roobicoAssistantChat";
  var MAX_SAVED_MESSAGES = 40;
  var currentUser = "";

  function loadState() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var state = JSON.parse(raw);
      // Чат другого пользователя (перелогин в той же вкладке) не восстанавливаем.
      if (!state || state.user !== currentUser) {
        sessionStorage.removeItem(STORAGE_KEY);
        return null;
      }
      if (!Array.isArray(state.history)) state.history = [];
      return state;
    } catch (e) {
      return null;
    }
  }

  function saveState() {
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
        user: currentUser,
        open: !panel.classList.contains("d-none"),
        history: history.slice(-MAX_SAVED_MESSAGES)
      }));
    } catch (e) {
      /* sessionStorage недоступен (приватный режим) — работаем без сохранения */
    }
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Минимальный markdown: **bold**, `code`, переводы строк.
  function renderText(target, text) {
    target.innerHTML = escapeHtml(text)
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>");
  }

  function addBubble(role) {
    var el = document.createElement("div");
    el.className = "assistant-msg assistant-msg-" + role;
    messagesEl.appendChild(el);
    scrollDown();
    return el;
  }

  function scrollDown() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setBusy(value) {
    busy = value;
    sendBtn.disabled = value;
  }

  function pageContext() {
    return window.location.pathname + " — " + document.title;
  }

  function send(text) {
    history.push({ role: "user", content: text });
    saveState();
    renderText(addBubble("user"), text);

    var botBubble = addBubble("assistant");
    botBubble.innerHTML = '<span class="assistant-typing">Thinking…</span>';

    setBusy(true);
    fetch("/assistant/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history.slice(-12), page: pageContext() })
    })
      .then(function (resp) {
        if (!resp.ok) {
          return resp
            .json()
            .catch(function () { return {}; })
            .then(function (data) {
              throw new Error(data.error || "Assistant failed — please try again.");
            });
        }
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var answer = "";

        function pump() {
          return reader.read().then(function (r) {
            if (r.done) return answer;
            answer += decoder.decode(r.value, { stream: true });
            renderText(botBubble, answer);
            scrollDown();
            return pump();
          });
        }

        return pump().then(function (full) {
          if (full) {
            history.push({ role: "assistant", content: full });
            saveState();
          } else {
            renderText(botBubble, "I didn't get an answer — please try again.");
          }
        });
      })
      .catch(function (err) {
        botBubble.classList.add("assistant-msg-error");
        renderText(botBubble, err && err.message ? err.message : "Assistant failed — please try again.");
        // Неудачный обмен в историю не пишем: последний элемент — вопрос пользователя.
        history.pop();
        saveState();
      })
      .finally(function () {
        setBusy(false);
        input.focus();
      });
  }

  function onSubmit(e) {
    e.preventDefault();
    if (busy) return;
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    send(text);
  }

  function togglePanel(open, skipFocus) {
    var willOpen = open != null ? open : panel.classList.contains("d-none");
    panel.classList.toggle("d-none", !willOpen);
    if (willOpen) {
      if (!messagesEl.childNodes.length) {
        renderText(addBubble("assistant"), GREETING);
      }
      scrollDown();
      if (!skipFocus) input.focus();
    }
    saveState();
  }

  function restoreState() {
    var state = loadState();
    if (!state) return;
    history = state.history;
    for (var i = 0; i < history.length; i++) {
      renderText(addBubble(history[i].role), history[i].content);
    }
    // Панель была открыта до перехода — открываем снова, но фокус не крадём.
    if (state.open) togglePanel(true, true);
  }

  function init() {
    var toggleBtn = document.getElementById("assistantToggleBtn");
    panel = document.getElementById("assistantPanel");
    messagesEl = document.getElementById("assistantMessages");
    form = document.getElementById("assistantForm");
    input = document.getElementById("assistantInput");
    sendBtn = document.getElementById("assistantSendBtn");
    if (!toggleBtn || !panel || !messagesEl || !form || !input || !sendBtn) return;

    currentUser = panel.getAttribute("data-user") || "";
    restoreState();

    toggleBtn.addEventListener("click", function () { togglePanel(); });
    document.getElementById("assistantCloseBtn").addEventListener("click", function () {
      togglePanel(false);
    });
    form.addEventListener("submit", onSubmit);
    // Enter — отправить, Shift+Enter — новая строка.
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        form.requestSubmit ? form.requestSubmit() : onSubmit(e);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
