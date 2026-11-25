// Classic script, run before the first paint: apply the saved theme so the page does not flash.
(function () {
  var theme = 'system';
  try {
    var saved = JSON.parse(localStorage.getItem('diffdesk.settings') || '{}');
    if (typeof saved.theme === 'string') theme = saved.theme;
  } catch (err) {
    // Unreadable storage: keep the system theme.
  }
  if (theme === 'system') theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
})();
