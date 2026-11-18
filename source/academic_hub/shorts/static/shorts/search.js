/* Shorts search: the bar that pops down from under the tab bar, and the
   results it shows over the feed.

   The tab under the tab bar opens and closes the bar. The open bar takes
   space above the reel, so the clip shrinks to make room (reels.js
   watches for that). Opening puts the cursor in the box; Escape closes
   it and hands focus back to the tab. While closed, the bar is inert, so
   Tab skips it.

   Typing searches (after a short pause, or at once on Enter):
   "#recursion" finds shorts with exactly that tag, anything else searches
   titles, closest first. The server decides both (shorts/search.py).
   Results cover the feed, five to a row, scrolling; a result opens the
   feed on that short, and that feed is only this search's results
   (views.search_order), looping. Emptying the box or closing the bar brings the feed
   back, and it carries on playing. */
(function () {
  "use strict";

  var root = document.querySelector("[data-shorts-search]");
  if (!root) return;
  var toggle = root.querySelector("[data-shorts-search-toggle]");
  var drawer = root.querySelector("[data-shorts-search-drawer]");
  var form = root.querySelector("[data-shorts-search-form]");
  var input = root.querySelector("[data-shorts-search-input]");
  var results = document.querySelector("[data-shorts-results]");
  var template = document.getElementById("result-template");
  if (!toggle || !drawer || !form || !input || !results || !template) return;
  var summary = results.querySelector("[data-shorts-results-summary]");
  var grid = results.querySelector("[data-shorts-results-grid]");
  var api = form.getAttribute("action");

  var TYPING_PAUSE = 250;   // ms after the last key before searching
  var timer = null;
  var asked = 0;            // number of the latest search sent
  var shownFor = null;      // the query the results on screen are for

  /* ---------- opening and closing the bar ---------- */
  function isOpen() { return root.classList.contains("is-open"); }

  function setOpen(open, focusToggle) {
    root.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Hide search" : "Show search");
    if (open) {
      drawer.removeAttribute("inert");
      input.focus({ preventScroll: true });
      if (input.value.trim()) run(input.value);  // pick up where it was left
    } else {
      drawer.setAttribute("inert", "");
      cancel();
      hideResults();
      if (focusToggle) toggle.focus();
    }
  }

  toggle.addEventListener("click", function () { setOpen(!isOpen(), false); });

  function onEscape(e) {
    if (e.key === "Escape" && isOpen()) {
      e.preventDefault();
      setOpen(false, true);
    }
  }
  root.addEventListener("keydown", onEscape);
  results.addEventListener("keydown", onEscape);

  /* ---------- showing and hiding the results ---------- */
  function announce(active) {
    document.dispatchEvent(new CustomEvent("shorts:search", { detail: { active: active } }));
  }

  function showResults() {
    if (!results.hidden) return;
    results.hidden = false;
    results.scrollTop = 0;
    announce(true);
  }

  function hideResults() {
    shownFor = null;
    if (results.hidden) return;
    results.hidden = true;
    grid.textContent = "";
    summary.textContent = "";
    announce(false);
  }

  /* ---------- searching ---------- */
  function cancel() {
    window.clearTimeout(timer);
    asked += 1;  // any answer still on its way is now out of date
  }

  function run(raw) {
    window.clearTimeout(timer);
    var q = raw.trim();
    if (!q) { cancel(); hideResults(); return; }
    if (q === shownFor) return;
    var number = ++asked;
    fetch(api + "?q=" + encodeURIComponent(q), {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    })
      .then(function (response) {
        if (!response.ok) throw new Error(String(response.status));
        return response.json();
      })
      .then(function (data) {
        if (number !== asked || !isOpen()) return;  // typed on since
        shownFor = q;
        render(q, data);
      })
      .catch(function () {
        if (number !== asked || !isOpen()) return;
        shownFor = null;
        grid.textContent = "";
        summary.textContent = "Search is not available right now. Try again in a moment.";
        showResults();
      });
  }

  input.addEventListener("input", function () {
    window.clearTimeout(timer);
    if (!input.value.trim()) { cancel(); hideResults(); return; }
    timer = window.setTimeout(function () { run(input.value); }, TYPING_PAUSE);
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    shownFor = null;  // Enter always asks again
    run(input.value);
  });

  /* ---------- drawing the results ---------- */
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function describe(q, data) {
    var shown = data.items.length;
    var tags = (data.tags || []).map(function (t) { return "#" + t; }).join(" ");
    if (data.kind === "tag" && !tags) return ["Type a tag after #, like ", "#recursion", "."];
    var what = data.kind === "tag" ? tags : "“" + q + "”";
    if (!data.total) {
      return data.kind === "tag"
        ? ["No shorts tagged ", what, "."]
        : ["No shorts match ", what, "."];
    }
    var count = plural(data.total, "short", "shorts");
    var lead = data.kind === "tag" ? count + " tagged " : count + " for ";
    var tail = shown < data.total ? " (showing the first " + shown + ")" : "";
    if (data.kind !== "tag") tail += ", closest first";
    return [lead, what, tail + "."];
  }

  function render(q, data) {
    var parts = describe(q, data);
    summary.textContent = "";
    summary.appendChild(document.createTextNode(parts[0]));
    var strong = document.createElement("strong");
    strong.textContent = parts[1];
    summary.appendChild(strong);
    summary.appendChild(document.createTextNode(parts[2]));

    grid.textContent = "";
    data.items.forEach(function (item) { grid.appendChild(build(item)); });
    showResults();
    results.scrollTop = 0;
  }

  function build(item) {
    var li = template.content.firstElementChild.cloneNode(true);
    var link = li.querySelector("[data-result-link]");
    link.href = item.url;
    li.querySelector("[data-result-title]").textContent = item.title;
    li.querySelector("[data-result-length]").textContent = item.length || "";
    li.querySelector("[data-result-tags]").textContent =
      (item.tags || []).map(function (t) { return "#" + t; }).join(" ");
    var video = li.querySelector("[data-result-video]");
    video.dataset.src = item.src;
    if (thumbs) thumbs.observe(video);
    else showFrame(video);
    return li;
  }

  /* ---------- a still from each clip, loaded as it scrolls into view ---------- */
  function showFrame(video) {
    if (!video.dataset.src) return;
    video.preload = "metadata";
    // #t= asks for a frame a moment in, past any black first frame.
    video.src = video.dataset.src + "#t=0.5";
    delete video.dataset.src;
  }

  var thumbs = window.IntersectionObserver
    ? new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          thumbs.unobserve(entry.target);
          showFrame(entry.target);
        });
      }, { root: results, rootMargin: "200px 0px" })
    : null;
})();
