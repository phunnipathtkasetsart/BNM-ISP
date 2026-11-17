/* Manage roles page.
   1. The programme picker shows only while a row's role is Student.
   2. The search box filters the rows by name, ID or email as you type. */
(function () {
  "use strict";

  /* ---------- 1. programme picker follows the role ---------- */
  document.querySelectorAll("[data-current-role]").forEach(function (role) {
    var field = role.closest(".rolerow").querySelector(".rolerow__programme");
    var programme = field.querySelector("select");

    function updateProgramme() {
      var isStudent = role.value === "student";
      field.hidden = !isStudent;
      programme.disabled = !isStudent;
      // Unchanged legacy accounts may have no programme; never assign one implicitly.
      programme.required = isStudent &&
        (role.dataset.currentRole !== "student" || programme.dataset.currentProgramme !== "");
    }

    role.addEventListener("change", updateProgramme);
    updateProgramme();
  });

  /* ---------- 2. search ----------
     Hidden rows stay in the form, so anything already changed on a row that
     is filtered out is still saved. */
  var search = document.querySelector("[data-role-search]");
  var list = document.querySelector("[data-role-list]");
  if (!search || !list) return;

  var rows = Array.prototype.slice.call(list.querySelectorAll(".rolerow"));
  var count = document.querySelector("[data-role-count]");
  var none = list.querySelector("[data-role-none]");

  function filter() {
    // Every word typed must appear somewhere in the row: "som 6810" works.
    var words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    var shown = 0;

    rows.forEach(function (row) {
      var text = row.dataset.search || "";
      var match = words.every(function (word) { return text.indexOf(word) !== -1; });
      row.hidden = !match;
      if (match) shown += 1;
    });

    if (none) none.hidden = shown !== 0;
    if (count) count.textContent = words.length ? "Showing " + shown + " of " + rows.length : "";
    list.scrollTop = 0;
  }

  search.addEventListener("input", filter);
  search.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && search.value) {
      search.value = "";
      filter();
    }
    // Enter would submit the roles form; here it should only search.
    if (event.key === "Enter") event.preventDefault();
  });
})();
