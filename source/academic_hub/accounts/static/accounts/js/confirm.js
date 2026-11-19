/* The app's own confirm box, used instead of the browser's confirm().

   Two ways to use it:

   1. On a form, with no script of your own:
        <form method="post" data-confirm="Archive this class?"> ... </form>
      The form only submits after the user presses the confirm button.
      Optional: data-confirm-label="Archive" sets the button's text, and
      data-confirm-tone="danger" makes it red. Both are worked out from the
      message when left out (see guessLabel below).

   2. From a script:
        if (!(await window.askConfirm("Archive this item?"))) return;

   The message's first question becomes the title and anything after it the
   explanation: "Delete Lab 3? It cannot be undone." shows "Delete Lab 3?"
   in bold over "It cannot be undone." */
(function () {
  "use strict";

  var dialog = null;
  var answer = null;   // resolves the promise for the box that is open

  /* Built once, the first time it is needed, so pages that never ask pay
     nothing. A native <dialog>: Escape, focus trapping and the backdrop
     come free. */
  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "confirm";
    dialog.setAttribute("aria-labelledby", "confirm-title");
    dialog.setAttribute("aria-describedby", "confirm-text");
    dialog.innerHTML =
      '<div class="confirm__panel">' +
        '<span class="confirm__icon" aria-hidden="true">' +
          '<svg viewBox="0 0 24 24"><path d="M12 8v5"/><path d="M12 16.5h.01"/>' +
          '<path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>' +
        '</span>' +
        '<h2 class="confirm__title" id="confirm-title"></h2>' +
        '<p class="confirm__text" id="confirm-text"></p>' +
        '<div class="confirm__actions">' +
          '<button type="button" class="confirm__btn confirm__btn--cancel" data-answer="no">Cancel</button>' +
          '<button type="button" class="confirm__btn confirm__btn--ok" data-answer="yes"></button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(dialog);

    dialog.addEventListener("click", function (event) {
      var button = event.target.closest("[data-answer]");
      if (button) close(button.dataset.answer === "yes");
      else if (event.target === dialog) close(false);   // the backdrop
    });
    // Escape: the browser closes the dialog itself; this only answers "no".
    dialog.addEventListener("cancel", function (event) {
      event.preventDefault();
      close(false);
    });
  }

  function close(yes) {
    if (!dialog.open) return;
    dialog.close();
    var done = answer;
    answer = null;
    if (done) done(yes);
  }

  /* "Permanently delete ..." -> Delete, "Remove ..." -> Remove, and so on. */
  function guessLabel(message) {
    var first = message.trim().split(/\s+/)[0].toLowerCase();
    if (first === "permanently" || first === "delete") return "Delete";
    if (first === "remove") return "Remove";
    if (first === "withdraw") return "Withdraw";
    if (first === "archive") return "Archive";
    return "Confirm";
  }

  function isDanger(label) {
    return label === "Delete" || label === "Remove" || label === "Withdraw";
  }

  /* askConfirm(message, {label, tone}) -> Promise<boolean> */
  window.askConfirm = function (message, options) {
    options = options || {};
    if (!dialog) build();
    if (dialog.open) return Promise.resolve(false);   // one question at a time

    var label = options.label || guessLabel(message);
    var danger = options.tone ? options.tone === "danger" : isDanger(label);
    var split = message.indexOf("? ");
    var title = split === -1 ? message : message.slice(0, split + 1);
    var text = split === -1 ? "" : message.slice(split + 2);

    dialog.classList.toggle("confirm--danger", danger);
    dialog.querySelector(".confirm__title").textContent = title;
    var textEl = dialog.querySelector(".confirm__text");
    textEl.textContent = text;
    textEl.hidden = !text;
    dialog.querySelector(".confirm__btn--ok").textContent = label;

    dialog.showModal();
    // Something that cannot be undone starts on Cancel, so a stray Enter
    // does no harm; anything else starts on the action.
    dialog.querySelector(danger ? ".confirm__btn--cancel" : ".confirm__btn--ok").focus();

    return new Promise(function (resolve) { answer = resolve; });
  };

  /* Any form with data-confirm. Listening in the capture phase on the
     document runs this before the form's own submit handlers, so a
     cancelled submit never reaches them (no busy spinner, no request). */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches || !form.matches("form[data-confirm]")) return;
    if (form.dataset.confirmed === "yes") {        // the second, real submit
      delete form.dataset.confirmed;
      return;
    }
    event.preventDefault();
    event.stopPropagation();

    var submitter = event.submitter;
    window.askConfirm(form.dataset.confirm, {
      label: form.dataset.confirmLabel,
      tone: form.dataset.confirmTone,
    }).then(function (yes) {
      if (!yes) return;
      form.dataset.confirmed = "yes";
      if (form.requestSubmit) form.requestSubmit(submitter || undefined);
      else form.submit();
    });
  }, true);
})();
