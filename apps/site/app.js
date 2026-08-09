/* Kale Forge product site. Small progressive-enhancement interactions.
   No dependencies, no build step. */
(function () {
  "use strict";

  var root = document.documentElement;

  // ---- theme (persisted, respects OS preference on first visit) ----
  var stored = null;
  try { stored = localStorage.getItem("kale-theme"); } catch (e) {}
  if (stored === "light" || stored === "dark") {
    root.setAttribute("data-theme", stored);
  } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
    root.setAttribute("data-theme", "light");
  }
  var themeBtn = document.getElementById("theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("kale-theme", next); } catch (e) {}
    });
  }

  // ---- mobile menu ----
  var menuBtn = document.getElementById("menu-btn");
  var menu = document.getElementById("mobile-menu");
  if (menuBtn && menu) {
    menuBtn.addEventListener("click", function () {
      var open = menu.classList.toggle("open");
      menuBtn.setAttribute("aria-expanded", String(open));
    });
    menu.addEventListener("click", function (e) {
      if (e.target.tagName === "A") {
        menu.classList.remove("open");
        menuBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  // ---- rule-category tabs ----
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var panels = Array.prototype.slice.call(document.querySelectorAll(".tab-panel"));
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var key = tab.getAttribute("data-tab");
      tabs.forEach(function (t) {
        var active = t === tab;
        t.classList.toggle("is-active", active);
        t.setAttribute("aria-selected", String(active));
      });
      panels.forEach(function (p) {
        p.classList.toggle("is-active", p.getAttribute("data-panel") === key);
      });
    });
  });

  // ---- copy-to-clipboard on code cards ----
  document.querySelectorAll(".copy").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sel = btn.getAttribute("data-copy");
      var el = sel && document.querySelector(sel);
      if (!el) return;
      var text = el.innerText.replace(/ /g, " ");
      var done = function () {
        var label = btn.textContent;
        btn.textContent = "Copied";
        setTimeout(function () { btn.textContent = label; }, 1400);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () { fallback(text, done); });
      } else {
        fallback(text, done);
      }
    });
  });
  function fallback(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) {}
    document.body.removeChild(ta);
  }

  // ---- footer year ----
  var yr = document.getElementById("year");
  if (yr) yr.textContent = String(new Date().getFullYear());

  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- entry sequence ----
  // The counter is the only thing gating the button, so the visitor is never held longer than
  // the animation itself, and SKIP / Escape / Enter get them past it at any point.
  var intro = document.getElementById("intro");
  if (intro) {
    var num = document.getElementById("intro-num");
    var bar = document.getElementById("intro-bar");
    var enter = document.getElementById("enter");
    var skip = document.getElementById("intro-skip");
    var left = false;

    function leave() {
      if (left) return;
      left = true;
      intro.classList.add("leaving");
      root.classList.remove("intro-armed");
      reveal();
      setTimeout(function () { intro.hidden = true; }, calm ? 0 : 900);
    }

    var isReady = false;
    function ready() {
      if (isReady) return;
      isReady = true;
      num.textContent = "100";
      bar.style.width = "100%";
      enter.classList.add("ready");
      enter.focus({ preventScroll: true });
    }

    enter.addEventListener("click", leave);
    skip.addEventListener("click", leave);
    document.addEventListener("keydown", function (e) {
      if (left) return;
      if (e.key === "Escape") leave();
      else if (e.key === "Enter" && enter.classList.contains("ready")) leave();
    });

    if (calm) {
      bar.style.width = "100%";
      ready();
    } else {
      var t0 = null, span = 1750;
      // requestAnimationFrame does not tick in a background tab, so a homepage opened in one
      // would count to 000 forever and never offer the button. The timer is the backstop.
      setTimeout(ready, span + 400);
      requestAnimationFrame(function step(now) {
        if (t0 === null) t0 = now;
        // Ease out, so the count decelerates into 100 instead of stopping dead.
        var p = Math.min(1, (now - t0) / span);
        var eased = 1 - Math.pow(1 - p, 3);
        var v = Math.round(eased * 100);
        num.textContent = v < 100 ? String(v).padStart(3, "0") : "100";
        bar.style.width = (eased * 100).toFixed(2) + "%";
        if (p < 1) requestAnimationFrame(step);
        else ready();
      });
    }
  }

  // ---- scroll reveals ----
  var items = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
  function reveal() {
    items.forEach(function (el) {
      if (el.getBoundingClientRect().top < innerHeight * 0.92) el.classList.add("in");
    });
  }
  if (!items.length) {
    /* nothing to reveal */
  } else if (calm || !("IntersectionObserver" in window)) {
    items.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("in");
        io.unobserve(e.target);
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    items.forEach(function (el) { io.observe(el); });
    // Anything already on screen behind the intro is revealed the moment the curtain lifts,
    // rather than waiting for a scroll that may never come on a short viewport.
    if (!intro) reveal();
  }
})();
