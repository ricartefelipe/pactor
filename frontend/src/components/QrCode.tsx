import qrcode from "qrcode-generator";
import { useMemo } from "react";

const QUIET_ZONE = 4;

/** Caminho SVG com um quadrado por módulo escuro (sem HTML gerado pela biblioteca). */
export function qrPath(text: string): { size: number; path: string } {
  const code = qrcode(0, "M");
  code.addData(text);
  code.make();
  const count = code.getModuleCount();
  const squares: string[] = [];
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (code.isDark(row, col)) squares.push(`M${col + QUIET_ZONE} ${row + QUIET_ZONE}h1v1h-1z`);
    }
  }
  return { size: count + QUIET_ZONE * 2, path: squares.join("") };
}

/** QR code acessível: imagem com descrição; o conteúdo também deve estar disponível em texto. */
export function QrCode({ text, label }: { text: string; label: string }) {
  const { size, path } = useMemo(() => qrPath(text), [text]);
  return (
    <svg
      className="qr-code"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#ffffff" />
      <path d={path} fill="#000000" />
    </svg>
  );
}
