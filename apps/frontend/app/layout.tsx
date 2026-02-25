import type { Metadata } from "next";
import { Space_Grotesk, IBM_Plex_Mono } from "next/font/google";

import "./globals.css";
import { Providers } from "./providers";

const heading = Space_Grotesk({ subsets: ["latin"], variable: "--font-heading" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "Polymarket Bot - Tableau de pilotage",
  description: "Dashboard de controle et d'apprentissage du bot Polymarket",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="fr" className={`${heading.variable} ${mono.variable}`}>
      <body style={{ fontFamily: "var(--font-heading), sans-serif" }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
