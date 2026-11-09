/* Create Short: check a clip the moment it is picked.

   The browser reads the clip's length from its metadata, so a 2-minute clip
   is turned away before anyone waits for it to upload. This only answers
   sooner: the server measures every clip again and has the final say. If
   the browser cannot read a file (some phone .mov files), nothing is
   blocked here and the server decides. */
(function () {
  "use strict";

  var form = document.querySelector("[data-short-form]");
  if (!form) return;

  var field = form.querySelector("[data-clip-field]");
  var input = field && field.querySelector('input[type="file"]');
  var status = field && field.querySelector("[data-clip-status]");
  var submit = form.querySelector("[data-short-submit]");
  if (!input || !status) return;

  var maxSeconds = Number(form.dataset.maxSeconds) || 90;
  var maxBytes = Number(form.dataset.maxBytes) || 0;
  var problem = "";

  function clock(seconds) {
    var whole = Math.round(seconds);
    var s = whole % 60;
    return Math.floor(whole / 60) + ":" + (s < 10 ? "0" : "") + s;
  }

  function show(message, bad) {
    problem = bad ? message : "";
    status.textContent = message;
    status.classList.toggle("shorts-clip-status--bad", !!bad);
    field.classList.toggle("annform__field--bad", !!bad);
    var server = field.querySelector("[data-clip-server-error]");
    if (server) server.remove(); // it described the previous file
  }

  input.addEventListener("change", function () {
    var file = input.files && input.files[0];
    if (!file) { show("", false); return; }

    if (maxBytes && file.size > maxBytes) {
      show("This clip is " + Math.round(file.size / 1048576) + " MB. The maximum is " +
           Math.round(maxBytes / 1048576) + " MB.", true);
      return;
    }

    show("Checking the clip…", false);
    var url = URL.createObjectURL(file);
    var probe = document.createElement("video");
    probe.preload = "metadata";
    probe.muted = true;

    function done() { URL.revokeObjectURL(url); probe.removeAttribute("src"); probe.load(); }

    probe.addEventListener("loadedmetadata", function () {
      var seconds = probe.duration;
      done();
      if (!isFinite(seconds) || seconds <= 0) { show("", false); return; }
      // Same rounding as the server: to the tenth of a second.
      if (Math.round(seconds * 10) / 10 > maxSeconds) {
        show("This clip is " + clock(seconds) + " long. Shorts can be at most " + clock(maxSeconds) + ".", true);
      } else {
        show("Length " + clock(seconds) + " (limit " + clock(maxSeconds) + ").", false);
      }
    });
    probe.addEventListener("error", function () { done(); show("", false); });
    probe.src = url;
  });

  form.addEventListener("submit", function (e) {
    if (problem) {
      e.preventDefault();
      input.focus();
      return;
    }
    if (submit) {
      var idle = submit.textContent;
      submit.disabled = true;
      submit.textContent = submit.dataset.busyLabel || "Uploading…";
      // Re-enable if the browser restores the page from its back/forward cache.
      window.addEventListener("pageshow", function () {
        submit.disabled = false;
        submit.textContent = idle;
      });
    }
  });
})();
