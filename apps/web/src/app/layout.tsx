import type { Metadata } from "next";
import { IBM_Plex_Sans, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";
import { QueryProvider } from "@/lib/query";
import { Shell } from "@/components/shell/Shell";
import "./globals.css";

const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-jb", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Aegis console", template: "%s · Aegis" },
  description: "Control console for the Aegis autonomous incident-response runtime.",
  icons: { icon: "/aegis-mark.svg" },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${plex.variable} ${mono.variable} dark`}>
      <body className="font-sans">
        <QueryProvider>
          <Shell>{children}</Shell>
        </QueryProvider>
      </body>
    </html>
  );
}
