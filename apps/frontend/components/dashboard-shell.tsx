"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PropsWithChildren } from "react";

const NAV_ITEMS = [
  { href: "/overview", label: "Vue d'ensemble" },
  { href: "/positions", label: "Positions" },
  { href: "/trades", label: "Transactions" },
  { href: "/mm", label: "Market Making" },
  { href: "/cd", label: "Crypto Directional" },
  { href: "/performance", label: "Performance" },
  { href: "/settings", label: "Parametres" },
  { href: "/learning", label: "Apprentissage" },
];

export function DashboardShell({ children }: PropsWithChildren) {
  const pathname = usePathname();

  return (
    <main>
      <div className="shell">
        <header className="topbar">
          <h1>Tableau de pilotage Polymarket</h1>
          <p>Interface simple pour piloter le bot, valider ses ameliorations et suivre ses progres.</p>
        </header>
        <nav className="nav">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={pathname === item.href ? "active" : ""}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        {children}
      </div>
    </main>
  );
}
