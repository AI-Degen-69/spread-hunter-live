/* Sidebar-pages prototype (#95) — navigation only.
 *
 * No data fetching, no endpoints, no render logic moved out of app.js. The one
 * behaviour here is which page is showing, and it survives a reload so the
 * operator clicking through the frame does not land back on Home every time.
 */

const PAGES = ['home', 'data-markets', 'strategy', 'trades', 'reports'];
const STORAGE_KEY = 'sh-proto-page';
const DEFAULT_PAGE = 'home';

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

if (typeof module === 'undefined' || !module.exports) {
  initPrototype(document);
}

// Node-only: lets tests drive the navigation without a browser.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PAGES, DEFAULT_PAGE, normalizePage, showPage, setNavOpen, initPrototype };
}
