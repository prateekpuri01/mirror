import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Companies",
};

export default function CompaniesLayout({ children }: { children: React.ReactNode }) {
  return children;
}
