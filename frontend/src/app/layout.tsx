import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { NavBar } from "@/components/nav-bar";

export const metadata: Metadata = {
  title: "Job Board",
  description: "AI-powered personal job board",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <Providers>
          <NavBar />
          {children}
        </Providers>
      </body>
    </html>
  );
}
