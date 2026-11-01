import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Structured Output × Thuật toán",
  description: "GenAI đọc và điền dữ liệu. Code kiểm tra và tính kết quả.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body>{children}</body></html>;
}
