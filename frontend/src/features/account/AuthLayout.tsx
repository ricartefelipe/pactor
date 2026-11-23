import { CircleCheck } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { Brand } from "../../components/Brand";

const HIGHLIGHTS = [
  "Aprovação presa à revisão exata, com impressão digital do conteúdo",
  "Assinatura eletrônica com webhooks verificados e sem duplicidade",
  "Prazos em dias úteis, renovações e obrigações com alertas",
  "Dados de cada organização isolados no banco por segurança em nível de linha",
] as const;

export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · Pactor`;
  }, [title]);
  return (
    <div className="auth">
      <aside className="auth__panel" aria-label="Sobre o Pactor">
        <Brand />
        <p className="auth__tagline">O ciclo de vida dos seus contratos, do pedido à renovação.</p>
        <ul className="auth__highlights">
          {HIGHLIGHTS.map((text) => (
            <li key={text}>
              <CircleCheck aria-hidden="true" size={18} />
              <span>{text}</span>
            </li>
          ))}
        </ul>
      </aside>
      <main className="auth__main" id="conteudo">
        <div className="auth__card">
          <h1>{title}</h1>
          {children}
        </div>
      </main>
    </div>
  );
}
