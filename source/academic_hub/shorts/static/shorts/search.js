/* Shorts search: the bar that pops down from under the tab bar.

   The tab under the tab bar opens and closes it. The open bar takes
   space above the reel, so the clip shrinks to make room (reels.js
   watches for that). Opening puts the cursor in the box; Escape closes
   it and hands focus back to the tab. While closed, the bar is inert, so
   Tab skips it.

   Only the bar for now: searching is not wired up yet, so submitting the
   form is held back instead of reloading the page. */
(function () {
  "use strict";

  var root = document.querySelector("[data-shorts-search]");
  if (!root) return;
  var toggle = root.querySelector("[data-shorts-search-toggle]");
  var drawer = root.querySelector("[data-shorts-search-drawer]");
  var form = root.querySelector("[data-shorts-search-form]");
  var input = root.querySelector("[data-shorts-search-input]");
  if (!toggle || !drawer || !form || !input) return;

  function isOpen() { return root.classList.contains("is-open"); }

  function setOpen(open, focusToggle) {
    root.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Hide search" : "Show search");
    if (open) {
      drawer.removeAttribute("inert");
      input.focus({ preventScroll: true });
    } else {
      drawer.setAttribute("inert", "");
      if (focusToggle) toggle.focus();
    }
  }

  toggle.addEventListener("click", function () { setOpen(!isOpen(), false); });

  root.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && isOpen()) {
      e.preventDefault();
      setOpen(false, true);
    }
  });

  // Not wired up yet: keep Enter from reloading the feed.
  form.addEventListener("submit", function (e) { e.preventDefault(); });
})();
