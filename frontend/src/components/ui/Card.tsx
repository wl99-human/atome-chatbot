import type { PropsWithChildren } from "react";

import { classNames } from "../../lib/utils";

type CardProps = PropsWithChildren<{
  className?: string;
}>;

export function Card({ className, children }: CardProps) {
  return (
    <div
      className={classNames(
        "rounded-[28px] border border-slate-200/80 bg-white p-5 shadow-[0_18px_45px_-28px_rgba(15,23,42,0.28)]",
        className,
      )}
    >
      {children}
    </div>
  );
}
