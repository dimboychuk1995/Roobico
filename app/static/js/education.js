/* Education Center: прогресс уроков, живой поиск, лайтбокс скриншотов,
   кнопка «Ask the AI Helper». CSRF к fetch добавляет csrf.js. */
(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function toast(message, kind) {
    if (typeof window.appAlert === "function") window.appAlert(message, kind || "info");
  }

  /* ── Открыть AI-помощника ────────────────────────────────────────── */
  $all("[data-edu-open-assistant]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var toggle = document.getElementById("assistantToggleBtn");
      var panel = document.getElementById("assistantPanel");
      if (toggle && panel && panel.classList.contains("d-none")) toggle.click();
      var input = document.getElementById("assistantInput");
      if (input) {
        var title = $(".edu-lesson-title");
        if (title && !input.value) input.value = "Tell me more about: " + title.textContent.trim();
        input.focus();
      }
    });
  });

  /* ── Прогресс урока ──────────────────────────────────────────────── */
  var layout = $(".edu-lesson-layout");
  if (layout) {
    var key = layout.getAttribute("data-lesson-key");
    var completed = layout.getAttribute("data-completed") === "1";
    var btn = $("[data-edu-complete]");
    var statusChip = $("[data-edu-status]");

    function applyProgress(p) {
      var modId = key.split("/")[0];
      var mod = p.modules && p.modules[modId];
      var mp = $("[data-edu-module-progress]");
      var mb = $("[data-edu-module-bar]");
      if (mod && mp) mp.textContent = mod.done + " / " + mod.total;
      if (mod && mb) mb.style.width = mod.percent + "%";
      var ob = $("[data-edu-overall-bar]");
      var ot = $("[data-edu-overall-text]");
      if (ob) ob.style.width = p.percent + "%";
      if (ot) ot.textContent = p.done + " of " + p.total + " lessons · " + p.percent + "%";
      var toc = $('[data-toc-key="' + key + '"]');
      if (toc) toc.classList.toggle("is-done", completed);
    }

    function renderButton() {
      if (!btn) return;
      btn.classList.toggle("btn-success", !completed);
      btn.classList.toggle("btn-outline-success", completed);
      btn.innerHTML = completed
        ? '<i class="bi bi-check-circle-fill"></i> Completed — click to undo'
        : '<i class="bi bi-check2-circle"></i> Mark lesson as complete';
      if (statusChip) {
        statusChip.classList.toggle("is-done", completed);
        statusChip.innerHTML = completed
          ? '<i class="bi bi-check-circle-fill"></i> Completed'
          : '<i class="bi bi-circle"></i> Not completed yet';
      }
    }

    if (btn) {
      btn.addEventListener("click", function () {
        var next = !completed;
        btn.disabled = true;
        fetch("/education/api/progress", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lesson: key, completed: next })
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
          .then(function (res) {
            if (!res.ok || !res.data.ok) throw new Error((res.data && res.data.error) || "save failed");
            completed = next;
            layout.setAttribute("data-completed", completed ? "1" : "0");
            renderButton();
            applyProgress(res.data.progress);
            if (completed) {
              var nextLink = $("[data-edu-next]");
              toast(nextLink ? "Lesson completed! Next: " + nextLink.getAttribute("title") : "Lesson completed!", "success");
              if (res.data.progress.percent === 100) toast("You completed every lesson — congratulations!", "success");
            }
          })
          .catch(function () { toast("Could not save your progress — please try again.", "error"); })
          .finally(function () { btn.disabled = false; });
      });
    }
  }

  /* ── Сброс прогресса (главная) ──────────────────────────────────── */
  var resetBtn = $("[data-edu-reset]");
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      var ask = typeof window.appConfirm === "function"
        ? window.appConfirm("Reset your Education Center progress? All lessons will be marked as not completed.", { confirmText: "Reset" })
        : Promise.resolve(window.confirm("Reset your progress?"));
      ask.then(function (ok) {
        if (!ok) return;
        fetch("/education/api/progress/reset", { method: "POST" })
          .then(function (r) { if (!r.ok) throw new Error(); window.location.reload(); })
          .catch(function () { toast("Could not reset progress — please try again.", "error"); });
      });
    });
  }

  /* ── Живой поиск уроков (главная) ───────────────────────────────── */
  var search = document.getElementById("eduSearch");
  var results = document.getElementById("eduSearchResults");
  var indexEl = document.getElementById("eduLessonIndex");
  if (search && results && indexEl) {
    var lessons = [];
    try { lessons = JSON.parse(indexEl.textContent) || []; } catch (e) { lessons = []; }

    function highlight(text, q) {
      var idx = text.toLowerCase().indexOf(q);
      if (idx < 0) return escapeHtml(text);
      return escapeHtml(text.slice(0, idx)) + "<mark>" + escapeHtml(text.slice(idx, idx + q.length)) + "</mark>" + escapeHtml(text.slice(idx + q.length));
    }

    function run() {
      var q = search.value.trim().toLowerCase();
      if (q.length < 2) { results.classList.add("d-none"); results.innerHTML = ""; return; }
      var hits = lessons.filter(function (l) {
        return (l.title + " " + l.summary + " " + l.module + " " + (l.keywords || "")).toLowerCase().indexOf(q) >= 0;
      }).slice(0, 12);
      if (!hits.length) {
        results.innerHTML = '<div class="edu-search-empty">No lessons match “' + escapeHtml(search.value.trim()) + '”. Try another word or ask the AI Helper.</div>';
      } else {
        results.innerHTML = hits.map(function (l) {
          return '<a class="edu-search-item" href="' + escapeHtml(l.url) + '">' +
            '<span class="edu-search-item-module">' + escapeHtml(l.module) + '</span>' +
            '<span><span class="edu-search-item-title">' + (l.done ? '<i class="bi bi-check-circle-fill text-success me-1"></i>' : '') + highlight(l.title, q) + '</span>' +
            '<div class="edu-search-item-summary">' + highlight(l.summary, q) + '</div></span></a>';
        }).join("");
      }
      results.classList.remove("d-none");
    }
    search.addEventListener("input", run);
    search.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var first = results.querySelector("a");
        if (first) window.location.href = first.getAttribute("href");
      }
      if (e.key === "Escape") { search.value = ""; run(); }
    });
  }

  /* ── Лайтбокс ───────────────────────────────────────────────────── */
  var lightbox = document.getElementById("eduLightbox");
  if (lightbox) {
    var lbImg = lightbox.querySelector("img");
    var lbCap = lightbox.querySelector("figcaption");
    function closeLb() { lightbox.hidden = true; lbImg.src = ""; document.body.style.overflow = ""; }
    $all("[data-edu-lightbox]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        lbImg.src = a.getAttribute("href");
        lbImg.alt = a.getAttribute("data-caption") || "";
        lbCap.textContent = a.getAttribute("data-caption") || "";
        lightbox.hidden = false;
        document.body.style.overflow = "hidden";
      });
    });
    lightbox.addEventListener("click", function (e) {
      if (e.target === lightbox || e.target.closest("[data-edu-lightbox-close]") || e.target === lbImg) closeLb();
    });
    document.addEventListener("keydown", function (e) {
      if (lightbox.hidden) return;
      if (e.key === "Escape") closeLb();
    });
  }

  /* ── Клавиатура: ← → между уроками (когда фокус не в поле ввода) ── */
  document.addEventListener("keydown", function (e) {
    if (!layout) return;
    var tag = (e.target && e.target.tagName) || "";
    if (/INPUT|TEXTAREA|SELECT/.test(tag) || e.altKey || e.ctrlKey || e.metaKey) return;
    if (lightbox && !lightbox.hidden) return;
    if (e.key === "ArrowRight") { var n = $("[data-edu-next]"); if (n) window.location.href = n.href; }
    if (e.key === "ArrowLeft") { var p = $(".edu-lesson-nav a.btn-outline-secondary"); if (p) window.location.href = p.href; }
  });
})();
