/* global React, Luna, LunaSidebar, LunaChat */
const { useState, useEffect } = React;
const { Button, Pill, Card, Input, Icon, tokens } = window.Luna;

// ============== Login screen ==============
function LoginScreen({ onLogin }) {
  const [email, setEmail] = useState('laila@firm.sa');
  const [password, setPassword] = useState('••••••••');
  const [mode, setMode] = useState('login');
  return React.createElement('div', {
    style: {
      position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '100%', height: '100%', background: tokens.bg, padding: 16,
    },
  },
    React.createElement('div', { style: { width: '100%', maxWidth: 420 } },
      // hero
      React.createElement('div', { style: { textAlign: 'center', marginBottom: 32 } },
        React.createElement('div', {
          style: {
            margin: '0 auto 16px', width: 64, height: 64, borderRadius: 16,
            background: tokens.primary, color: tokens.primaryFg,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 26, fontWeight: 700,
          },
        }, 'لونا'),
        React.createElement('h1', { style: { fontSize: 28, fontWeight: 700, letterSpacing: '-0.02em', margin: '0 0 4px', color: tokens.fg } },
          mode === 'login' ? 'مرحباً بك في لونا' : 'إنشاء حساب جديد'),
        React.createElement('p', { style: { color: tokens.mutedFg, fontSize: 14, margin: 0 } }, 'المساعد القانوني الذكي'),
      ),
      // form card
      React.createElement(Card, { style: { padding: 24 } },
        mode === 'register' && React.createElement('div', { style: { marginBottom: 16 } },
          React.createElement('label', { style: { display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 6, color: tokens.fg } }, 'الاسم الكامل'),
          React.createElement(Input, { dir: 'rtl', placeholder: 'أدخل اسمك الكامل' }),
        ),
        React.createElement('div', { style: { marginBottom: 16 } },
          React.createElement('label', { style: { display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 6, color: tokens.fg } }, 'البريد الإلكتروني'),
          React.createElement(Input, { dir: 'ltr', value: email, onChange: (e) => setEmail(e.target.value) }),
        ),
        React.createElement('div', { style: { marginBottom: 20 } },
          React.createElement('label', { style: { display: 'block', fontSize: 14, fontWeight: 500, marginBottom: 6, color: tokens.fg } }, 'كلمة المرور'),
          React.createElement(Input, { dir: 'ltr', type: 'password', value: password, onChange: (e) => setPassword(e.target.value) }),
        ),
        React.createElement(Button, { variant: 'default', onClick: onLogin, style: { width: '100%', height: 42 } },
          mode === 'login' ? 'تسجيل الدخول' : 'إنشاء حساب'),
      ),
      // toggle
      React.createElement('div', { style: { textAlign: 'center', marginTop: 20, fontSize: 14, color: tokens.mutedFg } },
        mode === 'login' ? 'ليس لديك حساب؟ ' : 'لديك حساب بالفعل؟ ',
        React.createElement('button', {
          onClick: () => setMode(mode === 'login' ? 'register' : 'login'),
          style: { background: 'none', border: 'none', color: tokens.primary, fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit', fontSize: 14 },
        }, mode === 'login' ? 'إنشاء حساب جديد' : 'تسجيل الدخول'),
      ),
    ),
  );
}

// ============== Artifact panel ==============
function ArtifactPanel({ onClose, artifacts }) {
  const typeColors = {
    report:   { bg: 'oklch(60% 0.15 250 / 0.1)', fg: 'oklch(55% 0.15 250)', label: 'تقرير' },
    contract: { bg: 'oklch(55% 0.18 300 / 0.1)', fg: 'oklch(50% 0.18 300)', label: 'عقد' },
    memo:     { bg: 'oklch(55% 0.16 280 / 0.1)', fg: 'oklch(48% 0.16 280)', label: 'مذكرة' },
    opinion:  { bg: 'oklch(65% 0.15 155 / 0.1)', fg: 'oklch(50% 0.15 155)', label: 'رأي قانوني' },
  };
  return React.createElement('div', {
    style: {
      width: 380, flexShrink: 0, display: 'flex', flexDirection: 'column',
      borderInlineStart: `1px solid ${tokens.border}`, background: tokens.bg, height: '100%',
    },
  },
    React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', gap: 8, borderBottom: `1px solid ${tokens.border}`, padding: '10px 14px' },
    },
      React.createElement('h2', { style: { flex: 1, fontSize: 14, fontWeight: 600, margin: 0 } }, 'المستندات'),
      React.createElement(Button, { variant: 'ghost', size: 'iconSm', icon: 'x', onClick: onClose }),
    ),
    React.createElement('div', { style: { flex: 1, overflow: 'auto', padding: 12 } },
      artifacts.map((a, i) => {
        const tc = typeColors[a.type];
        return React.createElement(Card, {
          key: i, hoverable: true, onClick: () => {},
          style: { padding: 12, marginBottom: 8 },
        },
          React.createElement('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 10 } },
            React.createElement('div', { style: { marginTop: 2 } },
              React.createElement(Icon, { name: 'file-text', size: 16, color: tokens.mutedFg })),
            React.createElement('div', { style: { flex: 1, minWidth: 0 } },
              React.createElement('div', { style: { fontSize: 14, fontWeight: 500, marginBottom: 6, color: tokens.fg } }, a.title),
              React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
                React.createElement(Pill, { bg: tc.bg, fg: tc.fg }, tc.label),
                React.createElement('span', { style: { fontSize: 11, color: tokens.mutedFg } }, a.time),
              ),
            ),
          ),
        );
      }),
    ),
  );
}

