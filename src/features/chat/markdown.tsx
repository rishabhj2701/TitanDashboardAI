import type { ReactNode } from 'react';

export type MarkdownChunk =
  | { type: 'text'; content: string }
  | { type: 'table'; headers: string[]; rows: string[][] };

export const parseMarkdownChunks = (text: string): MarkdownChunk[] => {
  const lines = (text || '').split('\n');
  const chunks: MarkdownChunk[] = [];
  const textBuffer: string[] = [];

  const flushText = () => {
    if (!textBuffer.length) return;
    chunks.push({ type: 'text', content: textBuffer.join('\n') });
    textBuffer.length = 0;
  };

  const isTableLine = (line: string): boolean => {
    const trimmed = line.trim();
    return trimmed.startsWith('|') && trimmed.includes('|');
  };

  const isTableSeparator = (line: string): boolean => {
    if (!isTableLine(line)) return false;
    const core = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    const cells = core.split('|').map((cell) => cell.trim());
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  };

  const parseTableRow = (line: string): string[] =>
    line
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((cell) => cell.trim());

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (isTableLine(line) && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      flushText();
      const headers = parseTableRow(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && isTableLine(lines[i]) && !isTableSeparator(lines[i])) {
        rows.push(parseTableRow(lines[i]));
        i += 1;
      }
      chunks.push({ type: 'table', headers, rows });
      continue;
    }
    textBuffer.push(line);
    i += 1;
  }

  flushText();
  return chunks;
};

const renderInlineFormatting = (text: string): ReactNode => {
  // Handle **bold** and highlight numbers like **1,234** as stat badges
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  if (parts.length <= 1) return text;
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          const inner = part.slice(2, -2);
          // Check if this bold text is primarily a number (stat-like)
          const isNum = /^[\d,]+\.?\d*\s*(mph|%|mi|km|pts|points)?$/i.test(inner.trim());
          if (isNum) {
            return <span key={i} className="chat-stat-badge">{inner}</span>;
          }
          return <strong key={i} style={{ color: '#64ffda' }}>{inner}</strong>;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
};

const renderTextBlock = (text: string, idx: number): ReactNode => {
  const lines = text.split('\n');
  const out: ReactNode[] = [];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      out.push(<div key={`spacer-${idx}-${i}`} style={{ height: '0.35em' }} />);
      i += 1;
      continue;
    }

    if (trimmed.startsWith('### ')) {
      out.push(
        <strong key={`h3-${idx}-${i}`} style={{ fontSize: '1.05em', display: 'block', marginTop: '0.5em' }}>
          {trimmed.slice(4)}
        </strong>
      );
      i += 1;
      continue;
    }

    if (trimmed.startsWith('#### ')) {
      out.push(
        <strong key={`h4-${idx}-${i}`} style={{ fontSize: '1em', display: 'block', marginTop: '0.4em' }}>
          {trimmed.slice(5)}
        </strong>
      );
      i += 1;
      continue;
    }

    if (trimmed.startsWith('>')) {
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''));
        i += 1;
      }
      out.push(
        <div
          key={`quote-${idx}-${i}`}
          style={{ borderLeft: '3px solid #555', paddingLeft: '10px', margin: '0.5em 0', opacity: 0.85, fontStyle: 'italic' }}
        >
          {quoteLines.join(' ')}
        </div>
      );
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ''));
        i += 1;
      }
      out.push(
        <ul key={`ul-${idx}-${i}`} style={{ margin: '0.3em 0', paddingLeft: '1.2em' }}>
          {items.map((item, itemIdx) => (
            <li key={`ul-item-${idx}-${i}-${itemIdx}`} style={{ marginBottom: '0.15em' }}>
              {renderInlineFormatting(item)}
            </li>
          ))}
        </ul>
      );
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ''));
        i += 1;
      }
      out.push(
        <ol key={`ol-${idx}-${i}`} style={{ margin: '0.3em 0', paddingLeft: '1.2em' }}>
          {items.map((item, itemIdx) => (
            <li key={`ol-item-${idx}-${i}-${itemIdx}`} style={{ marginBottom: '0.15em' }}>
              {renderInlineFormatting(item)}
            </li>
          ))}
        </ol>
      );
      continue;
    }

    out.push(
      <span key={`p-${idx}-${i}`} style={{ display: 'block', marginBottom: '0.3em' }}>
        {renderInlineFormatting(line)}
      </span>
    );
    i += 1;
  }

  return <>{out}</>;
};

export const renderRichMarkdown = (text: string): ReactNode[] => {
  const chunks = parseMarkdownChunks(text);
  return chunks.map((chunk, idx) => {
    if (chunk.type === 'text') {
      return <span key={`chunk-text-${idx}`}>{renderTextBlock(chunk.content, idx)}</span>;
    }

    return (
      <div key={`chunk-table-${idx}`} className="chat-table-wrapper">
        <div className="chat-table-badge">{chunk.rows.length} rows</div>
        <table className="chat-markdown-table">
          <thead>
            <tr>
              {chunk.headers.map((header, hIdx) => (
                <th key={`table-header-${idx}-${hIdx}`}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {chunk.rows.map((row, rIdx) => (
              <tr key={`table-row-${idx}-${rIdx}`} className={rIdx % 2 === 1 ? 'zebra' : ''}>
                {chunk.headers.map((_, cIdx) => (
                  <td key={`table-cell-${idx}-${rIdx}-${cIdx}`}>{row[cIdx] ?? ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  });
};
