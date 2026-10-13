import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fulfillment Control | Retail Operations",
  description: "Evidence-backed fulfillment failure investigations.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}