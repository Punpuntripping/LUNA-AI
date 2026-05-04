/* global React, Luna */
const { useState } = React;
const { Button, Pill, Card, Icon, tokens } = window.Luna;

// Agent selector pill
function AgentSelector({ agent, onChange }) {
  const agents = {
    auto: { label: 'تلقائي', icon: 'sparkles' },
    deep_search: { label: 'بحث معمّق', icon: 'search' },
    extraction: { label: 'استخراج', icon: 'file-search' },
    memory: { label: 'ذاكرة', icon: 'brain' },
    services: { label: 'خدمات', icon: 'file-edit' },
  };
  const cur = agents[agent] || agents.auto;
  const active = agent !== 'auto';
  return React.createElement('div', { style: { marginBottom: 8 } },
    React.createElement('button', {
      onClick: () => {
        const keys = Object.keys(agents);
        const i = keys.indexOf(agent);
        onChange(keys[(i + 1) % keys.length]);
      },
      style: {
        display: 'inline-flex', alignItems: 'center', gap: 6,
        height: 30, padding: '0 12px', borderRadius: 9999,
        border: `1px solid ${active ? tokens.primary : tokens.border}`,
        background: active ? tokens.brandLight : tokens.card,
        color: active ? tokens.primary : tokens.fg,
        fontSize: 14, fontWeight: 500, fontFamily: 'inherit', cursor: 'pointer',
      },
    },
      React.createElement(Icon, { name: cur.icon, size: 14 }),
      cur.label,
      React.createElement(Icon, { name: 'chevron-down', size: 12, style: { opacity: 0.6 } }),
    ),
  );
}

// Chat input with send / plus
function ChatInput({ onSend, onFocus, disabled, streaming }) {
  const [value, setValue] = useState('');
  const handleSend = () => {
    const v = value.trim();
    if (!v) return;
    onSend(v);
    setValue('');
  };
  return React.createElement('div', {
    style: { borderTop: `1px solid ${tokens.border}`, background: tokens.bg, padding: '12px 16px' },
  },
    React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'flex-end' } },
      React.createElement('textarea', {
        value, onChange: (e) => setValue(e.target.value),
        onKeyDown: (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } },
        onFocus,
        placeholder: 'اكتب رسالتك هنا... (اكتب @ لعرض الأوامر)',
        dir: 'rtl',
        disabled: streaming,
        rows: 1,
        style: {
          flex: 1, resize: 'none', borderRadius: 12,
          border: `1px solid ${tokens.border}`, background: tokens.muted,
          padding: '10px 14px', fontSize: 14, fontFamily: 'inherit', color: tokens.fg, outline: 'none',
          lineHeight: 1.5, maxHeight: 144,
        },
      }),
      React.createElement(Button, { variant: 'ghost', size: 'icon', icon: 'plus' }),
      streaming
        ? React.createElement(Button, { variant: 'destructive', size: 'icon', icon: 'square' })
        : React.createElement(Button, { variant: 'default', size: 'icon', icon: 'send', disabled: !value.trim(), onClick: handleSend }),
    ),
  );
}

// Citation pill
function Citation({ law, article }) {
  return React.createElement('button', {
    style: {
      display: 'inline-flex', alignItems: 'center', gap: 6,
      height: 26, padding: '0 12px', borderRadius: 9999,
      border: `1px solid ${tokens.border}`, background: tokens.bg,
      fontSize: 11, fontFamily: 'inherit', cursor: 'pointer', color: tokens.fg,
      marginInlineEnd: 4, marginTop: 6,
    },
  },
    React.createElement(Icon, { name: 'scale', size: 12 }),
    `${law} — مادة ${article}`,
  );
}

