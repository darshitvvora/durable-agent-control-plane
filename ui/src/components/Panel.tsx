import type { ReactNode } from "react";

/** A raised chassis panel with a sunken title bar — the base unit of the shell. */
export function Panel({
  title,
  right,
  children,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`raised flex min-h-0 flex-col bg-chrome p-2 ${className}`}>
      <header className="mb-2 flex shrink-0 items-center justify-between gap-3 px-1 pt-1">
        <h2 className="titlebar">{title}</h2>
        {right}
      </header>
      {children}
    </section>
  );
}

/** A recessed well — where data lives. Always dark, always inset. */
export function Well({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`sunken min-h-0 bg-well p-3 text-ink ${className}`}>{children}</div>
  );
}
