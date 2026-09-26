"use client";

import { MoreHorizontal, MoreVertical, PlusSquare, Share } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { InstallPlatform } from "@/lib/install-app";

/**
 * «ثبّت ريحان» — the ONE copy of the install instructions. Used by the settings
 * dialog (`InstallAppDialog`), the chat nudge's «كيف؟» (through that dialog) and
 * the public `/about_us/app` page. The iOS wording changes with every Safari redesign,
 * so it lives here and nowhere else.
 */

interface Step {
  icon?: React.ComponentType<{ className?: string }>;
  text: React.ReactNode;
  /** Optional mock-up drawn under the step text. */
  illustration?: React.ReactNode;
}

// ---------------------------------------------------------------------------
// Illustrations — ORIGINAL line drawings, not Apple screenshots or glyphs.
// Every colour is `currentColor` under a theme text token, so both follow the
// light/dark theme with no second asset.
// ---------------------------------------------------------------------------

/** A generic phone browser bottom bar with the Share glyph ringed. */
function BrowserBarIllustration() {
  return (
    <svg
      viewBox="0 0 240 72"
      role="img"
      aria-label="شريط المتصفح أسفل الشاشة، وزر المشاركة مميّز"
      className="h-auto w-full max-w-[15rem]"
    >
      {/* Bar surface */}
      <rect x="1" y="1" width="238" height="70" rx="14" className="text-muted" fill="currentColor" />
      <rect x="1" y="1" width="238" height="70" rx="14" className="text-border" fill="none" stroke="currentColor" strokeWidth="1.5" />
      {/* Address pill */}
      <rect x="16" y="9" width="208" height="20" rx="10" className="text-background" fill="currentColor" />
      <text
        x="120"
        y="23"
        textAnchor="middle"
        fontSize="10"
        className="text-muted-foreground"
        fill="currentColor"
      >
        rayhanai.com
      </text>
      <g className="text-muted-foreground" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
        {/* back / forward chevrons */}
        <path d="M34 44 l-6 6 l6 6" />
        <path d="M66 44 l6 6 l-6 6" />
        {/* book */}
        <path d="M160 45 h8 a3 3 0 0 1 3 3 v10 a3 3 0 0 0 -3 -3 h-8 z" />
        {/* tabs: two overlapping squares */}
        <rect x="200" y="46" width="11" height="11" rx="2" />
        <path d="M204 43 h8 a2 2 0 0 1 2 2 v8" />
      </g>
      {/* Share — highlighted */}
      <g className="text-primary">
        <circle cx="120" cy="50" r="15" fill="currentColor" fillOpacity="0.12" stroke="currentColor" strokeWidth="1.8" />
        <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M115 47 h-2 v11 h14 v-11 h-2" />
          <path d="M120 52 v-12" />
          <path d="M116 44 l4 -4 l4 4" />
        </g>
      </g>
    </svg>
  );
}

/** A share-sheet list with the «إضافة إلى الشاشة الرئيسية» row highlighted. */
function ShareSheetIllustration() {
  return (
    <svg
      viewBox="0 0 240 104"
      role="img"
      aria-label="قائمة المشاركة وخيار «إضافة إلى الشاشة الرئيسية» مميّز"
      className="h-auto w-full max-w-[15rem]"
    >
      <rect x="1" y="1" width="238" height="102" rx="14" className="text-muted" fill="currentColor" />
      <rect x="1" y="1" width="238" height="102" rx="14" className="text-border" fill="none" stroke="currentColor" strokeWidth="1.5" />
      {/* Neighbouring rows — placeholder bars, no real labels */}
      <g className="text-muted-foreground" fill="currentColor" fillOpacity="0.35">
        <rect x="80" y="13" width="126" height="8" rx="4" />
        <rect x="210" y="10" width="14" height="14" rx="3" />
        <rect x="100" y="83" width="106" height="8" rx="4" />
        <rect x="210" y="80" width="14" height="14" rx="3" />
      </g>
      {/* The row that matters */}
      <g className="text-primary">
        <rect x="8" y="34" width="224" height="36" rx="9" fill="currentColor" fillOpacity="0.12" stroke="currentColor" strokeWidth="1.5" />
        <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <rect x="208" y="44" width="16" height="16" rx="3.5" />
          <path d="M216 48 v8 M212 52 h8" />
        </g>
      </g>
      <text
        x="200"
        y="56"
        direction="rtl"
        textAnchor="start"
        fontSize="12"
        fontWeight="600"
        className="text-foreground"
        fill="currentColor"
      >
        إضافة إلى الشاشة الرئيسية
      </text>
    </svg>
  );
}

