/**
 * CipherContact - mouse-only glass card tilt.
 * The visual treatment lives in CSS so each theme retains its own glow.
 */
(function () {
  var MAX = 5;
  var attached = new WeakSet();

  function motionAllowed() {
    return !window.matchMedia || !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function applyTilt(card) {
    if (attached.has(card) || !motionAllowed()) return;
    attached.add(card);
    card.style.transformOrigin = 'center center';
    card.addEventListener('pointermove', function (e) {
      if (e.pointerType && e.pointerType !== 'mouse') return;
      var r = card.getBoundingClientRect();
      var dx = (e.clientX - r.left - r.width / 2) / (r.width / 2);
      var dy = (e.clientY - r.top - r.height / 2) / (r.height / 2);
      card.style.transform = 'translateY(-3px) perspective(600px) rotateX(' + (-dy * MAX) + 'deg) rotateY(' + (dx * MAX) + 'deg)';
      card.style.transition = 'transform 0.05s ease-out';
      card.classList.add('is-tilting');
    });
    card.addEventListener('pointerleave', function () {
      card.style.transform = '';
      card.style.transition = '';
      card.classList.remove('is-tilting');
    });
  }

  function initTilts() {
    document.querySelectorAll('.stat-card, [data-glass-tilt]').forEach(applyTilt);
  }

  document.addEventListener('DOMContentLoaded', initTilts);
})();
