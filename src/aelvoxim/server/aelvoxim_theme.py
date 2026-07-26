"""
Aelvoxim Unified Theme — shared CSS variables, dark mode, i18n infrastructure.
All server-served HTML pages (portal, admin_panel, API docs, dashboard)
use this module for consistent branding.

Usage:
    from .aelvoxim_theme import inject_theme
    html = inject_theme(Path("page.html").read_text(), title="Page")
"""

from html import escape

# ── Brand Color Palette (matches tailwind brand) ──
# Blue-purple gradient
BRAND_CSS_VARS = """
  --brand-50: #f0f5ff;
  --brand-100: #e0eaff;
  --brand-200: #c7d7fe;
  --brand-300: #a4bcfd;
  --brand-400: #8098f9;
  --brand-500: #6172f3;
  --brand-600: #444ce7;
  --brand-700: #3538cd;
  --brand-800: #2d31a6;
  --brand-900: #2b2f83;
  --brand-950: #1a1c4e;
"""

THEME_CSS = f"""
/* ═══════════════════════════════════════
   Aelvoxim Unified Theme — Shared
   ═══════════════════════════════════════ */
:root {{
{BRAND_CSS_VARS}
  /* Light theme (default) */
  --bg-base: #ffffff;
  --bg-surface: #f8f9fa;
  --bg-elevated: #ffffff;
  --bg-hover: #f0f2f5;
  --text-primary: #1a1a2e;
  --text-secondary: #6b7280;
  --text-muted: #9ca3af;
  --border: #e0e0e0;
  --border-light: #f0f0f0;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06);
  --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.07), 0 2px 4px -1px rgba(0,0,0,0.04);
  --gradient-brand: linear-gradient(135deg, var(--brand-500) 0%, var(--brand-600) 50%, var(--brand-700) 100%);
  --gradient-brand-text: linear-gradient(135deg, var(--brand-500), var(--brand-600));
}}

/* Dark theme */
.dark {{
  --bg-base: #0f1117;
  --bg-surface: #1a1b23;
  --bg-elevated: #22232d;
  --bg-hover: #2a2b36;
  --text-primary: #e4e6eb;
  --text-secondary: #9ca3af;
  --text-muted: #6b7280;
  --border: #2d2d3a;
  --border-light: #22232d;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.2);
  --shadow: 0 1px 3px rgba(0,0,0,0.3), 0 1px 2px rgba(0,0,0,0.2);
  --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.3), 0 2px 4px -1px rgba(0,0,0,0.2);
}}

/* Base styles */
.theme-body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  background: var(--bg-base);
  color: var(--text-primary);
  transition: background 0.2s, color 0.2s;
}}

/* Brand gradient button */
.btn-primary {{
  background: var(--gradient-brand);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 6px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.2s, transform 0.15s;
}}
.btn-primary:hover {{ opacity: 0.9; transform: translateY(-1px); }}
.btn-primary:active {{ transform: translateY(0); }}

/* Ghost button */
.btn-ghost {{
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}}
.btn-ghost:hover {{ background: var(--bg-hover); }}

/* Theme toggle button */
.theme-btn {{
  background: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  color: var(--text-secondary);
  transition: background 0.15s;
}}
.theme-btn:hover {{ background: var(--bg-hover); }}

/* Language toggle */
.lang-btn {{
  background: none;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 10px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  transition: all 0.15s;
}}
.lang-btn:hover {{ background: var(--bg-hover); color: var(--text-primary); }}
.lang-btn.active {{
  background: var(--gradient-brand);
  color: #fff;
  border-color: transparent;
}}

/* Card */
.card {{
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 18px 20px;
  box-shadow: var(--shadow-sm);
}}

/* Badge */
.badge {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}}
.badge.active {{ background: #dcfce7; color: #166534; }}
.badge.completed {{ background: var(--bg-surface); color: var(--text-secondary); }}
.badge.mastery {{ background: #fef3c7; color: #92400e; }}
.dark .badge.active {{ background: rgba(34,197,94,0.15); color: #4ade80; }}
.dark .badge.completed {{ background: rgba(255,255,255,0.05); color: var(--text-muted); }}
.dark .badge.mastery {{ background: rgba(251,191,36,0.15); color: #fbbf24; }}

/* Tag */
.tag {{
  display: inline-block;
  padding: 2px 8px;
  margin: 2px;
  background: var(--brand-50);
  color: var(--brand-700);
  border-radius: 4px;
  font-size: 11px;
}}
.dark .tag {{ background: rgba(97,114,243,0.15); color: var(--brand-300); }}

/* Table */
.theme-table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--bg-elevated);
  border-radius: 10px;
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}}
.theme-table th {{
  background: var(--bg-surface);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
  padding: 10px 14px;
  text-align: left;
  font-weight: 600;
}}
.theme-table td {{
  padding: 8px 14px;
  border-top: 1px solid var(--border-light);
  font-size: 13px;
}}
.theme-table tr:hover td {{ background: var(--bg-hover); }}

/* Loading */
.loading {{
  text-align: center;
  padding: 40px;
  color: var(--text-muted);
  font-size: 13px;
}}

/* Footer */
.theme-footer {{
  text-align: center;
  font-size: 11px;
  color: var(--text-muted);
  padding: 20px 0 10px;
}}

/* Status dot */
.status-dot {{
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
}}
.status-dot.green {{ background: #22c55e; }}
.status-dot.yellow {{ background: #eab308; }}
.status-dot.red {{ background: #ef4444; }}

/* Scrollbar */
.theme-scroll::-webkit-scrollbar {{ width: 4px; }}
.theme-scroll::-webkit-scrollbar-thumb {{ background: var(--text-muted); border-radius: 2px; }}
.theme-scroll::-webkit-scrollbar-track {{ background: transparent; }}
"""

