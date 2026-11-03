/* Calendar behaviour, Google Calendar style.

   1. Fitting: every deadline is in the HTML. For each day, hide the ones
      that do not fit in the cell and show "+N more" instead. Re-run on
      resize, because the rows stretch with the window.
   2. Popovers: "+N more" opens the day's full list over the cell; any
      deadline opens its details beside it. Escape, the X, or a click
      outside closes it.
   3. Focus: after "Next deadline", the page arrives with one deadline
      focused (data-focus on the calendar). It is kept visible in its day
      and its details open straight away.
   4. "Next deadline" follows what you open: while a deadline's details are
      showing, the button means "the deadline after this one"
      (?after=<id>). Closing the popover puts the button back.
   5. Day notes: clicking empty space in a day opens a form (title,
      description, "notify me"). Clicking a note shows it with Edit and
      Delete. The forms come from <template>s in the page, which carry the
      CSRF token, and are submitted as ordinary POSTs.

   All text from the server is set with textContent, never innerHTML. */
(function () {
  "use strict";

  var dataEl = document.getElementById("cal-events");
  var pop = document.getElementById("calpop");
  if (!dataEl || !pop) return;

  // The page's <main> is its own layer below the top bar, so a popover left
  // inside it would slide under the bar. Lift it to the top of <body>.
  document.body.appendChild(pop);

  var events = {};
  JSON.parse(dataEl.textContent).forEach(function (e) { events[e.id] = e; });

  var calEl = document.querySelector(".cal");
  var focusId = calEl ? calEl.dataset.focus : "";

  /* ---------- 4. the Next button follows the opened deadline ---------- */

  var nextBtn = document.querySelector("[data-next]");
  var nextDefault = nextBtn && {
    href: nextBtn.getAttribute("href"),
    title: nextBtn.title,
    disabled: nextBtn.classList.contains("is-disabled")
  };

  function aimNextAfter(e) {
    if (!nextBtn) return;
    nextBtn.setAttribute("href", "?after=" + encodeURIComponent(e.id));
    nextBtn.title = "Next after: " + e.title;
    nextBtn.classList.remove("is-disabled");
    nextBtn.removeAttribute("aria-disabled");
    nextBtn.removeAttribute("tabindex");
  }

  function resetNext() {
    if (!nextBtn) return;
    if (nextDefault.href) nextBtn.setAttribute("href", nextDefault.href);
    else nextBtn.removeAttribute("href");
    nextBtn.title = nextDefault.title;
    nextBtn.classList.toggle("is-disabled", nextDefault.disabled);
    if (nextDefault.disabled) {
      nextBtn.setAttribute("aria-disabled", "true");
      nextBtn.setAttribute("tabindex", "-1");
    }
  }

  /* ---------- 1. fitting ---------- */

  function fit(box) {
    var items = Array.prototype.slice.call(box.querySelectorAll(".cal__ev"));
    var more = box.querySelector("[data-more]");
    items.forEach(function (el) { el.hidden = false; });
    more.hidden = true;
    if (!items.length || box.scrollHeight <= box.clientHeight + 1) return;

    // Items share one height (set in CSS), so the count that fits is
    // a division. One slot is kept for the "+N more" line itself.
    var gap = parseFloat(getComputedStyle(box).rowGap) || 0;
    var step = items[0].offsetHeight + gap;
    var fits = Math.floor((box.clientHeight + gap) / step);
    var shown = Math.max(0, fits - 1);

    items.forEach(function (el, i) { el.hidden = i >= shown; });
    // Never bury the deadline "Next deadline" just jumped to: swap it into
    // the last visible slot.
    var focused = items.filter(function (el) { return el.dataset.event === focusId; })[0];
    if (focused && focused.hidden && shown > 0) {
      items[shown - 1].hidden = true;
      focused.hidden = false;
    }
    more.hidden = false;
    var hiddenCount = items.length - shown;
    more.textContent = "+" + hiddenCount + " more";
    more.setAttribute("aria-label", hiddenCount + " more deadlines on " +
      box.closest(".cal__day").dataset.dayLabel);
  }

  function fitAll() {
    document.querySelectorAll("[data-events]").forEach(fit);
  }

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { fitAll(); close(); }, 80);
  });
  // Fonts change text metrics; fit again once they have loaded.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll);
  fitAll();

  /* ---------- 2. popovers ---------- */

  var opener = null;      // element to return focus to
  var backTo = null;      // day cell, when details were opened from its list

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function iconButton(cls, label, glyph, onClick) {
    var b = el("button", "calpop__icon " + cls, glyph);
    b.type = "button";
    b.setAttribute("aria-label", label);
    b.addEventListener("click", onClick);
    return b;
  }

  function topBar(withBack) {
    var bar = el("div", "calpop__bar");
    if (withBack) {
      bar.appendChild(iconButton("calpop__back", "Back to day", "‹", function () {
        var cell = backTo;
        openDay(cell, cell.querySelector("[data-more]") || cell);
      }));
    }
    bar.appendChild(iconButton("calpop__close", "Close", "×", close));
    return bar;
  }

  // Beside the anchor when there is room, otherwise on the other side;
  // always kept inside the window.
  function placeBeside(r) {
    var w = pop.offsetWidth, h = pop.offsetHeight, m = 12;
    var left = r.right + 8;
    if (left + w > window.innerWidth - m) left = r.left - w - 8;
    if (left < m) left = Math.max(m, (window.innerWidth - w) / 2);
    var top = Math.min(Math.max(m, r.top), window.innerHeight - h - m);
    pop.style.left = left + "px";
    pop.style.top = Math.max(m, top) + "px";
  }

  // Over the day cell, as Google does for "+N more".
  function placeOver(cell) {
    var r = cell.getBoundingClientRect();
    var w = pop.offsetWidth, h = pop.offsetHeight, m = 12;
    var left = r.left + (r.width - w) / 2;
    left = Math.min(Math.max(m, left), window.innerWidth - w - m);
    var top = Math.min(Math.max(m, r.top - 8), window.innerHeight - h - m);
    pop.style.left = left + "px";
    pop.style.top = Math.max(m, top) + "px";
  }

  function show(anchor, quiet) {
    if (pop.hidden) opener = quiet ? null : anchor;
    pop.hidden = false;
    if (quiet) return;  // opened by the page itself: leave keyboard focus alone
    var first = pop.querySelector("button, a");
    if (first) first.focus({ preventScroll: true });
  }

  function close() {
    if (pop.hidden) return;
    pop.hidden = true;
    pop.replaceChildren();
    backTo = null;
    resetNext();
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    opener = null;
  }

  function chip(e) {
    var b = el("button", "cal__ev cal__ev--" + e.kind + (e.notify ? " cal__ev--notify" : "") +
      (e.passed ? " cal__ev--passed" : ""));
    b.type = "button";
    b.style.setProperty("--ev", e.colour);
    if (e.kind === "announcement") {
      var dot = el("span", "cal__dot"); dot.setAttribute("aria-hidden", "true");
      b.appendChild(dot);
      b.appendChild(el("span", "cal__time", e.time_label));
    }
    b.appendChild(el("span", "cal__evtitle", e.title));
    b.title = (e.time_label ? e.time_label + " · " : "") + e.title + " (" + e.source + ")";
    b.dataset.event = e.id;
    return b;
  }

  function openDay(cell, anchor) {
    resetNext();
    pop.replaceChildren();
    pop.className = "calpop calpop--day";
    pop.appendChild(topBar(false));
    pop.appendChild(el("p", "calpop__weekday", cell.dataset.weekday));
    var num = el("p", "calpop__daynum", cell.dataset.daynum);
    num.id = "calpop-title";
    pop.appendChild(num);
    var list = el("div", "calpop__list");
    cell.querySelectorAll(".cal__ev").forEach(function (src) {
      var e = events[src.dataset.event];
      if (!e) return;
      var b = chip(e);
      b.addEventListener("click", function () { openEvent(e, b, cell); });
      list.appendChild(b);
    });
    pop.appendChild(list);
    var add = el("button", "calpop__add", "+ Add note");
    add.type = "button";
    add.addEventListener("click", function () { openNoteForm(cell, null, cell); });
    pop.appendChild(add);
    show(anchor);
    placeOver(cell);
  }

  function openEvent(e, anchor, fromDay, quiet) {
    // Measured now: when opened from the day list, the clicked chip is
    // removed below, and the details sit where that list was.
    var rect = (fromDay ? pop : anchor).getBoundingClientRect();
    if (fromDay) rect = { left: rect.left, right: rect.left, top: rect.top };
    backTo = fromDay || null;
    pop.replaceChildren();
    pop.className = "calpop calpop--event";
    pop.appendChild(topBar(!!fromDay));

    var head = el("div", "calpop__head");
    var swatch = el("span", "calpop__swatch");
    swatch.style.background = e.colour;
    if (e.passed) swatch.classList.add("calpop__swatch--passed");
    swatch.setAttribute("aria-hidden", "true");
    var text = el("div");
    var title = el("h2", "calpop__title", e.title);
    title.id = "calpop-title";
    text.appendChild(title);
    text.appendChild(el("p", "calpop__when",
      e.kind === "note" ? e.date_label :
        e.date_label + (e.passed ? " · Was due " : " · Due ") + e.time_label));
    head.appendChild(swatch);
    head.appendChild(text);
    pop.appendChild(head);

    pop.appendChild(el("p", "calpop__source", e.source));
    if (e.kind === "note") {
      pop.appendChild(el("p", "calpop__source",
        e.notify ? "Notify me on this day" : "No notification"));
    }
    if (e.body && e.body.trim()) pop.appendChild(el("div", "calpop__body", e.body.trim()));

    if (e.kind === "note") {
      pop.appendChild(noteActions(e, fromDay));
    } else {
      var link = el("a", "calpop__link", e.link_label);
      link.href = e.url;
      pop.appendChild(link);
    }

    show(anchor, quiet);
    // Opened by the page after "Next": the button already points past it.
    // Notes are not deadlines, so they leave the button alone.
    if (e.kind === "note") resetNext();
    else if (!quiet) aimNextAfter(e);
    if (fromDay) {
      pop.style.left = Math.min(rect.left, window.innerWidth - pop.offsetWidth - 12) + "px";
      pop.style.top = Math.max(12, Math.min(rect.top, window.innerHeight - pop.offsetHeight - 12)) + "px";
    } else {
      placeBeside(rect);
    }
  }

  /* ---------- 5. day notes ---------- */

  var formTpl = document.getElementById("note-form-tpl");
  var deleteTpl = document.getElementById("note-delete-tpl");
  var urls = document.querySelector("[data-note-edit-url]");

  function noteUrl(kind, pk) {
    // The page carries .../notes/0/edit/ and .../notes/0/delete/; swap the 0.
    return urls.dataset[kind].replace("/0/", "/" + pk + "/");
  }

  function cellFor(dateIso) {
    return document.querySelector('.cal__day[data-date="' + dateIso + '"]');
  }

  function noteActions(e, fromDay) {
    var row = el("div", "notef__actions notef__actions--start");
    var edit = el("button", "notef__btn notef__btn--ghost", "Edit");
    edit.type = "button";
    edit.addEventListener("click", function () {
      openNoteForm(cellFor(e.date), e, fromDay);
    });
    row.appendChild(edit);

    var del = deleteTpl.content.firstElementChild.cloneNode(true);
    del.action = noteUrl("noteDeleteUrl", e.pk);
    var delBtn = del.querySelector("button");
    // Two presses, no browser dialog: the first arms it.
    del.addEventListener("submit", function (ev) {
      if (delBtn.dataset.armed) return;
      ev.preventDefault();
      delBtn.dataset.armed = "1";
      delBtn.textContent = "Confirm delete";
    });
    row.appendChild(del);
    return row;
  }

  function openNoteForm(cell, note, fromDay) {
    if (!formTpl || !cell) return;
    resetNext();
    backTo = fromDay || null;
    pop.replaceChildren();
    pop.className = "calpop calpop--note";
    pop.appendChild(topBar(!!fromDay));

    var form = formTpl.content.firstElementChild.cloneNode(true);
    form.elements.date.value = cell.dataset.date;
    var heading = form.querySelector(".notef__day");
    heading.textContent = (note ? "Edit note \u00b7 " : "New note \u00b7 ") + cell.dataset.dayLabel;
    heading.id = "calpop-title";
    if (note) {
      form.action = noteUrl("noteEditUrl", note.pk);
      form.elements.title.value = note.title;
      form.elements.description.value = note.body || "";
      form.elements.notify.checked = !!note.notify;
    }
    form.querySelector("[data-cancel]").addEventListener("click", close);
    form.addEventListener("submit", function () {
      var save = form.querySelector('button[type="submit"]');
      save.disabled = true;
      save.textContent = "Saving\u2026";
    });
    pop.appendChild(form);

    if (pop.hidden) opener = cell;
    pop.hidden = false;
    placeBeside(cell.getBoundingClientRect());
    form.elements.title.focus({ preventScroll: true });
  }

  document.addEventListener("click", function (ev) {
    var more = ev.target.closest("[data-more]");
    if (more) {
      openDay(more.closest(".cal__day"), more);
      return;
    }
    var item = ev.target.closest(".cal__events .cal__ev");
    if (item && events[item.dataset.event]) {
      openEvent(events[item.dataset.event], item, null);
      return;
    }
    // composedPath, not pop.contains(target): a click inside the popover
    // may have just replaced its contents, detaching the clicked node.
    // The Next button itself must not count as "outside": closing would
    // reset its link before the browser follows it.
    if (ev.target.closest("[data-next]")) return;
    if (!pop.hidden && ev.composedPath().indexOf(pop) === -1) {
      close();      // a click outside only closes, as in Google Calendar
      return;
    }
    // Empty space in a day: write a note.
    var cell = ev.target.closest(".cal__grid .cal__day");
    if (cell && pop.hidden) openNoteForm(cell, null, null);
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") close();
  });

  /* ---------- 3. focus from "Next deadline" ---------- */

  function openFocus() {
    if (!focusId || !events[focusId]) return;
    var chipEl = document.querySelector('.cal__events [data-event="' + focusId + '"]');
    if (!chipEl) return;
    var anchor = chipEl.hidden ? chipEl.closest("[data-events]").querySelector("[data-more]") : chipEl;
    openEvent(events[focusId], anchor, null, true);
  }
  // After fonts load, so the chip is measured where it finally sits.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(openFocus);
  else openFocus();
})();
