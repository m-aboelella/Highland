import type { Metadata } from "next";

import "./styles.css";

export const metadata: Metadata = {
  title: "Highland workspace",
  description: "A local enterprise agent learning workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
