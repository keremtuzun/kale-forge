// Per-domain branding for the static landing page.
//
// The studio is a Python function and is branded server-side, with no flash. This page is a
// static file on the CDN, so the swap has to happen in the browser. The <title> is set
// synchronously — it exists in <head> before this runs — and the visible wordmark is swapped
// as soon as the DOM is parsed, which is a frame or two on kaleai.vercel.app and nothing at
// all on forge.frcrams.com, where no substitution runs.
//
// External rather than inline because the CSP on "/" pins inline scripts to one sha256 hash;
// 'self' is allowed, so a file needs no CSP change.
(function () {
  var host = location.hostname.toLowerCase();
  if (host.indexOf('kaleai') < 0 && host.indexOf('kale.ai') < 0) return;

  function swap(text) {
    return text.replace(/Rams Forge/g, 'Kale Forge');
  }
  if (document.title) document.title = swap(document.title);

  function paint() {
    var mark = document.querySelector('.brand');
    if (mark) mark.innerHTML = mark.innerHTML.replace('RAMS <b>FORGE</b>', 'KALE <b>FORGE</b>');
    // Text nodes only: rewriting innerHTML wholesale would rebuild the DOM and drop any
    // listener the page has already attached.
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (node.nodeValue.indexOf('Rams Forge') >= 0) node.nodeValue = swap(node.nodeValue);
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', paint);
  } else {
    paint();
  }
})();
