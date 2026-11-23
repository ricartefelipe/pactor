import { useEffect, type ButtonHTMLAttributes, type ReactNode } from "react";

type Variant = "primary" | "secondary" | "danger" | "link";

export function Button({
  variant = "primary",
  busy = false,
  children,
  type = "button",
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      type={type}
      className={`button button--${variant}`}
      disabled={disabled === true || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {children}
      {busy && <span className="visually-hidden"> (processando)</span>}
    </button>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} · Pactor`;
  }, [title]);
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p className="page-header__description">{description}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  );
}

export function Section({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section" aria-label={title}>
      <div className="section__header">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

type Tone = "neutral" | "info" | "success" | "warning" | "danger";

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function Table({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className="table-wrapper">
      <table>
        <caption className="visually-hidden">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export function DefinitionList({ items }: { items: readonly (readonly [string, ReactNode])[] }) {
  return (
    <dl className="definition-list">
      {items.map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
