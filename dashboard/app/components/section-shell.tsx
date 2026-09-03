import type { ReactNode } from "react";
import SystemNav, { type SystemRoute } from "./system-nav";

export default function SectionShell({
  active,
  eyebrow,
  title,
  description,
  children,
}: {
  active: SystemRoute;
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <main className="portal-shell">
      <header className="portal-header">
        <div className="brand">
          <b>WQ</b>
          <div><span>{eyebrow}</span><h1>{title}</h1></div>
        </div>
        <p>{description}</p>
      </header>
      <SystemNav active={active} />
      {children}
    </main>
  );
}