THEME_JS = """
<script>
// ═══════════════════════════════════════
// Aelvoxim Unified Theme — Dark Mode Toggle
// ═══════════════════════════════════════
(function() {
  var key = 'ael_theme';
  var saved = localStorage.getItem(key);
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var isDark = saved ? saved === 'dark' : prefersDark;

  function apply(theme) {
    document.documentElement.classList.toggle('dark', theme === 'dark');
    localStorage.setItem(key, theme);
  }

  // Apply on load
  apply(isDark ? 'dark' : 'light');

  // Initialize toggle button text
  function initThemeBtn() {
    var btn = document.getElementById('themeToggleBtn');
    if (btn) btn.textContent = isDark ? '\\uD83C\\uDF19' : '\\u2600\\uFE0F';
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemeBtn);
  } else {
    initThemeBtn();
  }

  // Expose toggle globally
  window.toggleTheme = function() {
    var now = document.documentElement.classList.contains('dark');
    apply(now ? 'light' : 'dark');
    var btn = document.getElementById('themeToggleBtn');
    if (btn) btn.textContent = now ? '\\u2600\\uFE0F' : '\\uD83C\\uDF19';
  };
})();
</script>
"""

I18N_BOILERPLATE = """
<script>
// ═══════════════════════════════════════
// Aelvoxim i18n — shared infrastructure
// ═══════════════════════════════════════
var _LANG = localStorage.getItem('ael_lang') || 'zh';
var _I18N = {};

function t(key) {
  var m = _I18N[_LANG] || _I18N['zh'] || {};
  return m[key] !== undefined ? m[key] : key;
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(function(el) {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
}

function setLang(lang) {
  _LANG = lang;
  localStorage.setItem('ael_lang', lang);
  // Update toggle buttons
  document.querySelectorAll('.lang-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.getAttribute('data-lang') === lang);
  });
  applyI18n();
  if (window._onLangChange) window._onLangChange();
}

// Initialize lang buttons
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.lang-btn').forEach(function(btn) {
    btn.addEventListener('click', function() { setLang(this.getAttribute('data-lang')); });
    btn.classList.toggle('active', btn.getAttribute('data-lang') === _LANG);
  });
  applyI18n();
});
</script>
"""

THEME_BUTTONS = """
<div style="display:flex;align-items:center;gap:6px">
  <button class="lang-btn" data-lang="zh" data-i18n="">中</button>
  <button class="lang-btn" data-lang="en" data-i18n="">EN</button>
  <button class="theme-btn" id="themeToggleBtn">☀️</button>
</div>
"""


def inject_theme(html: str, title: str = "Aelvoxim") -> str:
    """Inject Aelvoxim unified theme into an HTML page.

    Adds:
    - CSS custom properties (light + dark)
    - Dark mode toggle script
    - i18n infrastructure
    - Theme toggle button in header area
    - Brand styling classes
    """
    # 1. Inject CSS before </head>
    css_block = f"<style>{THEME_CSS}</style>"
    html = html.replace("</head>", f"{css_block}\n</head>")

    # 2. Inject theme JS before </body>
    html = html.replace("</body>", f"{THEME_JS}\n</body>")

    # 3. Add theme-body class to <body> if it doesn't already have a class
    import re
    body_match = re.search(r'<body(\s[^>]*)?>', html)
    if body_match:
        attrs = body_match.group(1) or ''
        if 'class=' not in attrs:
            html = html.replace('<body' + attrs + '>', '<body class="theme-body"' + attrs + '>')

    return html


def inject_i18n(html: str) -> str:
    """Inject i18n infrastructure into an HTML page."""
    html = html.replace("</body>", f"{I18N_BOILERPLATE}\n</body>")

    # Add i18n to head if not present
    if 'data-i18n' not in html:
        # Add a script tag to the head
        pass  # The boilerplate handles it

    return html
