/* Shorts reel player (US-14, SRS-8).

   One clip per screen. Swiping, the mouse wheel, the arrow keys and the
   up/down buttons all move one clip at a time. The clip on screen plays and
   loops; the others are paused. Only the clips next to the current one are
   loaded, so a long feed does not download every video at once.

   The feed never ends: a few clips before the last one, the next batch is
   fetched from the server, and after the very last clip it starts over
   from the newest.

   Clips start muted because browsers refuse to autoplay video with sound.
   The sound button applies to every clip and is remembered for the visit.

   Beside each clip are a like button and a comments button. Likes change
   on screen at once and are put back if the server refuses. Comments open
   in one shared panel for the clip on screen. */
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

  var csrfInput = root.querySelector('input[name="csrfmiddlewaretoken"]');
  var csrf = csrfInput ? csrfInput.value : "";
  // Like and comment counts per short. The feed loops, so one short can be
  // on several slides; they all read from here and stay in step.
  var states = {};

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
      var slide = { el: el, video: el.querySelector("video"), userPaused: false, item: item };
      slides.push(slide);
      remember(item);
      wire(slide);
      observer.observe(el);
      added.push(slide);
      paint(item.id);
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
    // Comments belong to the clip they were opened on; moving on closes them.
    if (commentsFor && commentsFor !== current) closeComments(false);
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
    el.querySelector("[data-reel-like]").addEventListener("click", function () { toggleLike(slide); });
    el.querySelector("[data-reel-comments]").addEventListener("click", function () {
      if (commentsFor === slide) closeComments(true); else openComments(slide);
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

  /* ---------- talking to the server ---------- */
  function parse(response) {
    return response.json().catch(function () { return {}; }).then(function (data) {
      if (response.ok) return data;
      return Promise.reject(data.error || "Something went wrong. Try again.");
    });
  }
  function getJSON(url) {
    return fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } }).then(parse);
  }
  function send(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body || {})
    }).then(parse);
  }

  /* ---------- counts ---------- */
  // 950 -> "950", 1234 -> "1.2K", 15300 -> "15K", as Reels shows them.
  function compact(n) {
    if (n < 1000) return String(n);
    if (n < 1000000) return (n / 1000).toFixed(n < 10000 ? 1 : 0).replace(/\.0$/, "") + "K";
    return (n / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
  }

  function remember(item) {
    var state = states[item.id];
    if (state && state.pending) return; // a like is on its way; keep what it set
    states[item.id] = { liked: !!item.liked, likes: item.like_count || 0, comments: item.comment_count || 0 };
  }

  function paint(id) {
    var state = states[id];
    slides.forEach(function (slide) {
      if (slide.item.id !== id) return;
      var like = slide.el.querySelector("[data-reel-like]");
      like.setAttribute("aria-pressed", state.liked ? "true" : "false");
      like.setAttribute("aria-label", (state.liked ? "Unlike" : "Like") + ", " +
                        state.likes + (state.likes === 1 ? " like" : " likes"));
      slide.el.querySelector("[data-reel-like-count]").textContent = compact(state.likes);
      var talk = slide.el.querySelector("[data-reel-comments]");
      talk.setAttribute("aria-label", "Comments, " + state.comments);
      slide.el.querySelector("[data-reel-comment-count]").textContent = compact(state.comments);
    });
    if (commentsFor && commentsFor.item.id === id) {
      panelTotal.textContent = state.comments ? "(" + state.comments + ")" : "";
    }
  }

  /* ---------- likes ---------- */
  // The heart changes at once; if the server says no, it changes back.
  function toggleLike(slide) {
    var id = slide.item.id, state = states[id];
    if (state.pending) return;
    var before = { liked: state.liked, likes: state.likes };
    state.liked = !state.liked;
    state.likes = Math.max(0, state.likes + (state.liked ? 1 : -1));
    state.pending = true;
    paint(id);
    if (state.liked && !calm) {
      var heart = slide.el.querySelector("[data-reel-like]");
      heart.classList.remove("is-popping");
      void heart.offsetWidth; // restart the animation
      heart.classList.add("is-popping");
    }
    send(slide.item.like_url, { liked: state.liked })
      .then(function (data) { state.liked = data.liked; state.likes = data.like_count; })
      .catch(function () { state.liked = before.liked; state.likes = before.likes; })
      .then(function () { state.pending = false; paint(id); });
  }

  /* ---------- comments ---------- */
  var panel = root.querySelector("[data-comments]");
  var backdrop = root.querySelector(".comments__backdrop");
  var panelTotal = panel.querySelector("[data-comments-total]");
  var list = panel.querySelector("[data-comments-list]");
  var emptyNote = panel.querySelector("[data-comments-empty]");
  var moreButton = panel.querySelector("[data-comments-more]");
  var statusNote = panel.querySelector("[data-comments-status]");
  var form = panel.querySelector("[data-comments-form]");
  var input = panel.querySelector("[data-comments-input]");
  var postButton = panel.querySelector("[data-comments-post]");
  var commentTemplate = document.getElementById("comment-template");
  var commentsFor = null;   // the slide whose comments are open
  var nextBefore = null;
  var listToken = 0;        // ignores replies meant for a panel since closed

  // "now", "5m", "3h", "2d", then a date, the way Reels labels comments.
  function ago(iso) {
    var then = new Date(iso), seconds = (Date.now() - then.getTime()) / 1000;
    if (seconds < 60) return "now";
    if (seconds < 3600) return Math.floor(seconds / 60) + "m";
    if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
    if (seconds < 7 * 86400) return Math.floor(seconds / 86400) + "d";
    return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }

  function commentNode(comment) {
    var li = commentTemplate.content.firstElementChild.cloneNode(true);
    li.dataset.id = comment.id;
    li.querySelector("[data-comment-initial]").textContent = (comment.author || "?").trim().charAt(0).toUpperCase();
    li.querySelector("[data-comment-author]").textContent = comment.author;
    li.querySelector("[data-comment-role]").textContent = comment.role;
    var time = li.querySelector("[data-comment-time]");
    time.dateTime = comment.created_at;
    time.textContent = ago(comment.created_at);
    time.title = new Date(comment.created_at).toLocaleString();
    li.querySelector("[data-comment-body]").textContent = comment.body;
    var del = li.querySelector("[data-comment-delete]");
    if (comment.delete_url) {
      del.hidden = false;
      del.addEventListener("click", function () { deleteComment(li, del, comment.delete_url); });
    }
    return li;
  }

  function showEmpty() { emptyNote.hidden = list.children.length > 0; }

  function loadComments(before) {
    var slide = commentsFor, token = listToken;
    moreButton.hidden = true;
    statusNote.textContent = "";
    getJSON(slide.item.comments_url + (before ? "?before=" + before : ""))
      .then(function (data) {
        if (token !== listToken) return;
        data.items.forEach(function (c) { list.appendChild(commentNode(c)); });
        nextBefore = data.next_before;
        moreButton.hidden = nextBefore === null;
        states[slide.item.id].comments = data.comment_count;
        paint(slide.item.id);
        showEmpty();
      })
      .catch(function (message) {
        if (token === listToken) statusNote.textContent = message;
      });
  }

  function openComments(slide) {
    commentsFor = slide;
    listToken += 1;
    list.textContent = "";
    emptyNote.hidden = true;
    nextBefore = null;
    panel.hidden = false;
    backdrop.hidden = false;
    root.classList.add("has-comments");
    paint(slide.item.id);
    loadComments(null);
    panel.querySelector(".comments__close").focus({ preventScroll: true });
  }

  function closeComments(returnFocus) {
    var slide = commentsFor;
    if (!slide) return;
    commentsFor = null;
    listToken += 1;
    panel.hidden = true;
    backdrop.hidden = true;
    root.classList.remove("has-comments");
    if (returnFocus) slide.el.querySelector("[data-reel-comments]").focus({ preventScroll: true });
  }

  root.querySelectorAll("[data-comments-close]").forEach(function (button) {
    button.addEventListener("click", function () { closeComments(true); });
  });
  moreButton.addEventListener("click", function () { if (nextBefore !== null) loadComments(nextBefore); });

  // The box grows with what is typed, up to about four lines.
  function sizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight + 2, 104) + "px";
    postButton.disabled = !input.value.trim();
  }
  input.addEventListener("input", sizeInput);
  // Enter posts, Shift+Enter starts a new line. isComposing: a Thai (or any
  // IME) word still being composed must not be sent half-typed.
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!postButton.disabled) form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var slide = commentsFor, body = input.value.trim();
    if (!slide || !body) return;
    postButton.disabled = true;
    input.readOnly = true;
    statusNote.textContent = "";
    send(slide.item.comments_url, { body: body })
      .then(function (data) {
        if (commentsFor === slide) {
          list.insertBefore(commentNode(data.comment), list.firstChild);
          panel.querySelector("[data-comments-body]").scrollTop = 0;
          showEmpty();
        }
        states[slide.item.id].comments = data.comment_count;
        paint(slide.item.id);
        input.value = "";
      })
      .catch(function (message) { statusNote.textContent = message; })
      .then(function () { input.readOnly = false; sizeInput(); input.focus({ preventScroll: true }); });
  });

  // First press arms the button ("Delete?"), a second within 3s deletes.
  function deleteComment(li, button, url) {
    if (!button.classList.contains("is-confirming")) {
      button.classList.add("is-confirming");
      button.textContent = "Delete?";
      window.setTimeout(function () {
        button.classList.remove("is-confirming");
        button.textContent = "Delete";
      }, 3000);
      return;
    }
    var slide = commentsFor;
    if (!slide) return;
    button.disabled = true;
    send(url, {})
      .then(function (data) {
        li.remove();
        showEmpty();
        states[slide.item.id].comments = data.comment_count;
        paint(slide.item.id);
      })
      .catch(function (message) { statusNote.textContent = message; button.disabled = false; });
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
    if (commentsFor && e.key === "Escape") { closeComments(true); e.preventDefault(); return; }
    if (t && t.closest && t.closest("[data-comments]")) return; // typing or reading comments
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
