/* OSISS landing page. Everything here is an enhancement: the page reads fully without it. */
(function () {
  'use strict';

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, value);
    } catch (e) { /* storage blocked: ignore */ }
    return null;
  }

  /* Theme toggle */
  var root = document.documentElement;
  var themeBtn = document.getElementById('theme');
  function currentTheme() {
    var t = root.getAttribute('data-theme');
    if (t) return t;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      store('osiss-theme', next);
    });
  }

  /* Tabs (ARIA tabs pattern, arrow keys, optional remembered choice) */
  document.querySelectorAll('[data-tabs]').forEach(function (box, bi) {
    var list = box.querySelector(':scope > .tablist');
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    var panels = Array.prototype.slice.call(box.querySelectorAll(':scope > .panel'));
    if (!tabs.length || tabs.length !== panels.length) return;
    var remember = box.getAttribute('data-remember');

    tabs.forEach(function (tab, i) {
      var id = 'tabs' + bi + '-' + i;
      tab.id = id + '-tab';
      panels[i].id = id + '-panel';
      tab.setAttribute('aria-controls', panels[i].id);
      panels[i].setAttribute('aria-labelledby', tab.id);
    });

    function select(i, focus) {
      tabs.forEach(function (tab, j) {
        var on = i === j;
        tab.setAttribute('aria-selected', on ? 'true' : 'false');
        tab.tabIndex = on ? 0 : -1;
        panels[j].hidden = !on;
      });
      if (focus) tabs[i].focus();
      if (remember) store(remember, String(i));
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener('click', function () { select(i, false); });
      tab.addEventListener('keydown', function (e) {
        var n = tabs.length, to = null;
        if (e.key === 'ArrowRight') to = (i + 1) % n;
        else if (e.key === 'ArrowLeft') to = (i - 1 + n) % n;
        else if (e.key === 'Home') to = 0;
        else if (e.key === 'End') to = n - 1;
        if (to !== null) { e.preventDefault(); select(to, true); }
      });
    });

    var start = 0;
    if (remember) {
      var saved = parseInt(store(remember) || '', 10);
      if (saved >= 0 && saved < tabs.length) start = saved;
    }
    select(start, false);
  });

  /* Copy buttons on code blocks */
  var live = document.createElement('div');
  live.className = 'sr';
  live.setAttribute('role', 'status');
  document.body.appendChild(live);

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', '');
      ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy') ? resolve() : reject(); } catch (e) { reject(e); }
      document.body.removeChild(ta);
    });
  }

  document.querySelectorAll('pre.code').forEach(function (pre) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'copy';
    btn.textContent = 'Copy';
    btn.setAttribute('aria-label', 'Copy code');
    btn.addEventListener('click', function () {
      copyText(pre.innerText.replace(/\s+$/, '')).then(function () {
        btn.textContent = 'Copied';
        live.textContent = 'Copied to clipboard';
      }, function () {
        btn.textContent = 'Press Ctrl+C';
        live.textContent = 'Copy failed. Select the text and copy it.';
      }).then(function () {
        setTimeout(function () { btn.textContent = 'Copy'; live.textContent = ''; }, 1800);
      });
    });
    pre.classList.add('has-copy');
    pre.appendChild(btn);
  });

  /* Highlight the nav link of the section being read */
  var links = Array.prototype.slice.call(document.querySelectorAll('#nav a'));
  if ('IntersectionObserver' in window && links.length) {
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { a.classList.remove('on'); });
        var a = byId[en.target.id];
        if (a) a.classList.add('on');
      });
    }, { rootMargin: '-40% 0px -55% 0px' });
    Object.keys(byId).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) io.observe(el);
    });
  }


  /* Hover highlight: wrap each word of the readable text in <span class="w"> (code, buttons and the demo are left alone). */
  (function () {
    var skip = 'pre, code, button, .btn, .n, .demo, mark, .tablist, .copy, script, style, textarea, svg';
    var blocks = document.querySelectorAll('main h1, main h2, main h3, main p, main li, main summary, main figcaption, main td, .site-footer p');
    blocks.forEach(function (block) {
      if (block.closest(skip) && !block.matches('h1,h2,h3,p,li,summary,figcaption')) return;
      if (block.closest('pre, .demo, .tablist, .btn, button')) return;
      var walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, null);
      var nodes = [], n;
      while ((n = walker.nextNode())) {
        if (!n.nodeValue.trim()) continue;
        var el = n.parentElement;
        if (el.closest(skip) || el.classList.contains('w')) continue;
        nodes.push(n);
      }
      nodes.forEach(function (node) {
        var parts = node.nodeValue.split(/(\s+)/), frag = document.createDocumentFragment();
        parts.forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
          var span = document.createElement('span');
          span.className = 'w';
          span.textContent = part;
          frag.appendChild(span);
        });
        node.parentNode.replaceChild(frag, node);
      });
    });
  })();

  /* Reveal on scroll: fade and rise once when an element first enters the viewport. */
  if ('IntersectionObserver' in window && !reduce) {
    var targets = document.querySelectorAll(
      '.section .eyebrow, .section h2, .sub, .card, .split > div, .steps4, .stats, .note, ' +
      '.req, .install > li, .tabs:not(.install .tabs), .other-setups, #faq details'
    );
    var seen = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        en.target.classList.add('in');
        obs.unobserve(en.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    targets.forEach(function (el) {
      var idx = Array.prototype.indexOf.call(el.parentNode.children, el);
      el.style.setProperty('--d', Math.min(idx, 4) * 70 + 'ms');
      el.classList.add('reveal');
      seen.observe(el);
    });
  }

  /* Hero demo: type the query once, then reveal the result. Static final frame without motion. */
  var q = document.getElementById('demo-q');
  var result = document.getElementById('demo-result');
  if (q && result && !reduce) {
    var full = q.getAttribute('data-text') || q.textContent;
    q.textContent = '';
    result.classList.add('pending');
    var i = 0;
    (function tick() {
      i += 1;
      q.textContent = full.slice(0, i);
      if (i < full.length) { setTimeout(tick, 55); }
      else { setTimeout(function () { result.classList.remove('pending'); }, 350); }
    })();
  }
})();
