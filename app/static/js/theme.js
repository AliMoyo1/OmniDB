(function () {
  var saved = localStorage.getItem('cc-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
})();

function ccToggleTheme() {
  var current = document.documentElement.getAttribute('data-theme');
  var next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('cc-theme', next);
}

document.addEventListener('click', function (event) {
  if (event.target.closest('.theme-toggle')) ccToggleTheme();
});
