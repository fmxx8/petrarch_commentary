// Reading view: clicking a line loads its commentary into the side panel
// instead of navigating. Every clickable element is a real link, so the page
// still works with this script disabled.
(function () {
  var reader = document.querySelector("[data-reader]");
  var panel = document.getElementById("panel");
  if (!reader || !panel) return;

  var author = reader.dataset.author || "any";
  var placeholder = panel.innerHTML;
  var active = null;

  function clearActive() {
    if (active) active.classList.remove("active");
    active = null;
  }

  function close() {
    // On wide screens the panel stays in the layout, so closing means going
    // back to the placeholder rather than sliding the sheet away.
    panel.classList.remove("open");
    panel.innerHTML = placeholder;
    clearActive();
  }

  function open(link) {
    var params = new URLSearchParams({ poem: link.dataset.p, author: author });
    if (link.dataset.l) params.set("line", link.dataset.l);

    clearActive();
    active = link.closest("li") || link.closest("h2") || link;
    active.classList.add("active");

    panel.classList.add("open", "loading");
    fetch("/fragment/commentary?" + params.toString())
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.text();
      })
      .then(function (html) {
        panel.innerHTML = html;
        panel.classList.remove("loading");
        panel.scrollTop = 0;
      })
      .catch(function () {
        window.location = link.href;
      });
  }

  reader.addEventListener("click", function (event) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    var link = event.target.closest("a[data-p]");
    if (!link) return;
    event.preventDefault();
    open(link);
  });

  panel.addEventListener("click", function (event) {
    if (event.target.closest("[data-close]")) close();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") close();
  });

  var focus = reader.dataset.focus;
  if (focus) {
    var target = document.getElementById("p" + focus);
    if (target) target.scrollIntoView();
  }
})();
