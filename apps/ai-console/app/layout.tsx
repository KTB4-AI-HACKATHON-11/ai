import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI Backend Console",
  description: "Flowcheck AI 백엔드 테스트와 런타임 설정",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
