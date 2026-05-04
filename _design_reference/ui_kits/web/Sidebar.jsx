/* global React, Luna */
const { useState } = React;
const { Button, Pill, Card, Input, Icon, tokens } = window.Luna;

// Sidebar — header + tabs (Conversations / Cases) + footer
function Sidebar({ open, onToggle, active, onSelect, dark, onToggleDark }) {
  const [tab, setTab] = useState('conversations');

  const conversations = [
    { id: 'c1', t: 'حقوق العامل في نظام العمل السعودي', time: 'منذ 5 دقائق' },
    { id: 'c2', t: 'صياغة عقد إيجار تجاري', time: 'أمس' },
    { id: 'c3', t: 'تحليل بنود اتفاقية التوزيع', time: 'منذ يومين' },
    { id: 'c4', t: 'استشارة حول الأحوال الشخصية', time: 'الأسبوع الماضي' },
  ];

  const cases = [
    { id: 'k1', n: 'الدار البيضاء للاستثمار', type: 'عقاري',  icon: 'building-2', prio: 'عالية',  conv: 12, docs: 8 },
    { id: 'k2', n: 'شركة الخليج للتجارة',    type: 'تجاري',  icon: 'briefcase',  prio: 'متوسطة', conv: 5,  docs: 3 },
    { id: 'k3', n: 'نزاع عمالي — مصنع النور', type: 'عمالي',  icon: 'users',      prio: 'منخفضة', conv: 2,  docs: 1 },
  ];

  // Priority keeps semantic color (urgency = meaning); case types are neutral + iconographic
  const prioColors = {
    'عالية':   { bg: 'var(--status-danger-bg)',  fg: 'var(--status-danger-fg)'  },
    'متوسطة':  { bg: 'var(--status-warning-bg)', fg: 'var(--status-warning-fg)' },
    'منخفضة': { bg: 'var(--status-success-bg)', fg: 'var(--status-success-fg)' },
  };

  if (!open) {
    return React.createElement('div', {
      style: { width: 0, overflow: 'hidden', flexShrink: 0 },
    });
  }

  return React.createElement('aside', {
    style: {
      width: 288, flexShrink: 0, display: 'flex', flexDirection: 'column',
      background: tokens.sidebarBg, borderInlineEnd: `1px solid ${tokens.border}`,
      height: '100%',
    },
  },
    // header
    React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: 12, borderBottom: `1px solid ${tokens.border}` },
    },
      React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
        React.createElement('div', {
          style: { width: 32, height: 32, borderRadius: 8, background: tokens.primary,
            color: tokens.primaryFg, display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 12, fontWeight: 700 },
        }, 'لونا'),
        React.createElement('span', { style: { fontSize: 14, fontWeight: 600, color: tokens.fg } }, 'لونا القانونية'),
      ),
      React.createElement(Button, { variant: 'ghost', size: 'iconSm', onClick: onToggle, icon: 'panel-right-close' }),
    ),
    // tabs
    React.createElement('div', { style: { padding: '8px 8px 0', flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } },
      React.createElement('div', {
        style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 2, padding: 3, background: tokens.muted, borderRadius: 6 },
      },
        ['conversations', 'cases'].map(k =>
          React.createElement('button', {
            key: k, onClick: () => setTab(k),
            style: {
              height: 28, border: 'none', borderRadius: 4, fontSize: 12, fontWeight: 500, fontFamily: 'inherit',
              cursor: 'pointer',
              background: tab === k ? tokens.bg : 'transparent',
              color: tab === k ? tokens.fg : tokens.mutedFg,
              boxShadow: tab === k ? 'var(--shadow-sm)' : 'none',
            },
          }, k === 'conversations' ? 'المحادثات' : 'القضايا')
        )
      ),
      // list
      React.createElement('div', { style: { flex: 1, overflow: 'auto', marginTop: 8, paddingBottom: 8 } },
        tab === 'conversations'
          ? conversations.map(c =>
              React.createElement('div', {
                key: c.id,
                onClick: () => onSelect(c.id),
                style: {
                  padding: '10px 12px', borderRadius: 6, marginBottom: 4, cursor: 'pointer',
                  border: `1px solid ${active === c.id ? tokens.primary : tokens.border}`,
                  background: active === c.id ? tokens.accent : 'transparent',
                },
              },
                React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
                  React.createElement(Icon, { name: 'message-square', size: 14, color: tokens.mutedFg }),
                  React.createElement('span', { style: { fontSize: 14, fontWeight: 500, flex: 1, color: tokens.fg } }, c.t),
                ),
                React.createElement('div', { style: { fontSize: 11, color: tokens.mutedFg, marginTop: 4 } }, c.time),
              )
            )
          : cases.map(k =>
              React.createElement('div', {
                key: k.id,
                style: {
                  padding: 10, borderRadius: 6, marginBottom: 6,
                  border: `1px solid ${tokens.border}`,
                  background: 'transparent',
                },
              },
                React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 14, fontWeight: 500 } },
                  React.createElement(Icon, { name: 'briefcase', size: 14, color: tokens.mutedFg }),
                  k.n,
                ),
                React.createElement('div', { style: { display: 'flex', gap: 6, marginTop: 8 } },
                  React.createElement(Pill, { bg: tokens.muted, fg: tokens.fg },
                    React.createElement(Icon, { name: k.icon, size: 10, color: tokens.mutedFg }),
                    k.type,
                  ),
                  React.createElement(Pill, { bg: prioColors[k.prio].bg, fg: prioColors[k.prio].fg }, k.prio),
                ),
                React.createElement('div', { style: { display: 'flex', gap: 14, marginTop: 8, fontSize: 12, color: tokens.mutedFg } },
                  React.createElement('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 4 } },
                    React.createElement(Icon, { name: 'message-square', size: 12 }), k.conv),
                  React.createElement('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 4 } },
                    React.createElement(Icon, { name: 'file-text', size: 12 }), k.docs),
                ),
              )
            )
      ),
    ),
    // footer
    React.createElement('div', { style: { borderTop: `1px solid ${tokens.border}`, padding: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between' } },
      React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 } },
        React.createElement('div', {
          style: { width: 28, height: 28, borderRadius: '50%', background: tokens.muted,
            display: 'flex', alignItems: 'center', justifyContent: 'center' },
        }, React.createElement(Icon, { name: 'user', size: 14, color: tokens.mutedFg })),
        React.createElement('div', { style: { fontSize: 12, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', color: tokens.fg } }, 'المحامية ليلى القحطاني'),
      ),
      React.createElement('div', { style: { display: 'flex', gap: 4 } },
        React.createElement(Button, { variant: 'ghost', size: 'iconSm', icon: dark ? 'sun' : 'moon', onClick: onToggleDark }),
        React.createElement(Button, { variant: 'ghost', size: 'iconSm', icon: 'log-out' }),
      ),
    ),
  );
}

window.LunaSidebar = Sidebar;
