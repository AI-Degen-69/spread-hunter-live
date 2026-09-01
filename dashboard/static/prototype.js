/* Sidebar-pages layout (#95 frame, #140 content move).
 *
 * #120 shipped the frame empty. This fills it with the dashboard's real
 * panels — by moving the live nodes, not by copying their markup. `app.js`
 * finds everything it renders into by id, and ids survive a change of parent,
 * so there is exactly one copy of every panel and no second render path to
 * keep in sync with `index.html`.
 *
 * This is the dashboard now. `/` serves it. `/prototype` stays in
 * `LAYOUT_PATHS` so the path the layout was reviewed through still lands on
 * the layout instead of on a tab row nobody uses any more.
 *
 * Wrapped in an IIFE: this file is loaded into the same global scope as
 * `app.js`, and two classic scripts sharing a top-level `const` name is a
 * load-time error that would take the whole page down.
 */
(function () {
'use strict';

const PAGES =['home', 'data-markets', 'strategy', 'trades', 'reports'];
const STORAGE_KEY = 'sh-proto-page';
const DEFAULT_PAGE = 'home';
const LAYOUT_PATHS = ['/', '/prototype'];
const EXPLAINER_SRC = '/static/strategy_explainer.html';

/* Which live panel lands on which page. Every selector is an id `app.js`
 * already renders into; a selector that no longer matches is skipped rather
 * than throwing, so a renamed panel costs one empty slot instead of the whole
 * layout. */
const PAGE_LAYOUT = [
  {
    page: 'home',
    label: 'Dashboard',
    icon: '◆',
    note: 'Where the account stands and what the run is doing right now: ' +
          'account value, the bankroll split, and the three stages of every ' +
          'trade — quoted market, resting order, held position.',
    // The bankroll strip is part of the portfolio card and travels with it.
    selectors: ['#live-ops-master-card', '#broker-portfolio-overview',
                '#orders-trades-card'],
  },
  {
    page: 'data-markets',
    label: 'Data & Markets',
    icon: '▤',
    note: 'What the Market Filter saw: the pipeline buckets, its own state, ' +
          'and the market inspection table.',
    selectors: ['#screener-header', '#kanban-carousel-container', '#market-inspection-card'],
  },
  {
    page: 'strategy',
    label: 'Strategy',
    icon: '✦',
    note: 'How the strategy is configured and what it has to clear: risk limits, ' +
          'decision gates, and the explainer.',
    selectors: ['#params-panel', '#analytics-gates'],
  },
  {
    page: 'trades',
    label: 'Trades & Positions',
    icon: '⇄',
    note: 'The execution surface: the services running the loop and the live ' +
          'cycle event stream.',
    selectors: ['#services-deck', '#event-ticker-card'],
  },
  {
    page: 'reports',
    label: 'Reports & Analytics',
    icon: '▩',
    note: 'The statistical decks and the performance charts behind the ' +
          'numbers on the Dashboard.',
    selectors: ['.stats-subnav-container', '#analytics-surface'],
  },
];

function pageId(page) {
  return 'page-' + page;
}

// A stored value is data, not a command: anything that is not one of the five
// known pages falls back to Home rather than hiding every panel.
function normalizePage(page) {
  return PAGES.includes(page) ? page : DEFAULT_PAGE;
}

function readStoredPage() {
  try {
    return normalizePage(window.localStorage.getItem(STORAGE_KEY));
  } catch (e) {
    return DEFAULT_PAGE;
  }
}

function storePage(page) {
  try {
    window.localStorage.setItem(STORAGE_KEY, page);
  } catch (e) {
    // A browser with storage disabled still navigates; it just forgets.
  }
}

function showPage(page, doc) {
  const scope = doc || document;
  const target = normalizePage(page);

  for (const candidate of PAGES) {
    const section = scope.getElementById(pageId(candidate));
    if (section) section.hidden = candidate !== target;
  }

  const buttons = scope.querySelectorAll('.proto-nav-btn');
  buttons.forEach(button => {
    const isCurrent = button.dataset.page === target;
    if (isCurrent) {
      button.setAttribute('aria-current', 'page');
    } else {
      button.removeAttribute('aria-current');
    }
  });

  storePage(target);
  // The kanban measures its own scroll width, which reads as zero while the
  // page holding it is hidden. Re-measure once it is on screen.
  if (target === 'data-markets' && typeof updateKanbanNavButtons === 'function') {
    setTimeout(updateKanbanNavButtons, 60);
  }
  return target;
}

function setNavOpen(open, doc) {
  const scope = doc || document;
  const toggle = scope.getElementById('proto-nav-toggle');
  const body = scope.body;
  if (body && body.classList) {
    body.classList.toggle('proto-nav-open', !!open);
  }
  if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function initPrototype(doc) {
  const scope = doc || document;
  showPage(readStoredPage(), scope);

  scope.querySelectorAll('.proto-nav-btn').forEach(button => {
    button.addEventListener('click', () => {
      showPage(button.dataset.page, scope);
      // On a narrow window the rail is a drawer over the page, so a chosen
      // page has to close it or the operator cannot see what they picked.
      setNavOpen(false, scope);
    });
  });

  const toggle = scope.getElementById('proto-nav-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      setNavOpen(open, scope);
    });
  }
}

/* ── Layout mount ───────────────────────────────────────────────────────── */

function buildSidebar(doc) {
  const nav = doc.createElement('nav');
  nav.id = 'proto-sidebar';
  nav.className = 'proto-sidebar';
  nav.setAttribute('aria-label', 'Pages');

  const list = doc.createElement('ul');
  list.className = 'proto-nav';
  list.setAttribute('role', 'list');

  for (const entry of PAGE_LAYOUT) {
    const item = doc.createElement('li');
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'proto-nav-btn';
    button.dataset.page = entry.page;

    const icon = doc.createElement('span');
    icon.className = 'proto-nav-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = entry.icon;

    button.appendChild(icon);
    button.appendChild(doc.createTextNode(' ' + entry.label));
    item.appendChild(button);
    list.appendChild(item);
  }

  nav.appendChild(list);
  return nav;
}

/* Every selector is resolved before anything moves. Appending a panel detaches
 * it from the document, and `querySelector` does not see inside a detached
 * subtree -- so a later selector pointing at a node under an earlier one would
 * silently find nothing and its page would come up short. */
function resolvePanels(doc) {
  const found = {};
  for (const entry of PAGE_LAYOUT) {
    found[entry.page] = entry.selectors
      .map(selector => doc.querySelector(selector))
      .filter(Boolean);
  }
  return found;
}

function buildPage(doc, entry, panels) {
  const section = doc.createElement('section');
  section.id = pageId(entry.page);
  section.className = 'proto-page';
  section.hidden = true;
  section.setAttribute('aria-labelledby', pageId(entry.page) + '-title');

  const title = doc.createElement('h1');
  title.id = pageId(entry.page) + '-title';
  title.className = 'font-display proto-page-title';
  title.textContent = entry.label;

  const note = doc.createElement('p');
  note.className = 'proto-page-note';
  note.textContent = entry.note;

  section.appendChild(title);
  section.appendChild(note);

  for (const panel of panels || []) {
    section.appendChild(panel);
  }

  if (entry.page === 'strategy') section.appendChild(buildExplainer(doc));
  return section;
}

/* The explainer is a whole page of its own. Framing it keeps one copy of that
 * document instead of forking its markup into the dashboard. */
function buildExplainer(doc) {
  const card = doc.createElement('div');
  card.className = 'card proto-explainer-card';
  card.id = 'proto-strategy-explainer';

  const heading = doc.createElement('div');
  heading.className = 'font-display proto-explainer-title';
  heading.textContent = 'HOW THE SPREAD HUNTER STRATEGY WORKS';

  const frame = doc.createElement('iframe');
  frame.className = 'proto-explainer-frame';
  frame.setAttribute('title', 'Strategy explainer');
  frame.setAttribute('loading', 'lazy');
  frame.src = EXPLAINER_SRC;

  card.appendChild(heading);
  card.appendChild(frame);
  return card;
}

function mountSidebarLayout(doc) {
  const scope = doc || document;
  const container = scope.querySelector('.container');
  if (!container || scope.getElementById('proto-sidebar')) return null;

  const shell = scope.createElement('div');
  shell.className = 'proto-shell';

  const pages = scope.createElement('main');
  pages.id = 'proto-page-body';
  pages.className = 'proto-pages';

  const panels = resolvePanels(scope);
  shell.appendChild(buildSidebar(scope));
  for (const entry of PAGE_LAYOUT) {
    pages.appendChild(buildPage(scope, entry, panels[entry.page]));
  }
  shell.appendChild(pages);
  container.appendChild(shell);

  // The tab shells stay in the document, emptied. `app.js` toggles them by id
  // and reads `#tab-3.hidden` before it will scroll the kanban, so removing
  // them would break the keyboard navigation the kanban still needs. Unhiding
  // them costs nothing: they have no children left.
  for (const tab of ['tab-1', 'tab-2', 'tab-3']) {
    const section = scope.getElementById(tab);
    if (section) section.hidden = false;
  }

  const tabs = scope.querySelector('.tab-switcher');
  if (tabs) tabs.hidden = true;

  const header = scope.querySelector('header');
  if (header) header.insertBefore(buildNavToggle(scope), header.firstChild);

  if (scope.body && scope.body.classList) {
    scope.body.classList.add('proto-body');
  }

  // The analytics sub-nav filters the page it sits on, and the panels moved a
  // moment ago. Re-check which of its views still have something to show.
  if (typeof pruneStatsSubnav === 'function') pruneStatsSubnav();
  return shell;
}

function buildNavToggle(doc) {
  const toggle = doc.createElement('button');
  toggle.type = 'button';
  toggle.id = 'proto-nav-toggle';
  toggle.className = 'proto-hamburger';
  toggle.setAttribute('aria-label', 'Toggle navigation');
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'proto-sidebar');
  for (let i = 0; i < 3; i += 1) toggle.appendChild(doc.createElement('span'));
  return toggle;
}

function shouldMount(loc) {
  if (!loc) return false;
  const path = String(loc.pathname || '').replace(/\/+$/, '') || '/';
  return LAYOUT_PATHS.includes(path);
}

if (typeof module === 'undefined' || !module.exports) {
  if (shouldMount(window.location)) {
    mountSidebarLayout(document);
    initPrototype(document);
  }
}

// Node-only: lets tests drive the navigation and read the layout map without a
// browser.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    PAGES, DEFAULT_PAGE, PAGE_LAYOUT, LAYOUT_PATHS,
    normalizePage, showPage, setNavOpen, initPrototype,
    mountSidebarLayout, shouldMount, resolvePanels,
  };
}
})();