// ---------------------------------------------------------------------------

// iOS has no install API — Share → «إضافة إلى الشاشة الرئيسية» is the only
// path (Apple permits guiding users to it, not triggering it). iOS 26 moved
// Share behind «⋯» in some Safari layouts and added an «فتح كتطبيق ويب» toggle
// (on by default) to the add dialog — both are called out so users don't stall
// on a missing button or switch the toggle off.
const IOS_STEPS: Step[] = [
  {
    icon: Share,
    text: (
      <>
        اضغط زر <strong>المشاركة</strong> في Safari (مربع يخرج منه سهم). إن لم
        تجده، اضغط <MoreHorizontal className="inline h-4 w-4 align-middle" />{" "}
        أولًا.
      </>
    ),
    illustration: <BrowserBarIllustration />,
  },
  {
    icon: PlusSquare,
    text: (
      <>
        مرّر للأسفل واختر <strong>«إضافة إلى الشاشة الرئيسية»</strong>.
      </>
    ),
    illustration: <ShareSheetIllustration />,
  },
  {
    text: (
      <>
        أبقِ خيار <strong>«فتح كتطبيق ويب»</strong> مفعّلًا إن ظهر، ثم اضغط{" "}
        <strong>«إضافة»</strong>.
      </>
    ),
  },
];

// Fallback for Android when Chrome hasn't handed us a prompt (already
// dismissed, a non-Chrome browser, or the engagement heuristic not yet met).
const ANDROID_STEPS: Step[] = [
  {
    icon: MoreVertical,
    text: (
      <>
        اضغط قائمة المتصفح <strong>⋮</strong> في الأعلى.
      </>
    ),
  },
  {
    icon: PlusSquare,
    text: (
      <>
        اختر <strong>«تثبيت التطبيق»</strong> أو{" "}
        <strong>«إضافة إلى الشاشة الرئيسية»</strong>.
      </>
    ),
  },
];

function StepList({
  steps,
  illustrations,
}: {
  steps: Step[];
  illustrations: boolean;
}) {
  return (
    <ol className="flex flex-col gap-3">
      {steps.map((step, i) => (
        <li key={i} className="flex flex-col gap-2">
          <div className="flex items-start gap-3 text-sm leading-relaxed">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
              {i + 1}
            </span>
            <span className="flex-1">{step.text}</span>
            {step.icon && (
              <step.icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            )}
          </div>
          {illustrations && step.illustration && (
            <div className="ps-9">{step.illustration}</div>
          )}
        </li>
      ))}
    </ol>
  );
}

interface InstallStepsProps {
  platform: InstallPlatform;
  /** Chrome handed us `beforeinstallprompt` — show one button, not steps. */
  canPrompt?: boolean;
  onInstall?: () => void;
  /** Draw the mock-ups under the iOS steps. Default true. */
  illustrations?: boolean;
}

/**
 * The instructions for one platform. With a live Chrome prompt the whole list
 * collapses to «تثبيت ريحان» — the browser's own sheet does the rest. On iOS the
 * prompt never exists, so `canPrompt` is ignored there.
 */
export function InstallSteps({
  platform,
  canPrompt = false,
  onInstall,
  illustrations = true,
}: InstallStepsProps) {
  if (canPrompt && platform !== "ios" && onInstall) {
    return (
      <Button
        onClick={onInstall}
        className="h-11 w-full"
        data-testid="install-app-prompt"
      >
        تثبيت ريحان
      </Button>
    );
  }
  if (platform === "ios") {
    return <StepList steps={IOS_STEPS} illustrations={illustrations} />;
  }
  if (platform === "android") {
    return <StepList steps={ANDROID_STEPS} illustrations={illustrations} />;
  }
  return (
    <p className="text-sm text-muted-foreground">
      افتح rayhanai.com من متصفح جوالك (Safari في الآيفون أو Chrome في
      الأندرويد)، ثم افتح هذه القائمة من هناك لعرض خطوات التثبيت.
    </p>
  );
}
