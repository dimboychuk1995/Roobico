/**
 * Attachments component — auto-initialises every .attachments-block on the page.
 *
 * Endpoints used (from the `attachments` blueprint):
 *   POST   /attachments/api/upload          multipart (files + entity_type + entity_id)
 *   GET    /attachments/api/list?entity_type=…&entity_id=…
 *   GET    /attachments/api/<id>/download
 *   DELETE /attachments/api/<id>/delete
 */
(function () {
  "use strict";

  const BASE = "/attachments/api";

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /* ── Lightbox ──────────────────────────────────────────────────── */
  function openLightbox(src) {
    var overlay = document.createElement("div");
    overlay.className = "att-lightbox";
    overlay.innerHTML =
      '<button class="att-lightbox-close" aria-label="Close">&times;</button>' +
      '<img src="' + escapeHtml(src) + '" alt="Preview">';
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay || e.target.classList.contains("att-lightbox-close")) {
        overlay.remove();
      }
    });
    document.addEventListener("keydown", function handler(e) {
      if (e.key === "Escape") {
        overlay.remove();
        document.removeEventListener("keydown", handler);
      }
    });
    document.body.appendChild(overlay);
  }

  /* ── Single block controller ───────────────────────────────────── */
  function AttBlock(el) {
    this.el = el;
    this.entityType = el.dataset.entityType || "";
    this.entityId = el.dataset.entityId || "";
    this.parentId = el.dataset.parentId || "";
    this.gallery = el.querySelector(".att-gallery");
    this.empty = el.querySelector(".att-empty");
    this.counter = el.querySelector(".att-count");
    this.progress = el.querySelector(".att-progress");
    this.fileInput = el.querySelector(".att-file-input");
    this.items = [];

    var self = this;

    if (this.fileInput) {
      this.fileInput.addEventListener("change", function () {
        if (this.files && this.files.length) {
          self.upload(this.files);
          this.value = "";
        }
      });
    }

    if (this.gallery) {
      this.gallery.addEventListener("click", function (e) {
        var delBtn = e.target.closest(".att-delete-btn");
        if (delBtn) {
          e.preventDefault();
          e.stopPropagation();
          var id = delBtn.dataset.attId;
          if (id) self.deleteItem(id);
          return;
        }
        var thumb = e.target.closest(".att-thumb");
        if (thumb) {
          openLightbox(thumb.dataset.fullSrc || thumb.src);
        }
      });
    }

    // Autoload if entity exists
    if (this.entityId) {
      this.load();
    }
  }

  AttBlock.prototype.load = function () {
    var self = this;
    var url = BASE + "/list?entity_type=" + encodeURIComponent(this.entityType) +
      "&entity_id=" + encodeURIComponent(this.entityId);
    if (this.parentId) {
      url += "&parent_id=" + encodeURIComponent(this.parentId);
    }

    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.ok) {
          self.items = data.items || [];
          self.render();
        }
      })
      .catch(function () {});
  };

  AttBlock.prototype.upload = function (fileList) {
    var self = this;
    var fd = new FormData();
    fd.append("entity_type", this.entityType);
    fd.append("entity_id", this.entityId);
    if (this.parentId) fd.append("parent_id", this.parentId);

    for (var i = 0; i < fileList.length; i++) {
      fd.append("files", fileList[i]);
    }

    if (this.progress) this.progress.style.display = "";

    fetch(BASE + "/upload", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (self.progress) self.progress.style.display = "none";
        if (data.ok && data.saved && data.saved.length) {
          self.items = data.saved.concat(self.items);
          self.render();
          if (typeof Swal !== "undefined") {
            Swal.fire({ icon: "success", title: data.saved.length + " file(s) uploaded", timer: 1500, showConfirmButton: false });
          }
        }
        if (data.errors && data.errors.length) {
          var msgs = data.errors.map(function (e) { return e.filename + ": " + e.error; }).join("\n");
          if (typeof Swal !== "undefined") {
            Swal.fire({ icon: "warning", title: "Some files failed", text: msgs });
          } else {
            alert(msgs);
          }
        }
      })
      .catch(function () {
        if (self.progress) self.progress.style.display = "none";
        alert("Upload failed — network error.");
      });
  };

  AttBlock.prototype.deleteItem = function (id) {
    var self = this;
    var doDelete = function () {
      fetch(BASE + "/" + id + "/delete", { method: "DELETE" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            self.items = self.items.filter(function (it) { return it.id !== id; });
            self.render();
          }
        })
        .catch(function () {});
    };

    if (typeof Swal !== "undefined") {
      Swal.fire({
        title: "Delete this attachment?",
        icon: "warning",
        showCancelButton: true,
        confirmButtonColor: "#dc3545",
        confirmButtonText: "Delete",
      }).then(function (result) {
        if (result.isConfirmed) doDelete();
      });
    } else if (confirm("Delete this attachment?")) {
      doDelete();
    }
  };

  AttBlock.prototype.render = function () {
    if (!this.gallery) return;
    var html = "";
    for (var i = 0; i < this.items.length; i++) {
      var it = this.items[i];
      var downloadUrl = BASE + "/" + it.id + "/download";
      html += '<div class="att-item">';
      html += '<button type="button" class="att-delete-btn" data-att-id="' + escapeHtml(it.id) + '" title="Delete">&times;</button>';

      if (it.is_image) {
        html += '<img class="att-thumb" src="' + escapeHtml(downloadUrl) + '" data-full-src="' + escapeHtml(downloadUrl) + '" alt="' + escapeHtml(it.filename) + '" loading="lazy">';
      } else {
        html += '<a class="att-pdf-thumb" href="' + escapeHtml(downloadUrl) + '" target="_blank" rel="noopener noreferrer" title="' + escapeHtml(it.filename) + '"><i class="bi bi-file-earmark-pdf-fill"></i></a>';
      }

      html += '<span class="att-name" title="' + escapeHtml(it.filename) + '">' + escapeHtml(it.filename) + '</span>';
      html += '</div>';
    }
    this.gallery.innerHTML = html;

    // Counter badge
    if (this.counter) {
      if (this.items.length > 0) {
        this.counter.textContent = this.items.length;
        this.counter.style.display = "";
      } else {
        this.counter.style.display = "none";
      }
    }

    // Empty message
    if (this.empty) {
      this.empty.style.display = this.items.length === 0 ? "" : "none";
    }
  };

  /* ── Update entity_id after creation (e.g. save vendor, then attach files) */
  AttBlock.prototype.setEntityId = function (newId) {
    this.entityId = newId;
    this.el.dataset.entityId = newId;
  };

  /* ── Auto-init on DOMContentLoaded ─────────────────────────────── */
  function initAllBlocks() {
    var blocks = document.querySelectorAll(".attachments-block");
    for (var i = 0; i < blocks.length; i++) {
      if (!blocks[i]._attBlock) {
        blocks[i]._attBlock = new AttBlock(blocks[i]);
      }
    }

    // Wire up modal buttons (.js-open-att-modal)
    var modalEl = document.getElementById("attachmentsModal");
    if (modalEl) {
      modalEl.addEventListener("show.bs.modal", function (event) {
        var trigger = event.relatedTarget;
        if (!trigger) return;
        var block = document.getElementById("attModalBlock");
        if (!block) return;

        block.dataset.entityType = trigger.dataset.entityType || "";
        block.dataset.entityId = trigger.dataset.entityId || "";
        block.dataset.parentId = trigger.dataset.parentId || "";

        // Re-init or reload existing block
        if (block._attBlock) {
          block._attBlock.entityType = block.dataset.entityType;
          block._attBlock.entityId = block.dataset.entityId;
          block._attBlock.parentId = block.dataset.parentId || "";
          block._attBlock.items = [];
          block._attBlock.render();
          if (block._attBlock.entityId) block._attBlock.load();
        } else {
          block._attBlock = new AttBlock(block);
        }
      });
    }
  }

  /* ── Build inline attachment block HTML for dynamic rows ──────── */
  function buildInlineBlockHtml(entityType, entityId, parentId) {
    var pid = parentId ? ' data-parent-id="' + escapeHtml(parentId) + '"' : "";
    return '<div class="attachments-block attachments-compact"' +
      ' data-entity-type="' + escapeHtml(entityType) + '"' +
      ' data-entity-id="' + escapeHtml(entityId) + '"' + pid + '>' +
      '<div class="d-flex align-items-center justify-content-between">' +
        '<h6 class="mb-0 small fw-semibold">' +
          '<i class="bi bi-paperclip me-1"></i>Attachments' +
          ' <span class="att-count badge bg-secondary ms-1" style="display:none;">0</span>' +
        '</h6>' +
        '<label class="btn btn-sm btn-outline-secondary mb-0 att-upload-btn">' +
          '<i class="bi bi-upload me-1"></i>Upload' +
          '<input type="file" class="att-file-input d-none" multiple accept="image/*,.pdf">' +
        '</label>' +
      '</div>' +
      '<div class="att-progress my-2" style="display:none;">' +
        '<div class="progress" style="height:4px;"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width:100%"></div></div>' +
        '<small class="text-muted">Uploading…</small>' +
      '</div>' +
      '<div class="att-gallery d-flex flex-wrap gap-2 mt-1"></div>' +
      '<div class="att-empty text-muted small mt-1" style="display:none;">No attachments yet.</div>' +
    '</div>';
  }

  /* Expose global helper so dynamic JS can re-init or get a block controller */
  window.AttachmentsInit = initAllBlocks;
  window.AttachmentsBuildBlock = buildInlineBlockHtml;
  window.AttachmentsGetBlock = function (el) {
    return el ? el._attBlock || null : null;
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAllBlocks);
  } else {
    initAllBlocks();
  }
})();
