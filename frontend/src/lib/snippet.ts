/** Trechos da pesquisa marcam os termos encontrados com «…»; sem HTML vindo do servidor. */
export interface SnippetPart {
  text: string;
  match: boolean;
}

export function snippetParts(snippet: string): SnippetPart[] {
  const parts: SnippetPart[] = [];
  const pattern = /«([^»]*)»/g;
  let last = 0;
  for (const found of snippet.matchAll(pattern)) {
    const index = found.index;
    if (index > last) parts.push({ text: snippet.slice(last, index), match: false });
    parts.push({ text: found[1] ?? "", match: true });
    last = index + found[0].length;
  }
  if (last < snippet.length) parts.push({ text: snippet.slice(last), match: false });
  return parts;
}
