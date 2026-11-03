import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GenAI2 - Ho Xuan An - Bai tap buoi 4",
  description: "GenAI đọc và điền dữ liệu. Code kiểm tra và tính kết quả.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="vi"><body>{children}</body></html>;
}
