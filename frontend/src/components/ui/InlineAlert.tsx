import type { PropsWithChildren } from "react";

import { classNames } from "../../lib/utils";

type InlineAlertProps = PropsWithChildren<{
  tone?: "info" | "success" | "warning" | "danger";
  title?: string;
  className?: string;
}>;

const toneClasses: Record<NonNullable<InlineAlertProps["tone"]>, string> = {
  info: "border-slate-200 bg-slate-50 text-slate-700",
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warning: "border-amber-200 bg-amber-50 text-amber-800",
  danger: "border-rose-200 bg-rose-50 text-rose-800",
};

export function InlineAlert({ tone = "info", title, className, children }: InlineAlertProps) {
  return (
    <div className={classNames("rounded-3xl border px-4 py-3 text-sm", toneClasses[tone], className)}>
      {title ? <p className="font-semibold">{title}</p> : null}
      <div className={title ? "mt-1" : ""}>{children}</div>
    </div>
  );
}
