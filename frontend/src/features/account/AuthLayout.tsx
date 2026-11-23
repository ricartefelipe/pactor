import { useEffect, type ReactNode } from "react";

export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · Pactor`;
  }, [title]);
  return (
    <main className="auth" id="conteudo">
      <div className="auth__card">
        <p className="auth__brand">Pactor</p>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}
