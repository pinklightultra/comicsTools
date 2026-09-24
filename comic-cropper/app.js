/**
 * Comic Cropper — local-first panel cropper for penciled comic scans.
 * Vanilla JS, no build step. Project files are ZIP (.comiccrop) via JSZip.
 */
(function () {
  "use strict";

  const HANDLE_SIZE = 8;
  const MIN_CROP = 8;
  const SNAP_GRID = 8;
  const AUTOSAVE_MS = 1500;
  const DB_NAME = "comic-cropper";
  const DB_STORE = "autosave";
  const DB_KEY = "current";
  const PROJECT_VERSION = 1;

  /** @typedef {{ id: string, name: string, blob: Blob, width: number, height: number, objectUrl: string, image: HTMLImageElement, crops: Crop[] }} Page */
  /** @typedef {{ id: string, name: string, x: number, y: number, w: number, h: number }} Crop */

  const state = {
    pages: /** @type {Page[]} */ ([]),
    activePageId: /** @type {string|null} */ (null),
    selectedCropId: /** @type {string|null} */ (null),
    zoom: 1,
    panX: 0,
    panY: 0,
    spaceDown: false,
    middleDown: false,
    aspectLock: false,
    snap: false,
    interaction: /** @type {null | object} */ (null),
    projectName: "untitled",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    exportPrefs: { scale: 3, format: "png", jpegQuality: 0.92, customPercent: 300, bleed: 12 },
    dirty: false,
    recovering: false,
    soundOn: false,
    confettiOn: true,
    pulseCropId: null,
    pulseUntil: 0,
    zoomAnim: null,
    viewMode: "off", // off | blueline | contrast — display only
    bleed: 12,
    straightenAngle: 0, // live preview degrees; not baked until Apply
  };

  const el = {
    canvas: /** @type {HTMLCanvasElement} */ (document.getElementById("main-canvas")),
    wrap: document.getElementById("canvas-wrap"),
    empty: document.getElementById("empty-state"),
    pageList: document.getElementById("page-list"),
    panelList: document.getElementById("panel-list"),
    pageCount: document.getElementById("page-count"),
    panelCount: document.getElementById("panel-count"),
    pageHint: document.getElementById("page-hint"),
    previewCanvas: /** @type {HTMLCanvasElement} */ (document.getElementById("preview-canvas")),
    previewEmpty: document.getElementById("preview-empty"),
    status: document.getElementById("status-text"),
    autosaveBadge: document.getElementById("autosave-badge"),
    zoomLabel: document.getElementById("zoom-label"),
    dropOverlay: document.getElementById("drop-overlay"),
    recoverModal: document.getElementById("recover-modal"),
    recoverText: document.getElementById("recover-text"),
    fileInput: /** @type {HTMLInputElement} */ (document.getElementById("file-input")),
    fileInputEmpty: /** @type {HTMLInputElement} */ (document.getElementById("file-input-empty")),
    projectInput: /** @type {HTMLInputElement} */ (document.getElementById("project-input")),
    btnOpenProject: document.getElementById("btn-open-project"),
    btnSaveProject: document.getElementById("btn-save-project"),
    btnZoomIn: document.getElementById("btn-zoom-in"),
    btnZoomOut: document.getElementById("btn-zoom-out"),
    btnZoomFit: document.getElementById("btn-zoom-fit"),
    btnZoom100: document.getElementById("btn-zoom-100"),
    toggleAspect: /** @type {HTMLInputElement} */ (document.getElementById("toggle-aspect")),
    toggleSnap: /** @type {HTMLInputElement} */ (document.getElementById("toggle-snap")),
    btnDeleteCrop: /** @type {HTMLButtonElement} */ (document.getElementById("btn-delete-crop")),
    btnRenameCrop: /** @type {HTMLButtonElement} */ (document.getElementById("btn-rename-crop")),
    exportScale: /** @type {HTMLSelectElement} */ (document.getElementById("export-scale")),
    exportScaleCustom: /** @type {HTMLInputElement} */ (document.getElementById("export-scale-custom")),
    customScaleField: document.getElementById("custom-scale-field"),
    exportFormat: /** @type {HTMLSelectElement} */ (document.getElementById("export-format")),
    jpegQuality: /** @type {HTMLInputElement} */ (document.getElementById("export-jpeg-quality")),
    jpegQualityField: document.getElementById("jpeg-quality-field"),
    btnExportOne: /** @type {HTMLButtonElement} */ (document.getElementById("btn-export-one")),
    btnExportAll: /** @type {HTMLButtonElement} */ (document.getElementById("btn-export-all")),
    btnExportZip: /** @type {HTMLButtonElement} */ (document.getElementById("btn-export-zip")),
    btnRecoverYes: document.getElementById("btn-recover-yes"),
    btnRecoverNo: document.getElementById("btn-recover-no"),
    toastStack: document.getElementById("toast-stack"),
    inkReady: document.getElementById("ink-ready"),
    toggleSfx: /** @type {HTMLInputElement} */ (document.getElementById("toggle-sfx")),
    toggleConfetti: /** @type {HTMLInputElement} */ (document.getElementById("toggle-confetti")),
    fxCanvas: /** @type {HTMLCanvasElement} */ (document.getElementById("fx-canvas")),
    viewMode: /** @type {HTMLSelectElement} */ (document.getElementById("view-mode")),
    exportBleed: /** @type {HTMLInputElement} */ (document.getElementById("export-bleed")),
    bleedReadout: document.getElementById("bleed-readout"),
    btnContactSheet: /** @type {HTMLButtonElement} */ (document.getElementById("btn-contact-sheet")),
    btnRotateCw: document.getElementById("btn-rotate-cw"),
    btnRotateCcw: document.getElementById("btn-rotate-ccw"),
    straightenAngle: /** @type {HTMLInputElement} */ (document.getElementById("straighten-angle")),
    straightenReadout: document.getElementById("straighten-readout"),
    btnApplyStraighten: /** @type {HTMLButtonElement} */ (document.getElementById("btn-apply-straighten")),
    btnResetStraighten: /** @type {HTMLButtonElement} */ (document.getElementById("btn-reset-straighten")),
  };

  const ctx = el.canvas.getContext("2d");
  const previewCtx = el.previewCanvas.getContext("2d");

  // —— Utils ——
  function uid(prefix) {
    return prefix + "_" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
  }

  function setStatus(msg) {
    el.status.textContent = msg;
    el.status.classList.remove("is-pop");
    void el.status.offsetWidth;
    el.status.classList.add("is-pop");
  }

  function toast(title, sub, kind) {
    if (!el.toastStack) return;
    const node = document.createElement("div");
    node.className = "toast" + (kind ? " toast-" + kind : "");
    node.innerHTML =
      '<span class="toast-title">' + escapeHtml(title) + "</span>" +
      (sub ? '<span class="toast-sub">' + escapeHtml(sub) + "</span>" : "");
    el.toastStack.appendChild(node);
    const ttl = 2600;
    setTimeout(() => {
      node.classList.add("toast-out");
      setTimeout(() => node.remove(), 280);
    }, ttl);
  }

  let audioCtx = null;
  function playClick() {
    if (!state.soundOn) return;
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const t0 = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(520, t0);
      osc.frequency.exponentialRampToValueAtTime(240, t0 + 0.07);
      gain.gain.setValueAtTime(0.0001, t0);
      gain.gain.exponentialRampToValueAtTime(0.05, t0 + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.09);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(t0);
      osc.stop(t0 + 0.1);
    } catch (_) { /* ignore */ }
  }

  const fxParticles = [];
  let fxRaf = 0;

  function resizeFx() {
    if (!el.fxCanvas || !el.wrap) return;
    const rect = el.wrap.getBoundingClientRect();
    el.fxCanvas.width = Math.max(1, Math.floor(rect.width));
    el.fxCanvas.height = Math.max(1, Math.floor(rect.height));
  }

  function burstFlourish(cx, cy) {
    if (!state.confettiOn) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    resizeFx();
    const colors = ["#ff6b5a", "#5eb0ff", "#f3efe6", "#7fd99a", "#ffb454"];
    const n = 28;
    for (let i = 0; i < n; i++) {
      const ang = (Math.PI * 2 * i) / n + Math.random() * 0.4;
      const spd = 2.2 + Math.random() * 4.5;
      fxParticles.push({
        x: cx,
        y: cy,
        vx: Math.cos(ang) * spd,
        vy: Math.sin(ang) * spd - 1.5,
        life: 1,
        decay: 0.016 + Math.random() * 0.02,
        size: 2 + Math.random() * 3.5,
        color: colors[i % colors.length],
        kind: i % 3 === 0 ? "splash" : "dot",
      });
    }
    if (!fxRaf) fxRaf = requestAnimationFrame(tickFx);
  }

  function tickFx() {
    fxRaf = 0;
    const c = el.fxCanvas && el.fxCanvas.getContext("2d");
    if (!c) return;
    c.clearRect(0, 0, el.fxCanvas.width, el.fxCanvas.height);
    for (let i = fxParticles.length - 1; i >= 0; i--) {
      const p = fxParticles[i];
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.12;
      p.life -= p.decay;
      if (p.life <= 0) {
        fxParticles.splice(i, 1);
        continue;
      }
      c.globalAlpha = Math.max(0, p.life);
      c.fillStyle = p.color;
      if (p.kind === "splash") {
        c.beginPath();
        c.ellipse(p.x, p.y, p.size * 1.8, p.size * 0.7, p.vx * 0.2, 0, Math.PI * 2);
        c.fill();
      } else {
        c.fillRect(p.x, p.y, p.size, p.size);
      }
    }
    c.globalAlpha = 1;
    if (fxParticles.length) fxRaf = requestAnimationFrame(tickFx);
  }

  function pulseCrop(cropId) {
    state.pulseCropId = cropId;
    state.pulseUntil = performance.now() + 420;
    playClick();
    const tick = () => {
      render();
      if (performance.now() < state.pulseUntil) requestAnimationFrame(tick);
      else {
        state.pulseCropId = null;
        render();
      }
    };
    requestAnimationFrame(tick);
  }

  function totalPanelCount() {
    return state.pages.reduce((n, p) => n + p.crops.length, 0);
  }

  function updateInkReady() {
    if (!el.inkReady) return;
    const n = totalPanelCount();
    if (n <= 0) {
      el.inkReady.hidden = true;
      el.inkReady.textContent = "";
      return;
    }
    el.inkReady.hidden = false;
    el.inkReady.textContent =
      n === 1 ? "1 panel ready to ink" : n + " panels ready to ink";
  }

  function markDirty() {
    state.dirty = true;
    state.updatedAt = new Date().toISOString();
    scheduleAutosave();
  }

  function activePage() {
    return state.pages.find((p) => p.id === state.activePageId) || null;
  }

  function selectedCrop() {
    const page = activePage();
    if (!page || !state.selectedCropId) return null;
    return page.crops.find((c) => c.id === state.selectedCropId) || null;
  }

  function clamp(n, a, b) {
    return Math.max(a, Math.min(b, n));
  }

  function snapVal(v, enabled) {
    if (!enabled) return v;
    return Math.round(v / SNAP_GRID) * SNAP_GRID;
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function sanitizeFilename(name) {
    return String(name || "panel")
      .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
      .replace(/\s+/g, "_")
      .slice(0, 120);
  }

  // —— Coordinate transforms (image pixels ↔ screen) ——
  function imageToScreen(ix, iy) {
    return {
      x: ix * state.zoom + state.panX,
      y: iy * state.zoom + state.panY,
    };
  }

  function screenToImage(sx, sy) {
    return {
      x: (sx - state.panX) / state.zoom,
      y: (sy - state.panY) / state.zoom,
    };
  }

  function getCanvasPoint(e) {
    const rect = el.canvas.getBoundingClientRect();
    return {
      x: ((e.clientX - rect.left) / rect.width) * el.canvas.width,
      y: ((e.clientY - rect.top) / rect.height) * el.canvas.height,
    };
  }

  // —— Zoom / pan ——
  function fitToView() {
    const page = activePage();
    if (!page) return;
    const pad = 40;
    const cw = el.canvas.width;
    const ch = el.canvas.height;
    const zx = (cw - pad * 2) / page.width;
    const zy = (ch - pad * 2) / page.height;
    state.zoom = Math.max(0.05, Math.min(zx, zy, 8));
    state.panX = (cw - page.width * state.zoom) / 2;
    state.panY = (ch - page.height * state.zoom) / 2;
    updateZoomLabel();
    render();
  }

  function setZoomAt(newZoom, screenX, screenY, immediate) {
    if (immediate) {
      const before = screenToImage(screenX, screenY);
      state.zoom = clamp(newZoom, 0.05, 16);
      const after = imageToScreen(before.x, before.y);
      state.panX += screenX - after.x;
      state.panY += screenY - after.y;
      updateZoomLabel();
      render();
      return;
    }
    animateZoomTo(clamp(newZoom, 0.05, 16), screenX, screenY);
  }

  function animateZoomTo(targetZoom, screenX, screenY) {
    if (state.zoomAnim) cancelAnimationFrame(state.zoomAnim);
    const startZoom = state.zoom;
    const startPanX = state.panX;
    const startPanY = state.panY;
    const imgAnchor = {
      x: (screenX - startPanX) / startZoom,
      y: (screenY - startPanY) / startZoom,
    };
    const t0 = performance.now();
    const dur = 140;
    function step(now) {
      const t = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - t, 3);
      state.zoom = startZoom + (targetZoom - startZoom) * e;
      state.panX = screenX - imgAnchor.x * state.zoom;
      state.panY = screenY - imgAnchor.y * state.zoom;
      updateZoomLabel();
      render();
      if (t < 1) state.zoomAnim = requestAnimationFrame(step);
      else state.zoomAnim = null;
    }
    state.zoomAnim = requestAnimationFrame(step);
  }

  function updateZoomLabel() {
    el.zoomLabel.textContent = Math.round(state.zoom * 100) + "%";
  }

  function resizeCanvas() {
    const rect = el.wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    // Use CSS pixel coords for interaction simplicity (1 canvas unit = 1 CSS px)
    el.canvas.width = Math.max(1, Math.floor(rect.width));
    el.canvas.height = Math.max(1, Math.floor(rect.height));
    // Keep drawing crisp on hi-DPI by scaling context if needed — but we sized in CSS px
    // intentionally so hit-testing stays simple. Image itself is drawn at device pixels via zoom.
    void dpr;
    render();
  }

  // —— Display filters (never baked into exports) ——
  function viewFilterCss() {
    if (state.viewMode === "blueline") {
      // Non-photo blue: graphite → cool cyan-blue, paper stays light
      return "contrast(1.2) brightness(1.06) grayscale(1) sepia(1) hue-rotate(175deg) saturate(6)";
    }
    if (state.viewMode === "contrast") {
      return "contrast(1.55) brightness(1.1) saturate(0.25)";
    }
    return "none";
  }

  // —— Rendering ——
  function render() {
    const w = el.canvas.width;
    const h = el.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#121316";
    ctx.fillRect(0, 0, w, h);

    const page = activePage();
    if (!page || !page.image) {
      updateUI();
      return;
    }

    const dest = imageToScreen(0, 0);
    const dw = page.width * state.zoom;
    const dh = page.height * state.zoom;
    const angle = state.straightenAngle || 0;
    const previewStraighten = Math.abs(angle) > 0.001;

    ctx.imageSmoothingEnabled = state.zoom < 1 || previewStraighten;
    ctx.imageSmoothingQuality = "high";
    ctx.save();
    ctx.filter = viewFilterCss();
    if (previewStraighten) {
      const cx = dest.x + dw / 2;
      const cy = dest.y + dh / 2;
      ctx.translate(cx, cy);
      ctx.rotate((angle * Math.PI) / 180);
      ctx.translate(-cx, -cy);
      // Soft paper pad so rotated corners aren't clipped into void
      ctx.fillStyle = "#e8e2d6";
      ctx.fillRect(dest.x - 40, dest.y - 40, dw + 80, dh + 80);
    }
    ctx.drawImage(page.image, dest.x, dest.y, dw, dh);
    ctx.restore();

    // When straighten preview is active, skip crop overlays (about to bake/clear)
    if (!previewStraighten) {
      // Dim outside crops (even-odd path)
      if (page.crops.length > 0) {
        ctx.save();
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        ctx.beginPath();
        ctx.rect(dest.x, dest.y, dw, dh);
        for (const c of page.crops) {
          const p = imageToScreen(c.x, c.y);
          ctx.rect(p.x, p.y, c.w * state.zoom, c.h * state.zoom);
        }
        ctx.fill("evenodd");
        ctx.restore();
      }

      // Crop rectangles (+ bleed outline when selected)
      page.crops.forEach((c, i) => {
        drawCrop(c, i + 1, c.id === state.selectedCropId);
      });

      // In-progress draw
      if (state.interaction && state.interaction.type === "draw") {
        const r = normalizeRect(
          state.interaction.startX,
          state.interaction.startY,
          state.interaction.curX,
          state.interaction.curY
        );
        drawCropRect(r.x, r.y, r.w, r.h, true, null);
      }
    } else {
      ctx.save();
      ctx.fillStyle = "rgba(255,107,90,0.12)";
      ctx.fillRect(0, 0, w, 28);
      ctx.fillStyle = "#f3efe6";
      ctx.font = "12px Segoe UI, system-ui, sans-serif";
      ctx.textBaseline = "middle";
      ctx.fillText("Straighten preview — Apply to bake (crops will be cleared)", 12, 14);
      ctx.restore();
    }

    updateUI();
    renderPreview();
  }

  function drawCrop(crop, number, selected) {
    drawCropRect(crop.x, crop.y, crop.w, crop.h, selected, number, crop.name);
  }

  function drawCropRect(ix, iy, iw, ih, selected, number, name) {
    const p = imageToScreen(ix, iy);
    const w = iw * state.zoom;
    const h = ih * state.zoom;

    ctx.save();
    ctx.strokeStyle = selected ? "#9ed0ff" : "#5eb0ff";
    ctx.lineWidth = selected ? 2.5 : 1.5;
    ctx.setLineDash([]);
    ctx.strokeRect(p.x + 0.5, p.y + 0.5, w, h);

    // Soft bleed outline (dashed outer rect) when bleed > 0
    if (selected && state.bleed > 0) {
      const page = activePage();
      if (page) {
        const br = expandBleed(ix, iy, iw, ih, state.bleed, page.width, page.height);
        const bp = imageToScreen(br.x, br.y);
        const bw = br.w * state.zoom;
        const bh = br.h * state.zoom;
        ctx.save();
        ctx.strokeStyle = "rgba(255,180,84,0.85)";
        ctx.lineWidth = 1.25;
        ctx.setLineDash([5, 4]);
        ctx.strokeRect(bp.x + 0.5, bp.y + 0.5, bw, bh);
        ctx.restore();
      }
    }

    // Finish pulse
    if (state.pulseCropId && number != null) {
      const page = activePage();
      const crop = page && page.crops.find((c) => c.id === state.pulseCropId);
      if (crop && Math.abs(crop.x - ix) < 0.01 && Math.abs(crop.y - iy) < 0.01) {
        const t = Math.max(0, (state.pulseUntil - performance.now()) / 420);
        const glow = 0.15 + 0.35 * t;
        ctx.save();
        ctx.strokeStyle = "rgba(255,107,90," + (0.25 + 0.55 * t) + ")";
        ctx.lineWidth = 3 + 6 * t;
        ctx.strokeRect(p.x - 2 * t, p.y - 2 * t, w + 4 * t, h + 4 * t);
        ctx.fillStyle = "rgba(255,107,90," + glow * 0.35 + ")";
        ctx.fillRect(p.x, p.y, w, h);
        ctx.restore();
      }
    }

    if (selected) {
      ctx.fillStyle = "rgba(94,176,255,0.12)";
      ctx.fillRect(p.x, p.y, w, h);
      // Handles
      const handles = getHandlePoints(ix, iy, iw, ih);
      for (const hp of Object.values(handles)) {
        const s = imageToScreen(hp.x, hp.y);
        ctx.fillStyle = "#fff";
        ctx.strokeStyle = "#ff6b5a";
        ctx.lineWidth = 1;
        ctx.fillRect(s.x - HANDLE_SIZE / 2, s.y - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
        ctx.strokeRect(s.x - HANDLE_SIZE / 2, s.y - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
      }
    }

    if (number != null) {
      const label = String(number);
      const padX = 6;
      ctx.font = "bold 12px ui-monospace, SF Mono, Menlo, Consolas, monospace";
      const tw = ctx.measureText(label).width;
      const lx = p.x + 4;
      const ly = p.y + 4;
      const bw = tw + padX * 2;
      const bh = 18;
      // Caption-box badge
      ctx.fillStyle = "#f3efe6";
      ctx.fillRect(lx, ly, bw, bh);
      ctx.fillStyle = "#1a1512";
      ctx.fillRect(lx, ly + bh, 5, 4);
      ctx.fillStyle = "#1a1512";
      ctx.textBaseline = "middle";
      ctx.fillText(label, lx + padX, ly + 9);

      if (name && state.zoom > 0.35) {
        ctx.font = "11px Segoe UI, system-ui, sans-serif";
        const nw = ctx.measureText(name).width;
        const nx = lx + bw + 4;
        ctx.fillStyle = "rgba(22,18,28,0.82)";
        roundRect(ctx, nx, ly, nw + 10, 18, 4);
        ctx.fill();
        ctx.fillStyle = "#f3efe6";
        ctx.fillText(name, nx + 5, ly + 9);
      }
    }
    ctx.restore();
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  function getHandlePoints(x, y, w, h) {
    return {
      nw: { x, y },
      n: { x: x + w / 2, y },
      ne: { x: x + w, y },
      e: { x: x + w, y: y + h / 2 },
      se: { x: x + w, y: y + h },
      s: { x: x + w / 2, y: y + h },
      sw: { x, y: y + h },
      w: { x, y: y + h / 2 },
    };
  }

  function hitTestHandle(crop, ix, iy) {
    const threshold = (HANDLE_SIZE + 4) / state.zoom;
    const handles = getHandlePoints(crop.x, crop.y, crop.w, crop.h);
    for (const [name, hp] of Object.entries(handles)) {
      if (Math.abs(ix - hp.x) <= threshold && Math.abs(iy - hp.y) <= threshold) {
        return name;
      }
    }
    return null;
  }

  function hitTestCrop(page, ix, iy) {
    // Top-most first
    for (let i = page.crops.length - 1; i >= 0; i--) {
      const c = page.crops[i];
      if (ix >= c.x && iy >= c.y && ix <= c.x + c.w && iy <= c.y + c.h) {
        return c;
      }
    }
    return null;
  }

  function normalizeRect(x0, y0, x1, y1) {
    const x = Math.min(x0, x1);
    const y = Math.min(y0, y1);
    return { x, y, w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) };
  }

  // —— Preview ——
  function renderPreview() {
    const crop = selectedCrop();
    const page = activePage();
    if (!crop || !page) {
      el.previewCanvas.width = 1;
      el.previewCanvas.height = 1;
      el.previewEmpty.hidden = false;
      return;
    }
    el.previewEmpty.hidden = true;
    const br = expandBleed(crop.x, crop.y, crop.w, crop.h, state.bleed, page.width, page.height);
    const maxW = 248;
    const maxH = 220;
    const scale = Math.min(maxW / br.w, maxH / br.h, 1);
    const dw = Math.max(1, Math.round(br.w * scale));
    const dh = Math.max(1, Math.round(br.h * scale));
    el.previewCanvas.width = dw;
    el.previewCanvas.height = dh;
    previewCtx.imageSmoothingEnabled = true;
    previewCtx.imageSmoothingQuality = "high";
    previewCtx.clearRect(0, 0, dw, dh);
    // Show bleed area; draw inner crop guide if bleed > 0
    previewCtx.drawImage(page.image, br.x, br.y, br.w, br.h, 0, 0, dw, dh);
    if (state.bleed > 0) {
      const ix = (crop.x - br.x) * scale;
      const iy = (crop.y - br.y) * scale;
      const iw = crop.w * scale;
      const ih = crop.h * scale;
      previewCtx.strokeStyle = "rgba(94,176,255,0.9)";
      previewCtx.lineWidth = 1;
      previewCtx.setLineDash([3, 2]);
      previewCtx.strokeRect(ix + 0.5, iy + 0.5, Math.max(1, iw - 1), Math.max(1, ih - 1));
      previewCtx.setLineDash([]);
    }
  }

  function expandBleed(x, y, w, h, bleed, pageW, pageH) {
    const b = Math.max(0, Number(bleed) || 0);
    const nx = Math.max(0, x - b);
    const ny = Math.max(0, y - b);
    const x2 = Math.min(pageW, x + w + b);
    const y2 = Math.min(pageH, y + h + b);
    return { x: nx, y: ny, w: Math.max(1, x2 - nx), h: Math.max(1, y2 - ny) };
  }

  // —— UI lists ——
  function updateUI() {
    const hasPages = state.pages.length > 0;
    el.empty.hidden = hasPages;
    el.pageHint.hidden = hasPages;
    el.pageCount.textContent = String(state.pages.length);

    const page = activePage();
    const crops = page ? page.crops : [];
    el.panelCount.textContent = String(crops.length);

    el.btnExportOne.disabled = !selectedCrop();
    el.btnExportAll.disabled = !page || crops.length === 0;
    el.btnExportZip.disabled = state.pages.every((p) => p.crops.length === 0);
    if (el.btnContactSheet) {
      el.btnContactSheet.disabled = state.pages.every((p) => p.crops.length === 0);
    }
    el.btnDeleteCrop.disabled = !selectedCrop();
    el.btnRenameCrop.disabled = !selectedCrop();
    const hasPage = !!page;
    if (el.btnRotateCw) el.btnRotateCw.disabled = !hasPage;
    if (el.btnRotateCcw) el.btnRotateCcw.disabled = !hasPage;
    if (el.straightenAngle) el.straightenAngle.disabled = !hasPage;
    updateStraightenButtons();
    updateInkReady();

    // Pages
    el.pageList.innerHTML = "";
    state.pages.forEach((p, idx) => {
      const li = document.createElement("li");
      if (p.id === state.activePageId) li.classList.add("active");
      const img = document.createElement("img");
      img.className = "page-thumb";
      img.src = p.objectUrl;
      img.alt = "";
      const meta = document.createElement("div");
      meta.className = "page-meta";
      meta.innerHTML =
        `<span class="name">${escapeHtml(p.name)}</span>` +
        `<span class="sub">${p.width}×${p.height} · ${p.crops.length} panel${p.crops.length === 1 ? "" : "s"}</span>`;
      li.appendChild(img);
      li.appendChild(meta);
      li.addEventListener("click", () => selectPage(p.id));
      li.title = `Page ${idx + 1}: ${p.name}`;
      el.pageList.appendChild(li);
    });

    // Panels
    el.panelList.innerHTML = "";
    crops.forEach((c, i) => {
      const li = document.createElement("li");
      if (c.id === state.selectedCropId) li.classList.add("active");
      const num = document.createElement("span");
      num.className = "panel-num";
      num.textContent = String(i + 1);
      const meta = document.createElement("div");
      meta.className = "panel-meta";
      meta.innerHTML =
        `<span class="name">${escapeHtml(c.name)}</span>` +
        `<span class="sub">${Math.round(c.w)}×${Math.round(c.h)} px</span>`;
      li.appendChild(num);
      li.appendChild(meta);
      li.addEventListener("click", () => {
        state.selectedCropId = c.id;
        render();
      });
      li.addEventListener("dblclick", () => renameSelectedCrop());
      el.panelList.appendChild(li);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function selectPage(id) {
    if (state.activePageId === id) return;
    state.activePageId = id;
    state.selectedCropId = null;
    if (Math.abs(state.straightenAngle || 0) > 0.001) setStraightenAngle(0);
    requestAnimationFrame(() => {
      fitToView();
      setStatus("Page on deck");
    });
  }

  // —— Import ——
  async function importFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;

    const projectFiles = files.filter((f) => /\.comiccrop$/i.test(f.name) || (f.name.toLowerCase().endsWith(".zip") && f.name.toLowerCase().includes("comic")));
    const pdfFiles = files.filter((f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name));
    const imageFiles = files.filter((f) => /^image\//.test(f.type) || /\.(png|jpe?g|webp|gif|tiff?)$/i.test(f.name));

    if (projectFiles.length) {
      await openProjectFile(projectFiles[0]);
      return;
    }

    if (!imageFiles.length && !pdfFiles.length) {
      setStatus("Those files shrugged at us");
      toast("Hmm, nothing loadable", "Try PNG, JPG, WEBP, or PDF.", "info");
      return;
    }

    let imported = 0;
    for (const file of imageFiles) {
      try {
        await addPageFromFile(file);
        imported++;
      } catch (err) {
        console.error(err);
        setStatus("Failed to load " + file.name);
      }
    }
    for (const file of pdfFiles) {
      try {
        const n = await addPagesFromPdf(file);
        imported += n;
      } catch (err) {
        console.error(err);
        const msg = (err && err.message) || String(err);
        if (/password/i.test(msg)) {
          toast("Password-protected PDF", "Unlock it elsewhere, then drop the pages.", "info");
          setStatus("PDF needs a password");
        } else {
          toast("PDF import failed", file.name + " — " + msg.slice(0, 80), "info");
          setStatus("Failed to load " + file.name);
        }
      }
    }

    if (imported) {
      if (!state.activePageId) selectPage(state.pages[0].id);
      else fitToView();
      markDirty();
      setStatus(imported === 1 ? "Scan on the desk" : imported + " scans on the desk");
      toast(imported === 1 ? "Scan landed" : imported + " scans landed", "Draw boxes around the tiny panels.", "ok");
    }
  }

  async function addPagesFromPdf(file) {
    if (typeof pdfjsLib === "undefined") {
      throw new Error("PDF.js not loaded — check CDN");
    }
    const data = await file.arrayBuffer();
    let pdf;
    try {
      pdf = await pdfjsLib.getDocument({ data, password: "" }).promise;
    } catch (err) {
      const name = (err && (err.name || err.message)) || "";
      if (/PasswordException|password/i.test(name) || (err && err.code === 1)) {
        const e = new Error("Password-protected PDF");
        e.cause = err;
        throw e;
      }
      throw err;
    }
    const base = file.name.replace(/\.pdf$/i, "") || "pdf";
    let added = 0;
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      // Target ~150–200 DPI-ish for comics; scale from 72pt
      const scale = 2;
      const viewport = page.getViewport({ scale });
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      const c = canvas.getContext("2d");
      await page.render({ canvasContext: c, viewport }).promise;
      const blob = await canvasToBlob(canvas, "image/png");
      const name = pdf.numPages > 1 ? base + "-p" + i : base;
      await addPageFromBlob(blob, name);
      added++;
    }
    return added;
  }

  async function addPageFromBlob(blob, name) {
    const { img, url } = await loadImageFromBlob(blob);
    const page = {
      id: uid("page"),
      name: name || "page",
      blob,
      width: img.naturalWidth,
      height: img.naturalHeight,
      objectUrl: url,
      image: img,
      crops: [],
    };
    state.pages.push(page);
    if (!state.activePageId) {
      state.activePageId = page.id;
      requestAnimationFrame(fitToView);
    }
    return page;
  }

  function loadImageFromBlob(blob) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => resolve({ img, url });
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("Image decode failed"));
      };
      img.src = url;
    });
  }

  async function addPageFromFile(file) {
    const base = file.name.replace(/\.[^.]+$/, "") || "page";
    return addPageFromBlob(file, base);
  }

  function nextPanelName(page) {
    const pageIndex = state.pages.indexOf(page) + 1;
    const n = page.crops.length + 1;
    return `page${pageIndex}-panel${n}`;
  }

  // —— Crop mutations ——
  function addCrop(page, x, y, w, h) {
    const crop = {
      id: uid("crop"),
      name: nextPanelName(page),
      x,
      y,
      w,
      h,
    };
    page.crops.push(crop);
    state.selectedCropId = crop.id;
    markDirty();
    pulseCrop(crop.id);
    const lines = ["Panel nabbed", "Framed it", "Got that beat", "Crop locked"];
    toast(lines[page.crops.length % lines.length], crop.name, "ok");
    setStatus("Panel nabbed — " + crop.name);
    return crop;
  }

  function deleteSelectedCrop() {
    const page = activePage();
    const crop = selectedCrop();
    if (!page || !crop) return;
    page.crops = page.crops.filter((c) => c.id !== crop.id);
    state.selectedCropId = null;
    markDirty();
    setStatus("Panel ditched");
    toast("Panel ditched", "Back to the cutting room floor.", "info");
    render();
  }

  function renameSelectedCrop() {
    const crop = selectedCrop();
    if (!crop) return;
    const next = window.prompt("Panel name", crop.name);
    if (next == null) return;
    const trimmed = next.trim();
    if (!trimmed) return;
    crop.name = trimmed;
    markDirty();
    render();
  }

  function constrainCrop(page, crop) {
    crop.w = Math.max(MIN_CROP, crop.w);
    crop.h = Math.max(MIN_CROP, crop.h);
    crop.x = clamp(crop.x, 0, page.width - crop.w);
    crop.y = clamp(crop.y, 0, page.height - crop.h);
    crop.w = Math.min(crop.w, page.width - crop.x);
    crop.h = Math.min(crop.h, page.height - crop.y);
  }

  // —— Pointer interactions ——
  function onPointerDown(e) {
    if (!activePage()) return;
    if (e.button === 1 || (e.button === 0 && state.spaceDown)) {
      state.interaction = {
        type: "pan",
        lastX: e.clientX,
        lastY: e.clientY,
      };
      el.wrap.classList.add("is-dragging", "panning");
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;

    el.canvas.focus({ preventScroll: true });
    const pt = getCanvasPoint(e);
    const imgPt = screenToImage(pt.x, pt.y);
    const page = activePage();

    // Outside image → deselect
    if (imgPt.x < 0 || imgPt.y < 0 || imgPt.x > page.width || imgPt.y > page.height) {
      state.selectedCropId = null;
      render();
      return;
    }

    const selected = selectedCrop();
    if (selected) {
      const handle = hitTestHandle(selected, imgPt.x, imgPt.y);
      if (handle) {
        state.interaction = {
          type: "resize",
          cropId: selected.id,
          handle,
          startCrop: { ...selected },
          startX: imgPt.x,
          startY: imgPt.y,
          aspect: selected.w / selected.h,
        };
        e.preventDefault();
        return;
      }
    }

    const hit = hitTestCrop(page, imgPt.x, imgPt.y);
    if (hit) {
      state.selectedCropId = hit.id;
      state.interaction = {
        type: "move",
        cropId: hit.id,
        offsetX: imgPt.x - hit.x,
        offsetY: imgPt.y - hit.y,
      };
      render();
      e.preventDefault();
      return;
    }

    // Start drawing
    state.selectedCropId = null;
    let sx = snapVal(imgPt.x, state.snap);
    let sy = snapVal(imgPt.y, state.snap);
    state.interaction = {
      type: "draw",
      startX: sx,
      startY: sy,
      curX: sx,
      curY: sy,
      aspect: state.aspectLock ? 1 : null,
    };
    render();
    e.preventDefault();
  }

  function onPointerMove(e) {
    const inter = state.interaction;
    if (!inter) {
      updateCursor(e);
      return;
    }

    if (inter.type === "pan") {
      state.panX += e.clientX - inter.lastX;
      state.panY += e.clientY - inter.lastY;
      inter.lastX = e.clientX;
      inter.lastY = e.clientY;
      render();
      return;
    }

    const page = activePage();
    if (!page) return;
    const pt = getCanvasPoint(e);
    const imgPt = screenToImage(pt.x, pt.y);

    if (inter.type === "draw") {
      let cx = snapVal(imgPt.x, state.snap);
      let cy = snapVal(imgPt.y, state.snap);
      if (state.aspectLock || e.shiftKey) {
        const dx = cx - inter.startX;
        const dy = cy - inter.startY;
        const side = Math.max(Math.abs(dx), Math.abs(dy));
        cx = inter.startX + Math.sign(dx || 1) * side;
        cy = inter.startY + Math.sign(dy || 1) * side;
      }
      inter.curX = clamp(cx, 0, page.width);
      inter.curY = clamp(cy, 0, page.height);
      render();
      return;
    }

    if (inter.type === "move") {
      const crop = page.crops.find((c) => c.id === inter.cropId);
      if (!crop) return;
      let nx = imgPt.x - inter.offsetX;
      let ny = imgPt.y - inter.offsetY;
      nx = snapVal(nx, state.snap);
      ny = snapVal(ny, state.snap);
      crop.x = nx;
      crop.y = ny;
      constrainCrop(page, crop);
      render();
      return;
    }

    if (inter.type === "resize") {
      const crop = page.crops.find((c) => c.id === inter.cropId);
      if (!crop) return;
      resizeCropFromHandle(page, crop, inter, imgPt, e.shiftKey || state.aspectLock);
      render();
    }
  }

  function resizeCropFromHandle(page, crop, inter, imgPt, lockAspect) {
    const start = inter.startCrop;
    let x1 = start.x;
    let y1 = start.y;
    let x2 = start.x + start.w;
    let y2 = start.y + start.h;
    let mx = snapVal(imgPt.x, state.snap);
    let my = snapVal(imgPt.y, state.snap);
    mx = clamp(mx, 0, page.width);
    my = clamp(my, 0, page.height);

    const h = inter.handle;
    if (h.includes("w")) x1 = mx;
    if (h.includes("e")) x2 = mx;
    if (h.includes("n")) y1 = my;
    if (h.includes("s")) y2 = my;

    // Corner / edge aspect lock
    if (lockAspect && inter.aspect) {
      const aspect = inter.aspect;
      if (h === "e" || h === "w") {
        const w = Math.abs(x2 - x1);
        const cy = start.y + start.h / 2;
        const nh = w / aspect;
        y1 = cy - nh / 2;
        y2 = cy + nh / 2;
      } else if (h === "n" || h === "s") {
        const ht = Math.abs(y2 - y1);
        const cx = start.x + start.w / 2;
        const nw = ht * aspect;
        x1 = cx - nw / 2;
        x2 = cx + nw / 2;
      } else {
        // corners: dominate by the larger delta
        const w = Math.abs(x2 - x1);
        const ht = w / aspect;
        if (h.includes("n")) y1 = y2 - Math.sign(y2 - start.y - start.h / 2 || 1) * ht;
        // simpler: fix opposite corner
        const fixedX = h.includes("w") ? start.x + start.w : start.x;
        const fixedY = h.includes("n") ? start.y + start.h : start.y;
        let nw = Math.abs(mx - fixedX);
        let nh = nw / aspect;
        if (Math.abs(my - fixedY) / aspect > nw) {
          nh = Math.abs(my - fixedY);
          nw = nh * aspect;
        }
        x1 = h.includes("w") ? fixedX - nw : fixedX;
        x2 = h.includes("w") ? fixedX : fixedX + nw;
        y1 = h.includes("n") ? fixedY - nh : fixedY;
        y2 = h.includes("n") ? fixedY : fixedY + nh;
      }
    }

    const r = normalizeRect(x1, y1, x2, y2);
    crop.x = r.x;
    crop.y = r.y;
    crop.w = r.w;
    crop.h = r.h;
    constrainCrop(page, crop);
  }

  function onPointerUp(e) {
    const inter = state.interaction;
    if (!inter) return;

    if (inter.type === "pan") {
      state.interaction = null;
      el.wrap.classList.remove("is-dragging");
      updatePanClass();
      return;
    }

    const page = activePage();
    if (inter.type === "draw" && page) {
      const r = normalizeRect(inter.startX, inter.startY, inter.curX, inter.curY);
      state.interaction = null;
      if (r.w >= MIN_CROP && r.h >= MIN_CROP) {
        r.x = clamp(r.x, 0, page.width);
        r.y = clamp(r.y, 0, page.height);
        r.w = Math.min(r.w, page.width - r.x);
        r.h = Math.min(r.h, page.height - r.y);
        addCrop(page, r.x, r.y, r.w, r.h);
        }
      render();
      return;
    }

    if (inter.type === "move" || inter.type === "resize") {
      state.interaction = null;
      markDirty();
      render();
    }
  }

  function updateCursor(e) {
    if (state.spaceDown || state.middleDown) {
      el.wrap.style.cursor = "grab";
      return;
    }
    const page = activePage();
    if (!page) {
      el.wrap.style.cursor = "default";
      return;
    }
    const pt = getCanvasPoint(e);
    const imgPt = screenToImage(pt.x, pt.y);
    const selected = selectedCrop();
    if (selected) {
      const handle = hitTestHandle(selected, imgPt.x, imgPt.y);
      if (handle) {
        const map = {
          n: "ns-resize",
          s: "ns-resize",
          e: "ew-resize",
          w: "ew-resize",
          ne: "nesw-resize",
          sw: "nesw-resize",
          nw: "nwse-resize",
          se: "nwse-resize",
        };
        el.wrap.style.cursor = map[handle] || "crosshair";
        return;
      }
    }
    if (hitTestCrop(page, imgPt.x, imgPt.y)) {
      el.wrap.style.cursor = "move";
      return;
    }
    el.wrap.style.cursor = "crosshair";
  }

  function updatePanClass() {
    el.wrap.classList.toggle("space-pan", state.spaceDown);
    el.wrap.classList.toggle("panning", state.spaceDown || state.middleDown);
  }

  function onWheel(e) {
    if (!activePage()) return;
    e.preventDefault();
    const pt = getCanvasPoint(e);
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    setZoomAt(state.zoom * factor, pt.x, pt.y, true);
  }

  // —— Rotate / straighten ——
  function updateStraightenButtons() {
    const active = Math.abs(state.straightenAngle || 0) > 0.001;
    if (el.btnApplyStraighten) el.btnApplyStraighten.disabled = !activePage() || !active;
    if (el.btnResetStraighten) el.btnResetStraighten.disabled = !active;
  }

  function setStraightenAngle(deg) {
    state.straightenAngle = clamp(Number(deg) || 0, -15, 15);
    if (el.straightenAngle) el.straightenAngle.value = String(state.straightenAngle);
    if (el.straightenReadout) {
      const v = Math.round(state.straightenAngle * 10) / 10;
      el.straightenReadout.textContent = (v > 0 ? "+" : "") + v + "°";
    }
    updateStraightenButtons();
    render();
  }

  function resetStraightenPreview() {
    setStraightenAngle(0);
    setStatus("Straighten reset");
  }

  /** Transform axis-aligned crop for 90° CW page rotation (oldW×oldH → oldH×oldW). */
  function transformCropCw90(crop, oldW, oldH) {
    return {
      id: crop.id,
      name: crop.name,
      x: oldH - crop.y - crop.h,
      y: crop.x,
      w: crop.h,
      h: crop.w,
    };
  }

  /** Transform axis-aligned crop for 90° CCW. */
  function transformCropCcw90(crop, oldW, oldH) {
    return {
      id: crop.id,
      name: crop.name,
      x: crop.y,
      y: oldW - crop.x - crop.w,
      w: crop.h,
      h: crop.w,
    };
  }

  async function bakePageBitmap(page, drawFn) {
    const canvas = document.createElement("canvas");
    drawFn(canvas);
    const blob = await canvasToBlob(canvas, "image/png");
    if (page.objectUrl) URL.revokeObjectURL(page.objectUrl);
    const { img, url } = await loadImageFromBlob(blob);
    page.blob = blob;
    page.objectUrl = url;
    page.image = img;
    page.width = img.naturalWidth;
    page.height = img.naturalHeight;
  }

  async function rotateActivePage(dir) {
    const page = activePage();
    if (!page || !page.image) return;
    // Cancel any pending straighten preview
    if (Math.abs(state.straightenAngle) > 0.001) setStraightenAngle(0);

    const oldW = page.width;
    const oldH = page.height;
    const cw = dir === "cw";

    await bakePageBitmap(page, (canvas) => {
      canvas.width = oldH;
      canvas.height = oldW;
      const c = canvas.getContext("2d");
      c.imageSmoothingEnabled = true;
      c.imageSmoothingQuality = "high";
      if (cw) {
        c.translate(oldH, 0);
        c.rotate(Math.PI / 2);
      } else {
        c.translate(0, oldW);
        c.rotate(-Math.PI / 2);
      }
      c.drawImage(page.image, 0, 0);
    });

    page.crops = page.crops.map((c) =>
      cw ? transformCropCw90(c, oldW, oldH) : transformCropCcw90(c, oldW, oldH)
    );
    // Re-constrain after transform
    for (const c of page.crops) constrainCrop(page, c);

    markDirty();
    fitToView();
    setStatus(cw ? "Rotated 90° CW" : "Rotated 90° CCW");
    toast(cw ? "Spun clockwise" : "Spun counter-clockwise", "Crops remapped.", "ok");
  }

  async function applyStraighten() {
    const page = activePage();
    if (!page || !page.image) return;
    const angle = state.straightenAngle || 0;
    if (Math.abs(angle) < 0.001) return;

    if (page.crops.length) {
      const ok = window.confirm(
        "Apply straighten will bake the angle into this page and clear " +
          page.crops.length +
          " crop(s) on this page (axis-aligned rects can't survive free rotation cleanly). Continue?"
      );
      if (!ok) return;
    }

    const rad = (angle * Math.PI) / 180;
    const cos = Math.abs(Math.cos(rad));
    const sin = Math.abs(Math.sin(rad));
    const newW = Math.ceil(page.width * cos + page.height * sin);
    const newH = Math.ceil(page.width * sin + page.height * cos);

    await bakePageBitmap(page, (canvas) => {
      canvas.width = newW;
      canvas.height = newH;
      const c = canvas.getContext("2d");
      c.fillStyle = "#ffffff";
      c.fillRect(0, 0, newW, newH);
      c.imageSmoothingEnabled = true;
      c.imageSmoothingQuality = "high";
      c.translate(newW / 2, newH / 2);
      c.rotate(rad);
      c.drawImage(page.image, -page.width / 2, -page.height / 2);
    });

    page.crops = [];
    state.selectedCropId = null;
    setStraightenAngle(0);
    markDirty();
    fitToView();
    setStatus("Straighten baked in");
    toast("Page straightened", "Crops cleared on this page — redraw panels.", "ok");
  }

  // —— Export ——
  function getExportScale() {
    if (el.exportScale.value === "custom") {
      const pct = Number(el.exportScaleCustom.value) || 200;
      return clamp(pct, 10, 800) / 100;
    }
    return Number(el.exportScale.value) || 1;
  }

  function getExportMime() {
    return el.exportFormat.value === "jpeg" ? "image/jpeg" : "image/png";
  }

  function getExportExt() {
    return el.exportFormat.value === "jpeg" ? "jpg" : "png";
  }

  function canvasToBlob(canvas, mime, quality) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("toBlob failed"))),
        mime,
        quality
      );
    });
  }

  function getBleed() {
    return clamp(Number(state.bleed) || 0, 0, 64);
  }

  async function rasterizeCrop(page, crop, scale, opts) {
    opts = opts || {};
    const useBleed = opts.bleed !== false;
    const src = useBleed
      ? expandBleed(crop.x, crop.y, crop.w, crop.h, getBleed(), page.width, page.height)
      : { x: crop.x, y: crop.y, w: crop.w, h: crop.h };
    const w = Math.max(1, Math.round(src.w * scale));
    const h = Math.max(1, Math.round(src.h * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const c = canvas.getContext("2d");
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "high";
    // Prefer createImageBitmap for sharper decode path when available
    // Note: no viewMode filter — exports stay true to the original scan
    try {
      if (typeof createImageBitmap === "function") {
        const bmp = await createImageBitmap(page.image, Math.round(src.x), Math.round(src.y), Math.round(src.w), Math.round(src.h), {
          resizeWidth: w,
          resizeHeight: h,
          resizeQuality: "high",
        });
        c.drawImage(bmp, 0, 0);
        bmp.close();
      } else {
        c.drawImage(page.image, src.x, src.y, src.w, src.h, 0, 0, w, h);
      }
    } catch {
      c.drawImage(page.image, src.x, src.y, src.w, src.h, 0, 0, w, h);
    }
    const mime = opts.mime || getExportMime();
    const quality = mime === "image/jpeg" ? Number(el.jpegQuality.value) || 0.92 : undefined;
    const blob = await canvasToBlob(canvas, mime, quality);
    return { blob, width: w, height: h, canvas };
  }

  async function exportContactSheet() {
    const items = [];
    for (const page of state.pages) {
      for (const crop of page.crops) {
        items.push({ page, crop });
      }
    }
    if (!items.length) return;

    setStatus("Building contact sheet…");
    try {
      const scale = getExportScale();
      // Rasterize all at uniform scale first to find max cell
      const rasters = [];
      let maxW = 0;
      let maxH = 0;
      for (const { page, crop } of items) {
        const { canvas, width, height } = await rasterizeCrop(page, crop, scale, { mime: "image/png" });
        rasters.push({ canvas, width, height, name: crop.name, pageName: page.name });
        maxW = Math.max(maxW, width);
        maxH = Math.max(maxH, height);
      }

      const pad = 18;
      const labelH = 28;
      const gap = 14;
      const cols = Math.max(1, Math.ceil(Math.sqrt(rasters.length)));
      const rows = Math.ceil(rasters.length / cols);
      const cellW = maxW;
      const cellH = maxH + labelH;
      const sheetW = pad * 2 + cols * cellW + (cols - 1) * gap;
      const sheetH = pad * 2 + 36 + rows * cellH + (rows - 1) * gap;

      const sheet = document.createElement("canvas");
      sheet.width = sheetW;
      sheet.height = sheetH;
      const c = sheet.getContext("2d");
      // Dark charcoal studio bg + cream cells
      c.fillStyle = "#1a1a1e";
      c.fillRect(0, 0, sheetW, sheetH);
      c.fillStyle = "#f3efe6";
      c.font = "bold 16px Segoe UI, system-ui, sans-serif";
      c.textBaseline = "top";
      const title = (state.projectName || "untitled") + " — contact sheet";
      c.fillText(title, pad, pad);
      c.font = "12px Segoe UI, system-ui, sans-serif";
      c.fillStyle = "#a89f92";
      c.fillText(rasters.length + " panels · " + scale + "× · bleed " + getBleed() + "px", pad, pad + 20);

      rasters.forEach((r, i) => {
        const col = i % cols;
        const row = Math.floor(i / cols);
        const x = pad + col * (cellW + gap);
        const y = pad + 36 + row * (cellH + gap);
        c.fillStyle = "#2a2a30";
        c.fillRect(x, y, cellW, maxH);
        c.strokeStyle = "#3a3a42";
        c.strokeRect(x + 0.5, y + 0.5, cellW - 1, maxH - 1);
        const dx = x + Math.floor((cellW - r.width) / 2);
        const dy = y + Math.floor((maxH - r.height) / 2);
        c.drawImage(r.canvas, dx, dy);
        // Label under cell
        c.fillStyle = "#f3efe6";
        c.font = "12px ui-monospace, SF Mono, Menlo, Consolas, monospace";
        c.textBaseline = "middle";
        const label = r.name;
        c.fillText(label, x + 4, y + maxH + labelH / 2);
      });

      const blob = await canvasToBlob(sheet, "image/png");
      const fname = sanitizeFilename(state.projectName || "contact-sheet") + "-contact-sheet.png";
      downloadBlob(blob, fname);
      setStatus("Contact sheet ready");
      toast("Contact sheet", rasters.length + " panels on one PNG", "ok");
      burstFlourish(el.fxCanvas.width / 2, el.fxCanvas.height * 0.38);
    } catch (err) {
      console.error(err);
      setStatus("Contact sheet failed");
      toast("Contact sheet failed", "Try again — panels are still here.", "info");
    }
  }

  async function exportSelected() {
    const page = activePage();
    const crop = selectedCrop();
    if (!page || !crop) return;
    const scale = getExportScale();
    setStatus("Exporting…");
    try {
      const { blob } = await rasterizeCrop(page, crop, scale);
      downloadBlob(blob, `${sanitizeFilename(crop.name)}.${getExportExt()}`);
      setStatus("Ink-ready: " + crop.name + " @" + scale + "×");
      toast("Ink-ready", crop.name + " exported at " + scale + "×", "ok");
    } catch (err) {
      console.error(err);
      setStatus("Export failed");
      toast("Export failed", "Try again — the pencils are still here.", "info");
    }
  }

  async function exportAllPanels(asZip) {
    const scale = getExportScale();
    const all = [];
    for (const page of state.pages) {
      for (const crop of page.crops) {
        all.push({ page, crop });
      }
    }
    if (!all.length) return;

    setStatus(asZip ? "Building ZIP…" : "Exporting panels…");
    try {
      if (asZip) {
        if (typeof JSZip === "undefined") {
          setStatus("JSZip not loaded — cannot ZIP");
          return;
        }
        const zip = new JSZip();
        const used = new Set();
        for (const { page, crop } of all) {
          const { blob } = await rasterizeCrop(page, crop, scale);
          let name = `${sanitizeFilename(crop.name)}.${getExportExt()}`;
          let n = 2;
          while (used.has(name.toLowerCase())) {
            name = `${sanitizeFilename(crop.name)}_${n}.${getExportExt()}`;
            n++;
          }
          used.add(name.toLowerCase());
          zip.file(name, blob);
        }
        const out = await zip.generateAsync({ type: "blob" });
        downloadBlob(out, `${sanitizeFilename(state.projectName || "panels")}_export.zip`);
        setStatus("Ink-ready pack — " + all.length + " panels");
        toast("Ink-ready pack", all.length + " panels zipped at " + scale + "×", "ok");
        burstFlourish(el.fxCanvas.width / 2, el.fxCanvas.height * 0.38);
      } else {
        for (const { crop, page } of all) {
          const { blob } = await rasterizeCrop(page, crop, scale);
          downloadBlob(blob, `${sanitizeFilename(crop.name)}.${getExportExt()}`);
          await sleep(80);
        }
        setStatus("Ink-ready pack — " + all.length + " panels");
        toast("Ink-ready pack", all.length + " panels at " + scale + "× — go ink.", "ok");
        burstFlourish(el.fxCanvas.width / 2, el.fxCanvas.height * 0.38);
      }
    } catch (err) {
      console.error(err);
      setStatus("Export failed");
      toast("Export failed", "Something snagged — pencils are safe.", "info");
    }
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  function collectPrefs() {
    return {
      scale: el.exportScale.value || "3",
      customPercent: Number(el.exportScaleCustom.value) || 300,
      format: el.exportFormat.value || "png",
      jpegQuality: Number(el.jpegQuality.value) || 0.92,
      bleed: getBleed(),
      viewMode: state.viewMode || "off",
    };
  }

  function applyPrefs(ep) {
    if (!ep) return;
    // Defaults for old projects missing new fields
    const scale = ep.scale != null ? String(ep.scale) : "3";
    if ([...el.exportScale.options].some((o) => o.value === scale)) {
      el.exportScale.value = scale;
    } else {
      el.exportScale.value = "custom";
    }
    el.exportScaleCustom.value = String(ep.customPercent != null ? ep.customPercent : 300);
    if (ep.format) el.exportFormat.value = ep.format;
    if (ep.jpegQuality != null) el.jpegQuality.value = String(ep.jpegQuality);
    const bleed = ep.bleed != null ? Number(ep.bleed) : 12;
    state.bleed = clamp(bleed, 0, 64);
    if (el.exportBleed) el.exportBleed.value = String(state.bleed);
    if (el.bleedReadout) el.bleedReadout.textContent = state.bleed + " px";
    const vm = ep.viewMode || "off";
    state.viewMode = ["off", "blueline", "contrast"].includes(vm) ? vm : "off";
    if (el.viewMode) el.viewMode.value = state.viewMode;
    syncExportFields();
  }

  // —— Project save / load ——
  async function buildProjectZip() {
    if (typeof JSZip === "undefined") throw new Error("JSZip missing");
    const zip = new JSZip();
    const pagesMeta = [];

    for (let i = 0; i < state.pages.length; i++) {
      const page = state.pages[i];
      const ext = guessExt(page.blob, page.name);
      const filename = `pages/page_${String(i + 1).padStart(3, "0")}.${ext}`;
      zip.file(filename, page.blob);
      pagesMeta.push({
        id: page.id,
        name: page.name,
        file: filename,
        width: page.width,
        height: page.height,
        crops: page.crops.map((c) => ({
          id: c.id,
          name: c.name,
          x: c.x,
          y: c.y,
          w: c.w,
          h: c.h,
        })),
      });
    }

    const project = {
      version: PROJECT_VERSION,
      app: "Comic Cropper",
      name: state.projectName,
      createdAt: state.createdAt,
      updatedAt: new Date().toISOString(),
      exportPrefs: collectPrefs(),
      pages: pagesMeta,
    };

    zip.file("project.json", JSON.stringify(project, null, 2));
    return zip.generateAsync({ type: "blob" });
  }

  function guessExt(blob, name) {
    const fromName = (name || "").match(/\.([a-z0-9]+)$/i);
    if (fromName) return fromName[1].toLowerCase().replace("jpeg", "jpg");
    const t = (blob && blob.type) || "";
    if (t.includes("png")) return "png";
    if (t.includes("webp")) return "webp";
    if (t.includes("gif")) return "gif";
    if (t.includes("tif")) return "tif";
    return "jpg";
  }

  async function saveProjectDownload() {
    if (!state.pages.length) {
      setStatus("Nothing to save");
      return;
    }
    setStatus("Saving project…");
    try {
      const blob = await buildProjectZip();
      const name = sanitizeFilename(state.projectName || "comic-project") + ".comiccrop";
      downloadBlob(blob, name);
      state.dirty = false;
      setStatus("Studio saved");
      toast("Studio saved", name + " — reopen anytime for layout.", "ok");
      playClick();
    } catch (err) {
      console.error(err);
      setStatus("Save failed");
      toast("Save failed", "Check downloads / disk and try again.", "info");
    }
  }

  async function openProjectFile(file) {
    if (typeof JSZip === "undefined") {
      setStatus("JSZip not loaded");
      return;
    }
    setStatus("Opening project…");
    try {
      const zip = await JSZip.loadAsync(file);
      const jsonFile = zip.file("project.json");
      if (!jsonFile) throw new Error("Missing project.json");
      const project = JSON.parse(await jsonFile.async("string"));

      // Clear existing
      clearProject(false);

      state.projectName = project.name || file.name.replace(/\.comiccrop$/i, "") || "untitled";
      state.createdAt = project.createdAt || new Date().toISOString();
      state.updatedAt = project.updatedAt || state.createdAt;

      if (project.exportPrefs) applyPrefs(project.exportPrefs);

      for (const pm of project.pages || []) {
        const entry = zip.file(pm.file);
        if (!entry) {
          console.warn("Missing page file", pm.file);
          continue;
        }
        const blob = await entry.async("blob");
        // Ensure image MIME for decode
        const typed = blob.type && blob.type.startsWith("image/")
          ? blob
          : new Blob([blob], { type: mimeFromExt(pm.file) });
        const { img, url } = await loadImageFromBlob(typed);
        const page = {
          id: pm.id || uid("page"),
          name: pm.name || "page",
          blob: typed,
          width: img.naturalWidth,
          height: img.naturalHeight,
          objectUrl: url,
          image: img,
          crops: (pm.crops || []).map((c) => ({
            id: c.id || uid("crop"),
            name: c.name || "panel",
            x: Number(c.x) || 0,
            y: Number(c.y) || 0,
            w: Number(c.w) || MIN_CROP,
            h: Number(c.h) || MIN_CROP,
          })),
        };
        state.pages.push(page);
      }

      if (state.pages.length) {
        state.activePageId = state.pages[0].id;
        state.selectedCropId = state.pages[0].crops[0] ? state.pages[0].crops[0].id : null;
        requestAnimationFrame(fitToView);
      }
      state.dirty = false;
      markDirty(); // trigger autosave of restored project
      setStatus("Welcome back — " + state.projectName);
      toast("Project reopened", state.pages.length + " page(s), crops intact.", "ok");
      render();
    } catch (err) {
      console.error(err);
      setStatus("Failed to open project");
      alert("Could not open project file. Make sure it is a valid .comiccrop export.");
    }
  }

  function mimeFromExt(path) {
    const ext = (path.split(".").pop() || "").toLowerCase();
    const map = {
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      webp: "image/webp",
      gif: "image/gif",
      tif: "image/tiff",
      tiff: "image/tiff",
    };
    return map[ext] || "image/jpeg";
  }

  function clearProject(revokeAutosave) {
    for (const p of state.pages) {
      if (p.objectUrl) URL.revokeObjectURL(p.objectUrl);
    }
    state.pages = [];
    state.activePageId = null;
    state.selectedCropId = null;
    state.projectName = "untitled";
    state.createdAt = new Date().toISOString();
    state.updatedAt = state.createdAt;
    state.dirty = false;
    if (revokeAutosave) clearAutosave();
    render();
  }

  // —— IndexedDB autosave ——
  let autosaveTimer = null;
  let autosaveBadgeTimer = null;

  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(DB_STORE)) {
          db.createObjectStore(DB_STORE);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function scheduleAutosave() {
    if (state.recovering) return;
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => {
      autosaveNow().catch((err) => console.warn("autosave", err));
    }, AUTOSAVE_MS);
  }

  async function autosaveNow() {
    if (!state.pages.length) {
      await clearAutosave();
      return;
    }
    const db = await openDB();
    const pages = [];
    for (const p of state.pages) {
      pages.push({
        id: p.id,
        name: p.name,
        width: p.width,
        height: p.height,
        blob: p.blob,
        crops: p.crops.map((c) => ({ ...c })),
      });
    }
    const record = {
      version: PROJECT_VERSION,
      savedAt: new Date().toISOString(),
      projectName: state.projectName,
      createdAt: state.createdAt,
      updatedAt: state.updatedAt,
      activePageId: state.activePageId,
      selectedCropId: state.selectedCropId,
      exportPrefs: collectPrefs(),
      pages,
    };

    await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, "readwrite");
      tx.objectStore(DB_STORE).put(record, DB_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();

    el.autosaveBadge.hidden = false;
    clearTimeout(autosaveBadgeTimer);
    autosaveBadgeTimer = setTimeout(() => {
      el.autosaveBadge.hidden = true;
    }, 1800);
  }

  async function loadAutosaveRecord() {
    const db = await openDB();
    const record = await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE, "readonly");
      const req = tx.objectStore(DB_STORE).get(DB_KEY);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return record;
  }

  async function clearAutosave() {
    try {
      const db = await openDB();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).delete(DB_KEY);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
      db.close();
    } catch (err) {
      console.warn(err);
    }
  }

  async function restoreFromRecord(record) {
    state.recovering = true;
    clearProject(false);
    state.projectName = record.projectName || "untitled";
    state.createdAt = record.createdAt || new Date().toISOString();
    state.updatedAt = record.updatedAt || state.createdAt;

    if (record.exportPrefs) applyPrefs(record.exportPrefs);

    for (const pm of record.pages || []) {
      const { img, url } = await loadImageFromBlob(pm.blob);
      state.pages.push({
        id: pm.id || uid("page"),
        name: pm.name || "page",
        blob: pm.blob,
        width: img.naturalWidth,
        height: img.naturalHeight,
        objectUrl: url,
        image: img,
        crops: (pm.crops || []).map((c) => ({
          id: c.id || uid("crop"),
          name: c.name || "panel",
          x: Number(c.x) || 0,
          y: Number(c.y) || 0,
          w: Number(c.w) || MIN_CROP,
          h: Number(c.h) || MIN_CROP,
        })),
      });
    }

    state.activePageId =
      (record.activePageId && state.pages.some((p) => p.id === record.activePageId)
        ? record.activePageId
        : state.pages[0] && state.pages[0].id) || null;
    state.selectedCropId = record.selectedCropId || null;
    state.recovering = false;
    state.dirty = false;
    requestAnimationFrame(fitToView);
    setStatus("Picked up where you left off");
    toast("Recovered local project", "Autosave still had your back.", "ok");
    render();
  }

  async function maybeOfferRecovery() {
    try {
      const record = await loadAutosaveRecord();
      if (!record || !record.pages || !record.pages.length) return;
      const when = record.savedAt ? new Date(record.savedAt) : null;
      const label = when
        ? when.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
        : "earlier";
      el.recoverText.textContent = `A previous session (“${record.projectName || "untitled"}”, ${record.pages.length} page${record.pages.length === 1 ? "" : "s"}) was autosaved ${label}. Restore it?`;
      el.recoverModal.hidden = false;
    } catch (err) {
      console.warn("recovery check failed", err);
    }
  }

  // —— Keyboard ——
  function onKeyDown(e) {
    const tag = (e.target && e.target.tagName) || "";
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (e.target && e.target.isContentEditable);

    if (e.code === "Space" && !typing) {
      state.spaceDown = true;
      updatePanClass();
      e.preventDefault();
      return;
    }

    const mod = e.metaKey || e.ctrlKey;

    if (mod && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveProjectDownload();
      return;
    }
    if (mod && e.key.toLowerCase() === "e") {
      e.preventDefault();
      exportAllPanels(false);
      return;
    }
    if (mod && e.key.toLowerCase() === "o") {
      e.preventDefault();
      el.projectInput.click();
      return;
    }

    if (typing) return;

    if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedCrop()) {
        e.preventDefault();
        deleteSelectedCrop();
      }
      return;
    }
    if (e.key.toLowerCase() === "f") {
      fitToView();
      return;
    }
    if (e.key === "1" && !mod) {
      const page = activePage();
      if (!page) return;
      const cx = el.canvas.width / 2;
      const cy = el.canvas.height / 2;
      setZoomAt(1, cx, cy);
      return;
    }
    if (e.key.toLowerCase() === "z" && !mod) {
      const cx = el.canvas.width / 2;
      const cy = el.canvas.height / 2;
      setZoomAt(state.zoom * (e.shiftKey ? 1 / 1.25 : 1.25), cx, cy);
      return;
    }
    // Arrow-key nudge (image pixels)
    if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowUp" || e.key === "ArrowDown") {
      const crop = selectedCrop();
      const page = activePage();
      if (crop && page) {
        e.preventDefault();
        const step = e.shiftKey ? 10 : 1;
        if (e.key === "ArrowLeft") crop.x -= step;
        if (e.key === "ArrowRight") crop.x += step;
        if (e.key === "ArrowUp") crop.y -= step;
        if (e.key === "ArrowDown") crop.y += step;
        constrainCrop(page, crop);
        markDirty();
        render();
      }
      return;
    }

    if (e.key === "Escape") {
      state.selectedCropId = null;
      state.interaction = null;
      if (Math.abs(state.straightenAngle) > 0.001) setStraightenAngle(0);
      render();
    }
  }

  function onKeyUp(e) {
    if (e.code === "Space") {
      state.spaceDown = false;
      updatePanClass();
    }
  }

  function syncExportFields() {
    el.customScaleField.hidden = el.exportScale.value !== "custom";
    el.jpegQualityField.hidden = el.exportFormat.value !== "jpeg";
  }

  // —— Drag & drop ——
  let dragDepth = 0;

  function onDragEnter(e) {
    e.preventDefault();
    dragDepth++;
    el.dropOverlay.hidden = false;
  }
  function onDragLeave(e) {
    e.preventDefault();
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) el.dropOverlay.hidden = true;
  }
  function onDragOver(e) {
    e.preventDefault();
  }
  function onDrop(e) {
    e.preventDefault();
    dragDepth = 0;
    el.dropOverlay.hidden = true;
    if (e.dataTransfer && e.dataTransfer.files) {
      importFiles(e.dataTransfer.files);
    }
  }

  // —— Wire events ——
  function init() {
    resizeCanvas();
    window.addEventListener("resize", () => {
      resizeCanvas();
      resizeFx();
      if (activePage()) {
        render();
      }
    });

    el.canvas.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    el.canvas.addEventListener("wheel", onWheel, { passive: false });
    el.canvas.addEventListener("contextmenu", (e) => e.preventDefault());

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);

    el.fileInput.addEventListener("change", () => {
      importFiles(el.fileInput.files);
      el.fileInput.value = "";
    });
    el.fileInputEmpty.addEventListener("change", () => {
      importFiles(el.fileInputEmpty.files);
      el.fileInputEmpty.value = "";
    });

    el.btnOpenProject.addEventListener("click", () => el.projectInput.click());
    el.projectInput.addEventListener("change", () => {
      const f = el.projectInput.files && el.projectInput.files[0];
      if (f) openProjectFile(f);
      el.projectInput.value = "";
    });
    el.btnSaveProject.addEventListener("click", () => saveProjectDownload());

    el.btnZoomIn.addEventListener("click", () => {
      setZoomAt(state.zoom * 1.25, el.canvas.width / 2, el.canvas.height / 2);
    });
    el.btnZoomOut.addEventListener("click", () => {
      setZoomAt(state.zoom / 1.25, el.canvas.width / 2, el.canvas.height / 2);
    });
    el.btnZoomFit.addEventListener("click", fitToView);
    el.btnZoom100.addEventListener("click", () => {
      setZoomAt(1, el.canvas.width / 2, el.canvas.height / 2);
    });

    el.toggleAspect.addEventListener("change", () => {
      state.aspectLock = el.toggleAspect.checked;
    });
    el.toggleSnap.addEventListener("change", () => {
      state.snap = el.toggleSnap.checked;
    });

    el.btnDeleteCrop.addEventListener("click", deleteSelectedCrop);
    el.btnRenameCrop.addEventListener("click", renameSelectedCrop);

    el.exportScale.addEventListener("change", () => {
      syncExportFields();
      markDirty();
    });
    el.exportScaleCustom.addEventListener("change", markDirty);
    el.exportFormat.addEventListener("change", () => {
      syncExportFields();
      markDirty();
    });
    el.jpegQuality.addEventListener("change", markDirty);

    el.btnExportOne.addEventListener("click", exportSelected);
    el.btnExportAll.addEventListener("click", () => exportAllPanels(false));
    el.btnExportZip.addEventListener("click", () => exportAllPanels(true));

    el.btnRecoverYes.addEventListener("click", async () => {
      el.recoverModal.hidden = true;
      const record = await loadAutosaveRecord();
      if (record) await restoreFromRecord(record);
    });
    el.btnRecoverNo.addEventListener("click", async () => {
      el.recoverModal.hidden = true;
      await clearAutosave();
      setStatus("Fresh desk");
      toast("Started fresh", "Autosave cleared.", "info");
    });

    document.addEventListener("dragenter", onDragEnter);
    document.addEventListener("dragleave", onDragLeave);
    document.addEventListener("dragover", onDragOver);
    document.addEventListener("drop", onDrop);

    window.addEventListener("beforeunload", (e) => {
      if (state.dirty && state.pages.length) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    if (el.toggleSfx) {
      el.toggleSfx.checked = false;
      el.toggleSfx.addEventListener("change", () => {
        state.soundOn = el.toggleSfx.checked;
        if (state.soundOn) {
          playClick();
          toast("Sound on", "Soft click when you nab a panel.", "info");
        } else {
          toast("Sound off", "Silent studio.", "info");
        }
      });
    }
    if (el.toggleConfetti) {
      const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.toggleConfetti.checked = !reduce;
      state.confettiOn = !reduce;
      el.toggleConfetti.addEventListener("change", () => {
        state.confettiOn = el.toggleConfetti.checked;
        toast(state.confettiOn ? "Flourish on" : "Flourish off",
          state.confettiOn ? "Ink-splash on batch export." : "Clean handoffs only.", "info");
      });
    }

    // Bleed
    if (el.exportBleed) {
      el.exportBleed.value = String(state.bleed);
      if (el.bleedReadout) el.bleedReadout.textContent = state.bleed + " px";
      el.exportBleed.addEventListener("input", () => {
        state.bleed = clamp(Number(el.exportBleed.value) || 0, 0, 64);
        if (el.bleedReadout) el.bleedReadout.textContent = state.bleed + " px";
        render();
      });
      el.exportBleed.addEventListener("change", markDirty);
    }

    // Pencil view mode (display only)
    if (el.viewMode) {
      el.viewMode.value = state.viewMode;
      el.viewMode.addEventListener("change", () => {
        state.viewMode = el.viewMode.value || "off";
        markDirty();
        render();
        const labels = { off: "Normal view", blueline: "Blue-line view", contrast: "High-contrast view" };
        toast(labels[state.viewMode] || "View updated", "Display only — exports stay true to the scan.", "info");
      });
    }

    // Contact sheet
    if (el.btnContactSheet) {
      el.btnContactSheet.addEventListener("click", () => exportContactSheet());
    }

    // Rotate / straighten
    if (el.btnRotateCw) el.btnRotateCw.addEventListener("click", () => rotateActivePage("cw"));
    if (el.btnRotateCcw) el.btnRotateCcw.addEventListener("click", () => rotateActivePage("ccw"));
    if (el.straightenAngle) {
      el.straightenAngle.addEventListener("input", () => {
        setStraightenAngle(el.straightenAngle.value);
      });
    }
    if (el.btnApplyStraighten) {
      el.btnApplyStraighten.addEventListener("click", () => applyStraighten());
    }
    if (el.btnResetStraighten) {
      el.btnResetStraighten.addEventListener("click", () => resetStraightenPreview());
    }

    syncExportFields();
    resizeFx();
    updateUI();
    setStatus("Ready when you are — drop a scan");
    maybeOfferRecovery();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
