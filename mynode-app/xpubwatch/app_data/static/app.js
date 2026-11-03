// Confirmation dialogs and a refresh that never discards user input.
(function () {
  "use strict";
  document.addEventListener("submit", function (ev) {
    var msg = ev.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      ev.preventDefault();
    }
  });

  var refresh = parseInt(document.body.getAttribute("data-refresh") || "0", 10);
  if (!refresh) {
    return;
  }
  var dirty = false;
  document.addEventListener("input", function () { dirty = true; });
  window.setInterval(function () {
    var active = document.activeElement;
    var editing = active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
    var openDetails = document.querySelector("details[open] form");
    if (!dirty && !editing && !openDetails && !document.hidden) {
      window.location.reload();
    }
  }, refresh * 1000);
})();
