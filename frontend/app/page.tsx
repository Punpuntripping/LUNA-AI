import { redirect } from "next/navigation";

// Next.js App Router requires default export for page files
// eslint-disable-next-line import/no-default-export
export default function Home() {
  redirect("/chat");
}
