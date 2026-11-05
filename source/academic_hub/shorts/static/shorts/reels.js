/* Shorts reel player (US-14, SRS-8).

   One clip per screen. Swiping, the mouse wheel, the arrow keys and the
   up/down buttons all move one clip at a time. The clip on screen plays and
   loops; the others are paused. Only the clips next to the current one are
   loaded, so a long feed does not download every video at once.

   The feed never ends: a few clips before the last one, the next batch is
   fetched from the server, and after the very last clip it starts over
   from the newest.

   Clips start muted because browsers refuse to autoplay video with sound.
   The sound button applies to every clip and is remembered for the visit. */
(function () {
  "use strict";

  var root = document.querySelector("[data-reels]");
  var template = document.getElementById("reel-template");
  var dataNode = document.getElementById("reels-data");
  if (!root || !template || !dataNode) return;

  var viewport = root.querySelector("[data-reels-viewport]");
  var prevButton = root.querySelector("[data-reels-prev]");
  var nextButton = root.querySelector("[data-reels-next]");
  var calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var page;
  try { page = JSON.parse(dataNode.textContent); } catch (e) { return; }

  var slides = [];      // {el, video, userPaused}
  var active = -1;
  var nextOffset = page.next;
  var loading = false;
  var LOAD_AHEAD = 3;   // fetch more when this close to the last clip

  var muted = true;
  try { muted = window.sessionStorage.getItem("shorts-muted") !== "0"; } catch (e) { /* storage off */ }

  /* ---------- fill the window under the tab bar ---------- */
  function fit() {
    var top = root.getBoundingClientRect().top + window.scrollY;
    root.style.setProperty("--reels-top", Math.max(0, Math.round(top)) + "px");
  }
  fit();
  window.addEventListener("resize", fit);

  /* ---------- building slides ---------- */
  function build(item) {
    var el = template.content.firstElementChild.cloneNode(true);
    el.setAttribute("aria-label", item.title);
    el.querySelector("[data-reel-title]").textContent = item.title;
    el.querySelector("[data-reel-desc]").textContent = item.description || "";
    var tags = el.querySelector("[data-reel-tags]");
    (item.tags || []).forEach(function (label) {
      var li = document.createElement("li");
      li.textContent = "#" + label;
      tags.appendChild(li);
    });
    el.querySelector("video").dataset.src = item.src;
    return el;
  }

  function slideIndex(el) {
    for (var i = 0; i < slides.length; i++) if (slides[i].el === el) return i;
    return -1;
  }

  function add(items) {
    var added = [];
    items.forEach(function (item) {
      var el = build(item);
      viewport.appendChild(el);
      var slide = { el: el, video: el.querySelector("video"), userPaused: false };
      slides.push(slide);
      wire(slide);
      observer.observe(el);
      added.push(slide);
    });
    slides.forEach(function (s, i) {
      s.el.setAttribute("aria-posinset", String(i + 1));
      s.el.setAttribute("aria-setsize", "-1"); // the feed has no end
    });
    // "more" only when the description really is cut off; that needs layout.
    window.requestAnimationFrame(function () { added.forEach(checkOverflow); });
  }

  function checkOverflow(slide) {
    var desc = slide.el.querySelector("[data-reel-desc]");
    var more = slide.el.querySelector("[data-reel-more]");
    more.hidden = !(desc.textContent && desc.scrollHeight > desc.clientHeight + 1);
  }

  /* ---------- loading and unloading video ---------- */
  function load(slide, eager) {
    var video = slide.video;
    video.preload = eager ? "auto" : "metadata";
    if (!video.getAttribute("src")) video.src = video.dataset.src;
  }

  function unload(slide) {
    var video = slide.video;
    if (!video.getAttribute("src")) return;
    video.pause();
    video.removeAttribute("src");
    video.load(); // releases the network connection and decoder
  }

  /* ---------- playing ---------- */
  function setPaused(slide, paused) {
    slide.el.classList.toggle("is-paused", paused);
    slide.el.querySelector("[data-reel-toggle]").setAttribute("aria-label", paused ? "Play" : "Pause");
  }

  function play(slide) {
    var video = slide.video;
    video.muted = muted;
    var attempt = video.play();
    if (!attempt || !attempt.catch) return;
    attempt.catch(function () {
      // Sound before any tap is blocked: fall back to muted rather than not playing.
      if (!video.muted) {
        setMuted(true);
        video.play().catch(function () { setPaused(slide, true); });
      } else {
        setPaused(slide, true);
      }
    });
  }

  function activate(index) {
    if (index === active || index < 0) return;
    active = index;
    slides.forEach(function (slide, i) {
      var distance = Math.abs(i - index);
      if (distance <= 1) load(slide, distance === 0);
      else if (distance > 2) unload(slide);
      if (i !== index) {
        slide.video.pause();
        setPaused(slide, false);
        slide.el.classList.remove("is-expanded");
        slide.userPaused = false;
      }
    });
    var current = slides[index];
    try { current.video.currentTime = 0; } catch (e) { /* not loaded yet */ }
    play(current);
    prevButton.disabled = index === 0;
    loadMore();
  }

  function toggle(slide) {
    if (slide.video.paused) {
      slide.userPaused = false;
      play(slide);
    } else {
      slide.userPaused = true;
      slide.video.pause();
    }
  }

  /* ---------- sound ---------- */
  function setMuted(value) {
    muted = value;
    try { window.sessionStorage.setItem("shorts-muted", value ? "1" : "0"); } catch (e) { /* storage off */ }
    slides.forEach(function (slide) {
      slide.video.muted = value;
      var button = slide.el.querySelector("[data-reel-mute]");
      button.setAttribute("aria-pressed", value ? "true" : "false");
      button.setAttribute("aria-label", value ? "Turn sound on" : "Turn sound off");
    });
  }

  /* ---------- per-slide controls ---------- */
  function wire(slide) {
    var el = slide.el, video = slide.video;
    var bar = el.querySelector("[data-reel-progress]");
    var mute = el.querySelector("[data-reel-mute]");
    mute.setAttribute("aria-pressed", muted ? "true" : "false");
    mute.setAttribute("aria-label", muted ? "Turn sound on" : "Turn sound off");

    el.querySelector("[data-reel-toggle]").addEventListener("click", function () { toggle(slide); });
    mute.addEventListener("click", function () {
      setMuted(!muted);
      if (!muted && slides[active] === slide && video.paused && !slide.userPaused) play(slide);
    });
    el.querySelector("[data-reel-more]").addEventListener("click", function (e) {
      var open = el.classList.toggle("is-expanded");
      e.currentTarget.textContent = open ? "less" : "more";
    });

    video.addEventListener("play", function () { setPaused(slide, false); });
    video.addEventListener("pause", function () {
      if (slides[active] === slide) setPaused(slide, true);
    });
    video.addEventListener("timeupdate", function () {
      if (video.duration) bar.style.width = (video.currentTime / video.duration * 100) + "%";
    });
    video.addEventListener("playing", function () {
      el.querySelector("[data-reel-error]").hidden = true;
    });
    video.addEventListener("error", function () {
      if (video.getAttribute("src")) el.querySelector("[data-reel-error]").hidden = false;
    });
  }

  /* ---------- which clip is on screen ---------- */
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) activate(slideIndex(entry.target));
    });
  }, { root: viewport, threshold: 0.6 });

  /* ---------- moving between clips ---------- */
  function go(step) {
    var target = Math.max(0, Math.min(slides.length - 1, active + step));
    if (target === active) return;
    slides[target].el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
  }

  prevButton.addEventListener("click", function () { go(-1); });
  nextButton.addEventListener("click", function () { go(1); });

  // A mouse wheel moves exactly one clip per flick, as on Reels, instead of
  // however far the wheel happened to spin. Trackpads send a burst of small
  // events for one gesture, hence the pause before the next move.
  var wheelLocked = false;
  viewport.addEventListener("wheel", function (e) {
    var desc = e.target.closest && e.target.closest(".is-expanded .reel__desc");
    if (desc && desc.scrollHeight > desc.clientHeight) return; // reading a long description
    if (Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;
    e.preventDefault();
    if (wheelLocked || Math.abs(e.deltaY) < 12) return;
    wheelLocked = true;
    go(e.deltaY > 0 ? 1 : -1);
    window.setTimeout(function () { wheelLocked = false; }, 650);
  }, { passive: false });

  document.addEventListener("keydown", function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (t && t.closest && t.closest(".topbar, .subnav")) return; // menus keep their keys
    var slide = slides[active];
    switch (e.key) {
      case "ArrowDown": case "PageDown": case "j": go(1); break;
      case "ArrowUp": case "PageUp": case "k": go(-1); break;
      case " ":
        if (t && t.tagName === "BUTTON") return; // Space presses the focused button
        if (slide) toggle(slide);
        break;
      case "m": setMuted(!muted); if (slide && !muted && !slide.userPaused) play(slide); break;
      default: return;
    }
    e.preventDefault();
  });

  // Stop playing in a background tab; pick up again on return.
  document.addEventListener("visibilitychange", function () {
    var slide = slides[active];
    if (!slide) return;
    if (document.hidden) slide.video.pause();
    else if (!slide.userPaused) play(slide);
  });

  /* ---------- the endless feed ---------- */
  function loadMore() {
    if (loading || active < slides.length - LOAD_AHEAD) return;
    loading = true;
    viewport.setAttribute("aria-busy", "true");
    // After the last batch (next is null), start over from the newest clip.
    var offset = nextOffset === null ? 0 : nextOffset;
    var grew = false;
    fetch(root.dataset.api + "?offset=" + offset, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (data) {
        nextOffset = data.next;
        if (data.items && data.items.length) {
          add(data.items);
          grew = true;
        }
      })
      .catch(function () { /* offline or signed out: the next move tries again */ })
      .then(function () {
        loading = false;
        viewport.setAttribute("aria-busy", "false");
        if (grew) loadMore(); // a very short feed may need another lap to fill ahead
      });
  }

  /* ---------- one-off messages ("… was uploaded") ---------- */
  root.querySelectorAll("[data-reels-toast]").forEach(function (toast) {
    window.setTimeout(function () { toast.classList.add("is-gone"); }, 4000);
  });

  add(page.items);
})();
