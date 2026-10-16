import type { Metadata } from "next";
import { Geist_Mono, Inter, Instrument_Serif } from "next/font/google";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const serif = Instrument_Serif({ variable: "--font-serif", subsets: ["latin"], weight: "400", style: ["normal", "italic"] });

export const metadata: Metadata = {
  title: "PatientZero: npm supply-chain incident response on FalkorDB",
  description:
    "When an npm maintainer gets phished, PatientZero traverses your fleet's dependency graph in FalkorDB to show who is exposed, the exact chain, who to wake, and the fix.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${mono.variable} ${serif.variable} antialiased`}>{children}</body>
    </html>
  );
}
