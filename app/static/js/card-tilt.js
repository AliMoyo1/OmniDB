/**
 * CipherContact - stat card hover: lift + subtle 3D mouse-tilt.
 * Ported verbatim from the One For All platform's shared design system
 * (the production variant, MAX:5, not the simplified skill-doc version).
 */
(function () {
  var MAX = 5;
  var attached = new WeakSet();

  function applyTilt(card) {
    if (attached.has(card)) return;
    attached.add(card);
    card.style.transformOrigin = 'center center';
    card.addEventListener('mousemove', function (e) {
      var r = card.getBoundingClientRect();
      var dx = (e.clientX - r.left - r.width / 2) / (r.width / 2);
      var dy = (e.clientY - r.top - r.height / 2) / (r.height / 2);
      card.style.transform = 'translateY(-3px) perspective(600px) rotateX(' + (-dy * MAX) + 'deg) rotateY(' + (dx * MAX) + 'deg)';
      card.style.transition = 'transform 0.05s ease-out, box-shadow 0.2s';
      card.style.boxShadow = '0 12px 32px rgba(0,0,0,0.13)';
    });
    card.addEventListener('mouseleave', function () {
      card.style.transform = '';
      card.style.boxShadow = '';
      card.style.transition = 'transform 0.35s ease-out, box-shadow 0.35s';
    });
  }

  function initTilts() {
    document.querySelectorAll('.stat-card, [data-glass-tilt]').forEach(applyTilt);
  }

  document.addEventListener('DOMContentLoaded', initTilts);
})();
