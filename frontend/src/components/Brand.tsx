import { useId } from "react";

/** Marca do Pactor: um "P" com o ponto final de uma assinatura, sobre um quadrado em gradiente. */
export function BrandMark({ size = 32 }: { size?: number }) {
  const gradient = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#6366f1" />
          <stop offset="1" stopColor="#4338ca" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${gradient})`} />
      <path
        d="M10 22V10h6.5a4 4 0 0 1 0 8H10"
        fill="none"
        stroke="#ffffff"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="22" cy="22" r="1.9" fill="#c7d2fe" />
    </svg>
  );
}

export function Brand() {
  return (
    <span className="brand">
      <BrandMark />
      <span className="brand__name">Pactor</span>
    </span>
  );
}
