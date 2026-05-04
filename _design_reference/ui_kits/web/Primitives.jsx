/* global React */
const { useState, useEffect } = React;

// Tokens read live from CSS variables on :root, so toggling the .dark class
// on <html> automatically retints every component that uses them.
const cssVar = (name, fallback) => {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
};
const tokens = new Proxy({}, {
  get(_, k) {
    const map = {
      primary:        '--luna-primary',
      primaryFg:      '--luna-primary-foreground',
      primaryHover:   '--luna-primary-hover',
      brandLight:     '--luna-brand-light',
      bg:             '--background',
      fg:             '--foreground',
      muted:          '--muted',
      mutedFg:        '--muted-foreground',
      accent:         '--accent',
      accentFg:       '--accent-foreground',
      border:         '--border',
      sidebarBg:      '--sidebar-background',
      destructive:    '--destructive',
      destructiveFg:  '--destructive-foreground',
      destructiveHover:'--destructive-hover',
      card:           '--card',
      popover:        '--popover',
    };
    return `var(${map[k] || '--foreground'})`;
  },
});

// --- Icon wrapper (Lucide CDN) ---
function Icon({ name, size = 16, color, style }) {
  const ref = React.useRef(null);
  useEffect(() => {
    if (ref.current && window.lucide) {
      ref.current.innerHTML = '';
      const el = document.createElement('i');
      el.setAttribute('data-lucide', name);
      ref.current.appendChild(el);
      window.lucide.createIcons({ nameAttr: 'data-lucide', attrs: { width: size, height: size } });
    }
  }, [name, size]);
  return React.createElement('span', {
    ref,
    style: { display: 'inline-flex', alignItems: 'center', color, ...style },
  });
}

// --- Button ---
function Button({ variant = 'default', size = 'default', icon, iconRight, children, onClick, disabled, style = {}, ...props }) {
  const variants = {
    default:    { bg: tokens.primary,     fg: tokens.primaryFg,     border: 'transparent', hover: tokens.primaryHover },
    secondary:  { bg: tokens.brandLight,  fg: tokens.primary,        border: 'transparent', hover: tokens.accent },
    outline:    { bg: tokens.card,        fg: tokens.fg,             border: tokens.border, hover: tokens.accent },
    ghost:      { bg: 'transparent',      fg: tokens.fg,            border: 'transparent' },
    destructive:{ bg: tokens.destructive, fg: tokens.destructiveFg, border: 'transparent' },
    link:       { bg: 'transparent',      fg: tokens.primary,       border: 'transparent' },
  };
  const sizes = {
    sm: { h: 32, px: 12, fs: 13 },
    default: { h: 40, px: 16, fs: 14 },
    lg: { h: 44, px: 28, fs: 14 },
    icon: { h: 40, px: 0, fs: 14, w: 40 },
    iconSm: { h: 28, px: 0, fs: 12, w: 28 },
  };
  const v = variants[variant];
  const s = sizes[size];
  return React.createElement('button', {
    onClick, disabled,
    style: {
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 6,
      height: s.h, width: s.w, padding: s.w ? 0 : `0 ${s.px}px`,
      fontSize: s.fs, fontWeight: 500, fontFamily: 'inherit',
      background: v.bg, color: v.fg, border: `1px solid ${v.border}`,
      borderRadius: 8, cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.5 : 1, transition: 'background-color 150ms',
      textDecoration: variant === 'link' ? 'underline' : 'none', textUnderlineOffset: 4,
      whiteSpace: 'nowrap', ...style,
    },
    onMouseEnter: (e) => { if (!disabled && v.hover) {
      e.currentTarget.style.background = v.hover;
    } else if (!disabled) {
      if (variant === 'ghost') e.currentTarget.style.background = tokens.accent;
      if (variant === 'destructive') e.currentTarget.style.background = tokens.destructiveHover;
    }},
    onMouseLeave: (e) => { e.currentTarget.style.background = v.bg; },
    ...props,
  }, icon && React.createElement(Icon, { name: icon, size: size === 'sm' ? 14 : 16 }),
     children, iconRight && React.createElement(Icon, { name: iconRight, size: 14 }));
}

// --- Pill / badge ---
function Pill({ bg, fg, children, style = {} }) {
  return React.createElement('span', {
    style: {
      display: 'inline-flex', alignItems: 'center', gap: 4,
      padding: '2px 8px', borderRadius: 9999, fontSize: 10, fontWeight: 500,
      background: bg, color: fg, ...style,
    }
  }, children);
}

// --- Card ---
function Card({ children, style = {}, onClick, hoverable }) {
  const [hover, setHover] = useState(false);
  return React.createElement('div', {
    onClick,
    onMouseEnter: () => setHover(true),
    onMouseLeave: () => setHover(false),
    style: {
      background: tokens.card, border: `1px solid ${tokens.border}`,
      borderRadius: 8, padding: 16,
      boxShadow: hover && hoverable ? 'var(--shadow-md)' : 'var(--shadow-sm)',
      cursor: onClick ? 'pointer' : 'default',
      transition: 'box-shadow 150ms', ...style,
    }
  }, children);
}

// --- Input ---
function Input({ dir = 'rtl', error, style = {}, ...props }) {
  const [focus, setFocus] = useState(false);
  return React.createElement('input', {
    dir,
    onFocus: () => setFocus(true),
    onBlur: () => setFocus(false),
    style: {
      width: '100%', boxSizing: 'border-box', height: 40,
      border: `1px solid ${error ? tokens.destructive : (focus ? 'transparent' : tokens.border)}`,
      background: tokens.card, borderRadius: 6, padding: '0 12px',
      fontSize: 14, fontFamily: 'inherit', color: tokens.fg,
      outline: 'none',
      boxShadow: focus ? `0 0 0 2px ${tokens.primary}` : 'none',
      transition: 'all 150ms',
      ...style,
    },
    ...props,
  });
}

window.Luna = { Button, Pill, Card, Input, Icon, tokens };
