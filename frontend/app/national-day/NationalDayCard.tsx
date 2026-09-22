"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CARD_H,
  CARD_W,
  COMPANY_MAX,
  DEFAULT_GREETING,
  GREETING_MAX,
  NAME_MAX,
  drawCard,
  sanitizeName,
  type CardAssets,
} from "./card";
import {
  DEFAULT_FONT,
  FONTS,
  ensureFont,
  stackFor,
  type FontEntry,
} from "./fonts";
import { COLOURWAYS, DEFAULT_COLOURWAY, type Colourway } from "./motif";
import "./nd96.css";

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`asset failed: ${src}`));
    img.src = src;
  });
}

// The faces must be resident before the first draw: a canvas paints whatever
// is loaded at fillText time, and an unloaded face would be baked into the PNG
// as Tahoma with no second chance.
async function loadFaces(): Promise<void> {
  if (typeof document === "undefined" || !document.fonts) return;
  await Promise.all([
    document.fonts.load('800 104px "ND96 Camel"', "اسم"),
    document.fonts.load('500 56px "ND96 Camel"', "اسم"),
    document.fonts.load('500 30px "ND96 Domain"', "rayhanai.com"),
  ]);
  await document.fonts.ready;
}

export function NationalDayCard() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const assetsRef = useRef<CardAssets | null>(null);
  const [name, setName] = useState("");
  const [greeting, setGreeting] = useState("");
  const [company, setCompany] = useState("");
  const [colourway, setColourway] = useState<Colourway>(DEFAULT_COLOURWAY);
  const [font, setFont] = useState<FontEntry>(DEFAULT_FONT);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [lockup, subline] = await Promise.all([
          loadImage("/nd96/lockup.png"),
          loadImage("/nd96/subline.png"),
        ]);
        await loadFaces();
        if (!live) return;
        assetsRef.current = { lockup, subline };
        setReady(true);
      } catch {
        if (live) setFailed(true);
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    const assets = assetsRef.current;
    if (!canvas || !assets) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    drawCard(ctx, assets, { name, greeting, company }, colourway, font);
  }, [name, greeting, company, colourway, font]);

  useEffect(() => {
    if (ready) paint();
  }, [ready, paint]);

  const toBlob = useCallback(
    () =>
      new Promise<Blob | null>((resolve) => {
        const canvas = canvasRef.current;
        if (!canvas) return resolve(null);
        canvas.toBlob((b) => resolve(b), "image/png");
      }),
    [],
  );

  const fileName = useCallback(() => {
    const clean = sanitizeName(name);
    return clean
      ? `تهنئة-اليوم-الوطني-${clean.replace(/\s+/g, "-")}.png`
      : "تهنئة-اليوم-الوطني.png";
  }, [name]);

  const onDownload = useCallback(async () => {
    setBusy(true);
    try {
      const blob = await toBlob();
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName();
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoked on the next frame — Safari cancels the download if the URL
      // dies in the same tick as the click.
      requestAnimationFrame(() => URL.revokeObjectURL(url));
    } finally {
      setBusy(false);
    }
  }, [toBlob, fileName]);

  const onShare = useCallback(async () => {
    setBusy(true);
    try {
      const blob = await toBlob();
      if (!blob) return;
      const file = new File([blob], fileName(), { type: "image/png" });
      if (navigator.canShare?.({ files: [file] })) {
        await navigator.share({ files: [file] });
      } else {
        await onDownload();
      }
    } catch {
      /* the visitor dismissed the sheet — nothing to report */
    } finally {
      setBusy(false);
    }
  }, [toBlob, onDownload, fileName]);

  // Which faces have arrived, so a chip can be set in its own font once it is
  // safe to do so — and stay in the UI face until then rather than flashing.
  const [shown, setShown] = useState<string[]>([DEFAULT_FONT.id]);

  const warmFont = useCallback(async (entry: FontEntry) => {
    try {
      await ensureFont(entry);
      setShown((s) => (s.includes(entry.id) ? s : [...s, entry.id]));
    } catch {
      /* a chip that will not load simply stays in the UI face */
    }
  }, []);

  const pickFont = useCallback(
    async (next: FontEntry) => {
      // Fetch before switching. Setting the font first would paint one frame
      // of the card in Camel at the new face's scale — a visible jolt.
      await warmFont(next);
      setFont(next);
    },
    [warmFont],
  );

  // Resolved after mount, never during render: `navigator` does not exist on
  // the server, so branching on it inline renders a different tree there than
  // in the browser and React throws a hydration mismatch.
  const [canShare, setCanShare] = useState(false);
  useEffect(() => {
    setCanShare(typeof navigator.share === "function");
  }, []);

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 md:py-10">
      {/* The heading spans both columns. Left inside the controls column it
          would be wrapping across a ~200px measure on a phone, which is where
          the two-column layout leaves it. */}
      <header className="mb-6 md:mb-8">
        <h1
          className="nd96-display text-2xl leading-tight sm:text-3xl md:text-4xl"
          style={{ color: "var(--text-primary)" }}
        >
          هنّئ باليوم الوطني 96
        </h1>
        <p
          className="mt-2 text-sm leading-relaxed sm:text-base"
          style={{ color: "var(--text-secondary)" }}
        >
          اكتب اسمك وعبارة تهنئتك، اختر لون البطاقة والخط، ثم احفظها وشاركها.
        </p>
      </header>

      {/* Side by side at every width. The card sticks so it stays in view
          while the fields below it scroll — beside the options is only useful
          if it is still there once you reach the last of them. */}
      <div className="flex flex-row-reverse items-start gap-4 md:gap-12">
        {/* the card */}
        {/* 81px = the site header (sticky top-0, 65px in SiteHeader) + 16px of
            air. Pinning at top-4 instead slides the card's upper Sadu band
            under that header on every scroll. */}
        <div className="sticky top-[81px] w-[75%] max-w-[380px] shrink-0 self-start md:w-[380px]">
        <canvas
          ref={canvasRef}
          width={CARD_W}
          height={CARD_H}
          className="nd96-canvas"
          aria-label={
            sanitizeName(name)
              ? `بطاقة تهنئة باليوم الوطني باسم ${sanitizeName(name)}`
              : "بطاقة تهنئة باليوم الوطني"
          }
          role="img"
        />
        {failed ? (
          <p className="mt-3 text-center text-sm text-red-700">
            تعذّر تحميل عناصر البطاقة. حدّث الصفحة من فضلك.
          </p>
        ) : null}
      </div>

        {/* the controls */}
        <div className="flex min-w-0 flex-1 flex-col gap-3 sm:gap-5 md:gap-7"
          style={
            {
              "--nd96-face": stackFor(font),
              // Capped: see .nd96-field in nd96.css.
              "--nd96-face-scale": String(Math.min(font.scale, 1.35)),
            } as React.CSSProperties
          }>
        <div className="flex flex-col gap-2">
          <label
            htmlFor="nd96-name"
            className="text-[11px] font-semibold leading-tight sm:text-sm"
            style={{ color: "var(--text-primary)" }}
          >
            الاسم
          </label>
          <input
            id="nd96-name"
            type="text"
            value={name}
            maxLength={NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            placeholder="مثال: عبدالله محمد"
            autoComplete="name"
            enterKeyHint="done"
            className="nd96-field-lg w-full rounded-lg border px-1.5 py-1.5 outline-none transition focus:ring-2 sm:rounded-xl sm:px-4 sm:py-3"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface-1)",
              color: "var(--text-primary)",
            }}
          />
          <span className="text-[10px] sm:text-xs" style={{ color: "var(--text-tertiary)" }}>
            {name.length}/{NAME_MAX}
          </span>
        </div>

        <div className="flex flex-col gap-2">
          <label
            htmlFor="nd96-company"
            className="text-[11px] font-semibold leading-tight sm:text-sm"
            style={{ color: "var(--text-primary)" }}
          >
            الشركة{" "}
            <span
              className="font-normal"
              style={{ color: "var(--text-tertiary)" }}
            >
              (اختياري)
            </span>
          </label>
          <input
            id="nd96-company"
            type="text"
            value={company}
            maxLength={COMPANY_MAX}
            onChange={(e) => setCompany(e.target.value)}
            placeholder="مثال: شركة ريحان"
            autoComplete="organization"
            enterKeyHint="done"
            className="nd96-field w-full rounded-lg border px-1.5 py-1.5 outline-none transition focus:ring-2 sm:rounded-xl sm:px-4 sm:py-3"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface-1)",
              color: "var(--text-primary)",
            }}
          />
        </div>

        <div className="flex flex-col gap-2">
          <label
            htmlFor="nd96-greeting"
            className="text-[11px] font-semibold leading-tight sm:text-sm"
            style={{ color: "var(--text-primary)" }}
          >
            عبارة التهنئة
          </label>
          <textarea
            id="nd96-greeting"
            value={greeting}
            maxLength={GREETING_MAX}
            rows={2}
            onChange={(e) => setGreeting(e.target.value.replace(/\n/g, " "))}
            placeholder={DEFAULT_GREETING}
            className="nd96-field w-full resize-none rounded-lg border px-1.5 py-1.5 leading-relaxed outline-none transition focus:ring-2 sm:rounded-xl sm:px-4 sm:py-3"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface-1)",
              color: "var(--text-primary)",
            }}
          />
          <span className="text-[10px] sm:text-xs" style={{ color: "var(--text-tertiary)" }}>
            {greeting.length}/{GREETING_MAX}
            <span className="hidden sm:inline">
              {" "}
              — اتركها فارغة لاستخدام «{DEFAULT_GREETING}»
            </span>
          </span>
        </div>

        <fieldset className="flex flex-col gap-3">
          <legend
            className="mb-0.5 text-[11px] font-semibold leading-tight sm:mb-1 sm:text-sm"
            style={{ color: "var(--text-primary)" }}
          >
            لون البطاقة
          </legend>
          <div className="flex flex-wrap gap-1.5 sm:gap-3">
            {COLOURWAYS.map((c) => (
              <button
                key={c.id}
                type="button"
                className="nd96-swatch"
                aria-pressed={c.id === colourway.id}
                aria-label={c.label}
                title={c.label}
                onClick={() => setColourway(c)}
              >
                <span
                  style={{
                    background: c.ground,
                    boxShadow: `inset 0 0 0 5px ${c.frame}`,
                  }}
                />
              </button>
            ))}
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-3">
          <legend
            className="mb-0.5 text-[11px] font-semibold leading-tight sm:mb-1 sm:text-sm"
            style={{ color: "var(--text-primary)" }}
          >
            الخط
          </legend>
          {/* Narrow column: the eight chips become eight stacked rows at ~70px,
              so a native select carries them in one. Chips return at sm, where
              they can preview each face in itself. */}
          <select
            value={font.id}
            onChange={(e) => {
              const next = FONTS.find((f) => f.id === e.target.value);
              if (next) pickFont(next);
            }}
            aria-label="الخط"
            className="w-full rounded-lg border px-1.5 py-1.5 text-[11px] outline-none sm:hidden"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface-1)",
              color: "var(--text-primary)",
            }}
          >
            {FONTS.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>

          <div className="hidden flex-wrap gap-2 sm:flex">
            {FONTS.map((f) => {
              const on = f.id === font.id;
              return (
                <button
                  key={f.id}
                  type="button"
                  aria-pressed={on}
                  onClick={() => pickFont(f)}
                  // Hovering or tabbing to a chip fetches its face, so the
                  // label can preview itself before the visitor commits.
                  onPointerEnter={() => warmFont(f)}
                  onFocus={() => warmFont(f)}
                  className="rounded-lg border px-2 py-1.5 text-sm transition sm:px-3 sm:py-2 sm:text-base"
                  style={{
                    fontFamily: shown.includes(f.id) ? stackFor(f) : undefined,
                    borderColor: on ? "var(--primary)" : "var(--border)",
                    background: on ? "var(--primary)" : "var(--surface-1)",
                    color: on ? "#FFFFFF" : "var(--text-primary)",
                  }}
                >
                  {f.label}
                </button>
              );
            })}
          </div>
        </fieldset>

        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-3">
          <button
            type="button"
            onClick={onDownload}
            disabled={!ready || busy}
            className="flex-1 rounded-lg px-2 py-2 text-[11px] font-semibold leading-tight text-white transition disabled:opacity-50 sm:flex-none sm:rounded-xl sm:px-6 sm:py-3 sm:text-base"
            style={{ background: "var(--primary)" }}
          >
            {busy ? "…" : "حفظ البطاقة"}
          </button>
          {canShare ? (
            <button
              type="button"
              onClick={onShare}
              disabled={!ready || busy}
              className="flex-1 rounded-lg border px-2 py-2 text-[11px] font-semibold leading-tight transition disabled:opacity-50 sm:flex-none sm:rounded-xl sm:px-6 sm:py-3 sm:text-base"
              style={{
                borderColor: "var(--border)",
                color: "var(--text-primary)",
              }}
            >
              مشاركة
            </button>
          ) : null}
        </div>

        <div
          className="flex flex-col gap-1 text-[10px] leading-relaxed sm:text-xs"
          style={{ color: "var(--text-tertiary)" }}
        >
          <p>
            جميع الخطوط المستخدمة في هذه البطاقة هي الخطوط السعودية، وجميع
            حقوقها مملوكة لـ
            <a
              href="https://engage.moc.gov.sa/e/fonts/saudi-font/?lang=ar"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
              style={{ color: "var(--text-secondary)" }}
            >
              {" "}
              وزارة الثقافة
            </a>
            .
          </p>
        </div>
        </div>
      </div>
    </div>
  );
}
