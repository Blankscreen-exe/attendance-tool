// Tooltips for the timeline chart.
// Pointing at a mark shows that stretch of work or break. Focusing a row with
// the keyboard shows the whole row in words. The text is always set with
// textContent, never as HTML.
(function () {
  document.querySelectorAll("[data-timeline]").forEach(function (chart) {
    var tip = chart.querySelector("[data-timeline-tip]");
    var value = tip.querySelector('[data-tip-slot="value"]');
    var label = tip.querySelector('[data-tip-slot="label"]');

    function show(target, x, y) {
      value.textContent = target.dataset.tipValue;
      label.textContent = target.dataset.tipLabel;
      tip.hidden = false;
      var box = tip.getBoundingClientRect();
      var left = Math.min(Math.max(8, x - box.width / 2), window.innerWidth - box.width - 8);
      var top = y - box.height - 12;
      if (top < 8) top = y + 20;
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    }

    function hide() {
      tip.hidden = true;
    }

    function point(event) {
      var target = event.target.closest("[data-tip-value]");
      if (!target || !chart.contains(target) || target.dataset.tipOn === "focus") {
        hide();
        return;
      }
      show(target, event.clientX, event.clientY);
    }

    chart.addEventListener("pointermove", point);
    chart.addEventListener("pointerdown", point);
    chart.addEventListener("pointerleave", hide);
    chart.addEventListener("focusin", function (event) {
      var target = event.target.closest("[data-tip-value]");
      if (!target) return;
      var box = target.getBoundingClientRect();
      show(target, box.left + box.width / 2, box.top);
    });
    chart.addEventListener("focusout", hide);
  });
})();