// ============== Main chat view ==============
function ChatView({ conversationId, onOpenTemplates }) {
  const { AgentSelector, ChatInput, MessageBubble, TemplateCards } = window.LunaChat;
  const [agent, setAgent] = useState('auto');
  const [artifactsOpen, setArtifactsOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [streaming, setStreaming] = useState(false);

  const artifacts = [
    { type: 'contract', title: 'عقد إيجار تجاري — الدار البيضاء', time: 'منذ دقيقتين' },
    { type: 'report',   title: 'تقرير بحث: حقوق العامل', time: 'منذ ساعة' },
    { type: 'opinion',  title: 'رأي قانوني في نزاع الإيجار', time: 'أمس' },
  ];

  const handleSend = (content) => {
    const userMsg = { role: 'user', content, time: 'الآن' };
    setMessages(m => [...m, userMsg]);
    setStreaming(true);
    setTimeout(() => {
      setMessages(m => [...m, {
        role: 'assistant', model: 'claude-sonnet-4', time: 'الآن',
        content: 'بناءً على نظام العمل السعودي الصادر بالمرسوم الملكي رقم (م/51)، يتمتع العامل بعدة حقوق أساسية:\n\n١. الحق في الأجر العادل وفق العقد.\n٢. ساعات عمل محددة (٨ ساعات يومياً، ٤٨ ساعة أسبوعياً).\n٣. إجازة سنوية مدفوعة الأجر لا تقل عن ٢١ يوماً.\n٤. مكافأة نهاية الخدمة عند انتهاء العقد.',
        citations: [
          { law: 'نظام العمل', article: '٦١' },
          { law: 'نظام العمل', article: '١٠٤' },
        ],
      }]);
      setStreaming(false);
    }, 800);
  };

  return React.createElement('div', { style: { flex: 1, display: 'flex', flexDirection: 'row', minWidth: 0, height: '100%' } },
    artifactsOpen && React.createElement(ArtifactPanel, { onClose: () => setArtifactsOpen(false), artifacts }),
    React.createElement('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 } },
      // header
      React.createElement('div', {
        style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          borderBottom: `1px solid ${tokens.border}`, padding: '8px 16px', flexShrink: 0 },
      },
        React.createElement('h2', { style: { fontSize: 14, fontWeight: 500, color: tokens.mutedFg, margin: 0 } }, 'المحادثة'),
        React.createElement(Button, {
          variant: artifactsOpen ? 'secondary' : 'ghost', size: 'sm', icon: 'file-text',
          onClick: () => setArtifactsOpen(v => !v),
        }, 'المخرجات'),
      ),
      // messages area — flex column, scrollable
      React.createElement('div', { style: { flex: 1, overflow: 'auto', padding: 16, minHeight: 0 } },
        messages.length === 0
          ? React.createElement('div', { style: { maxWidth: 768, margin: '0 auto', textAlign: 'center', paddingTop: 40 } },
              React.createElement('div', {
                style: { margin: '0 auto 16px', width: 56, height: 56, borderRadius: 14,
                  background: tokens.primary, color: tokens.primaryFg,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 22, fontWeight: 700 },
              }, 'لونا'),
              React.createElement('h3', { style: { fontSize: 20, fontWeight: 600, margin: '0 0 8px', color: tokens.fg } }, 'كيف يمكنني مساعدتك اليوم؟'),
              React.createElement('p', { style: { color: tokens.mutedFg, fontSize: 14, margin: 0 } }, 'ابدأ محادثة جديدة أو اختر من القوالب أدناه'),
            )
          : React.createElement('div', { style: { maxWidth: 768, margin: '0 auto' } },
              messages.map((m, i) => React.createElement(MessageBubble, { key: i, ...m })),
              streaming && React.createElement('div', { style: { display: 'flex', justifyContent: 'flex-end', marginBottom: 14 } },
                React.createElement('div', {
                  style: { padding: '10px 14px', borderRadius: 16, background: tokens.bg,
                    border: `1px solid ${tokens.border}`, boxShadow: 'var(--shadow-sm)',
                    display: 'flex', gap: 4 },
                },
                  [0, 1, 2].map(i => React.createElement('span', {
                    key: i,
                    style: {
                      width: 6, height: 6, borderRadius: '50%', background: tokens.mutedFg,
                      animation: `lunaBounce 1.2s ease-in-out ${i * 0.2}s infinite`,
                    },
                  })),
                ),
              ),
            ),
      ),
      // templates + input
      React.createElement('div', { style: { maxWidth: 768, margin: '0 auto', width: '100%', padding: '0 16px' } },
        messages.length === 0 && React.createElement('div', { style: { marginBottom: 8 } },
          React.createElement('div', { style: { fontSize: 13, color: tokens.mutedFg, marginBottom: 10 } }, 'ابدأ محادثة جديدة أو اختر من القوالب:'),
          React.createElement(TemplateCards, { onSelect: handleSend }),
        ),
        React.createElement(AgentSelector, { agent, onChange: setAgent }),
      ),
      React.createElement(ChatInput, { onSend: handleSend, streaming }),
    ),
  );
}

// ============== App shell ==============
function App() {
  const [loggedIn, setLoggedIn] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [active, setActive] = useState('c1');
  const [dark, setDark] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
  }, [dark]);
  const toggleDark = () => setDark(v => !v);

  if (!loggedIn) return React.createElement(LoginScreen, { onLogin: () => setLoggedIn(true) });

  return React.createElement('div', {
    style: { display: 'flex', height: '100vh', width: '100vw', background: tokens.bg, color: tokens.fg, fontFamily: "'IBM Plex Sans Arabic', system-ui, sans-serif" },
  },
    React.createElement(LunaSidebar, {
      open: sidebarOpen,
      onToggle: () => setSidebarOpen(v => !v),
      active, onSelect: setActive,
      dark, onToggleDark: toggleDark,
    }),
    !sidebarOpen && React.createElement('div', { style: { position: 'absolute', top: 12, insetInlineStart: 12, zIndex: 10 } },
      React.createElement(Button, { variant: 'outline', size: 'icon', icon: 'menu', onClick: () => setSidebarOpen(true) }),
    ),
    React.createElement(ChatView, { conversationId: active }),
  );
}

window.LunaApp = App;