// Message bubble
// User  = no bubble (prose style with avatar + name header)
// Assistant = bubble, aligned to RTL start (right edge)
function MessageBubble({ role, model, content, citations, time, userName = 'أنت' }) {
  const isUser = role === 'user';

  if (isUser) {
    // Prose style — avatar + name + time header, then text indented
    return React.createElement('div', {
      style: { marginBottom: 22, display: 'flex', flexDirection: 'column', gap: 6 },
    },
      React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
        React.createElement('div', {
          style: {
            width: 28, height: 28, borderRadius: 9999,
            background: tokens.muted, color: tokens.mutedFg,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 12, fontWeight: 600, flexShrink: 0,
          },
        }, (userName || 'أ').slice(0, 1)),
        React.createElement('span', { style: { fontSize: 13, fontWeight: 600, color: tokens.fg } }, userName),
        time && React.createElement('span', {
          style: { fontSize: 11, color: tokens.mutedFg, marginInlineStart: 'auto' },
        }, time),
      ),
      React.createElement('div', {
        style: {
          fontSize: 14, lineHeight: 1.75, color: tokens.fg,
          paddingInlineStart: 38, whiteSpace: 'pre-wrap',
        },
      }, content),
    );
  }

  // Assistant = bubble, RTL-start (right) aligned. In RTL, flex-start = right edge.
  return React.createElement('div', {
    style: { display: 'flex', marginBottom: 14, justifyContent: 'flex-start' },
  },
    React.createElement('div', {
      style: {
        maxWidth: '85%', padding: '12px 16px', borderRadius: 16,
        background: tokens.card,
        border: `1px solid ${tokens.border}`,
        boxShadow: 'var(--shadow-sm)',
        color: tokens.fg, fontSize: 14, lineHeight: 1.75,
      },
    },
      model && React.createElement('div', {
        style: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, color: tokens.mutedFg, marginBottom: 6 },
      }, React.createElement(Icon, { name: 'bot', size: 12 }), model),
      React.createElement('div', { style: { whiteSpace: 'pre-wrap' } }, content),
      citations && citations.length > 0 && React.createElement('div', { style: { marginTop: 4 } },
        citations.map((c, i) => React.createElement(Citation, { key: i, law: c.law, article: c.article }))
      ),
      React.createElement('div', { style: { fontSize: 10, color: tokens.mutedFg, marginTop: 6 } }, time),
    ),
  );
}

// Template cards
function TemplateCards({ onSelect }) {
  const tmpls = [
    { t: 'عقد إيجار تجاري', d: 'إنشاء مسودة عقد إيجار', f: 'خدمات نهائية', c: { bg: 'oklch(55% 0.18 300 / 0.1)', fg: 'oklch(50% 0.18 300)' }, p: 'أريد إنشاء عقد إيجار تجاري' },
    { t: 'بحث في نظام العمل', d: 'بحث معمق في نظام العمل السعودي', f: 'بحث معمق', c: { bg: 'oklch(60% 0.15 250 / 0.1)', fg: 'oklch(55% 0.15 250)' }, p: 'بحث معمق في نظام العمل السعودي' },
    { t: 'تحليل عقد', d: 'استخراج وتحليل بنود العقد', f: 'استخراج', c: { bg: 'oklch(65% 0.15 50 / 0.1)', fg: 'oklch(55% 0.15 50)' }, p: 'أريد تحليل واستخراج معلومات من العقد المرفق' },
    { t: 'حقوق العامل', d: 'استشارة سريعة حول حقوق العمال', f: 'بحث معمق', c: { bg: 'oklch(60% 0.15 250 / 0.1)', fg: 'oklch(55% 0.15 250)' }, p: 'ما هي حقوق العامل في نظام العمل السعودي؟' },
  ];
  return React.createElement('div', { style: { display: 'flex', gap: 10, overflowX: 'auto', padding: '0 4px 8px' } },
    tmpls.map((t, i) =>
      React.createElement('button', {
        key: i, onClick: () => onSelect(t.p),
        style: {
          display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 6,
          padding: 14, borderRadius: 12, border: `1px solid ${tokens.border}`,
          minWidth: 200, textAlign: 'right', background: tokens.bg, cursor: 'pointer',
          fontFamily: 'inherit', flexShrink: 0,
        },
      },
        React.createElement('span', { style: { fontWeight: 500, fontSize: 14, color: tokens.fg } }, t.t),
        React.createElement('span', { style: { fontSize: 12, color: tokens.mutedFg, lineHeight: 1.5 } }, t.d),
        React.createElement(Pill, { bg: t.c.bg, fg: t.c.fg, style: { marginTop: 4 } }, t.f),
      )
    )
  );
}

window.LunaChat = { AgentSelector, ChatInput, Citation, MessageBubble, TemplateCards };
