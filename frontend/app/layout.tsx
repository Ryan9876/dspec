import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DSpec AI",
  description: "Spec-first local software delivery workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
