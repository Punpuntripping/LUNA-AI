"use client";

import { useMemo, useState } from "react";
import { Calculator } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getCalculator,
  type CalculatorInput,
  type CalculatorValues,
} from "@/lib/calculators/registry";

/**
 * The generic, registry-driven calculator form. Given a calculator `slug`, it
 * resolves the definition FROM the registry on the client (so no non-serializable
 * `compute` function is passed across the server/client boundary), renders the
 * input schema, and recomputes the result panel instantly on every change —
 * zero network, zero submit. RTL throughout, Arabic-Indic result numerals.
 *
 * Used both standalone on `/calculators/{slug}` and embedded (`compact`) inside
 * the CalculatorBlock on مادة pages.
 */
export function CalculatorForm({
  slug,
  compact = false,
}: {
  slug: string;
  compact?: boolean;
}) {
  const def = getCalculator(slug);

  // Hooks must run unconditionally — seed initial values from the schema even if
  // the (invalid) slug resolves to undefined, then bail out below.
  const [values, setValues] = useState<CalculatorValues>(() => {
    const initial: CalculatorValues = {};
    for (const input of def?.inputs ?? []) {
      initial[input.name] = input.defaultValue;
    }
    return initial;
  });

  const rows = useMemo(
    () => (def ? def.compute(values) : []),
    [def, values],
  );

  if (!def) return null;

  const setValue = (name: string, value: number | string) =>
    setValues((prev) => ({ ...prev, [name]: value }));

  return (
    <div
      dir="rtl"
      className={cn(
        "grid gap-5",
        compact ? "sm:grid-cols-2" : "md:grid-cols-2",
      )}
    >
      {/* Inputs */}
      <form
        className="space-y-4"
        onSubmit={(event) => event.preventDefault()}
        noValidate
      >
        {def.inputs.map((input) => (
          <Field
            key={input.name}
            input={input}
            value={values[input.name]}
            onChange={(v) => setValue(input.name, v)}
          />
        ))}
      </form>

      {/* Instant result panel */}
      <div className="rounded-xl border border-border bg-card p-4 sm:p-5">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-bold text-foreground">
          <Calculator
            aria-hidden="true"
            className="h-4 w-4 shrink-0 text-primary"
          />
          النتيجة
        </h3>
        <dl className="space-y-2.5">
          {rows.map((row, index) => (
            <div
              key={index}
              className={cn(
                "flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5",
                row.emphasis &&
                  "mt-1 rounded-lg bg-primary/10 px-3 py-2.5",
              )}
            >
              <dt
                className={cn(
                  "text-sm text-text-secondary",
                  row.emphasis && "font-bold text-foreground",
                )}
              >
                {row.label}
              </dt>
              <dd
                className={cn(
                  "text-sm font-semibold text-foreground",
                  row.emphasis && "text-base font-bold text-primary",
                )}
              >
                {row.value}
              </dd>
              {row.hint && (
                <p className="w-full text-xs text-muted-foreground">
                  {row.hint}
                </p>
              )}
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

/** One schema-driven field (number input or select). */
function Field({
  input,
  value,
  onChange,
}: {
  input: CalculatorInput;
  value: number | string;
  onChange: (value: number | string) => void;
}) {
  const fieldId = `calc-${input.name}`;
  const controlClasses =
    "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-ring";

  return (
    <div className="space-y-1.5">
      <label
        htmlFor={fieldId}
        className="block text-sm font-medium text-foreground"
      >
        {input.label}
      </label>

      {input.kind === "select" ? (
        <select
          id={fieldId}
          dir="rtl"
          value={String(value)}
          onChange={(event) => onChange(event.target.value)}
          className={controlClasses}
        >
          {input.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <div className="relative">
          <input
            id={fieldId}
            type="number"
            dir="rtl"
            inputMode="decimal"
            value={Number.isFinite(Number(value)) ? String(value) : ""}
            min={input.min}
            max={input.max}
            step={input.step}
            onChange={(event) => {
              const next = event.target.value;
              onChange(next === "" ? 0 : Number(next));
            }}
            className={cn(controlClasses, input.unit && "pe-14")}
          />
          {input.unit && (
            <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-xs text-muted-foreground">
              {input.unit}
            </span>
          )}
        </div>
      )}

      {input.help && (
        <p className="text-xs text-muted-foreground">{input.help}</p>
      )}
    </div>
  );
}
