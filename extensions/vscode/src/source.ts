import { TextDecoder } from 'node:util';

export const supportedExtensions = ['.py', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'] as const;
export function supportedSourcePath(filename: string): boolean {
  return supportedExtensions.some(extension => filename.endsWith(extension));
}
export class SourceEncodingError extends Error {}
function decodePython(bytes: Buffer): string {
  // Match the existing PEP 263/UTF-8 BOM source contract. Latin-1 is decoded
  // explicitly rather than WHATWG's different windows-1252 interpretation.
  const first = bytes.subarray(0, 4096).toString('latin1').split(/\r\n|\r|\n/).slice(0, 2);
  const match = first.map(line => /^\s*#.*?coding[:=]\s*([-\w.]+)/.exec(line)).find(Boolean);
  let encoding = (match?.[1] ?? 'utf-8').toLowerCase().replaceAll('_', '-');
  if (['latin-1', 'latin1', 'iso-8859-1'].includes(encoding)) { return bytes.toString('latin1'); }
  if (['ascii', 'us-ascii'].includes(encoding)) {
    if (bytes.some(byte => byte > 127)) { throw new SourceEncodingError('Saved Python encoding cannot be verified'); }
    return bytes.toString('ascii');
  }
  if (encoding === 'utf-8-sig') { encoding = 'utf-8'; }
  try { return new TextDecoder(encoding, { fatal: true }).decode(bytes); }
  catch { throw new SourceEncodingError('Saved Python encoding cannot be verified; captured preview remains available'); }
}
export function decodeSavedSource(filename: string, bytes: Buffer): string {
  if (!supportedSourcePath(filename)) { throw new SourceEncodingError('Unsupported saved source suffix'); }
  if (filename.endsWith('.py')) { return decodePython(bytes); }
  try {
    // JS/TS backend decodes UTF-8 directly, retaining BOM in exact first-line
    // windows. ignoreBOM=true retains that character rather than stripping it.
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch { throw new SourceEncodingError('Saved JS/TS source must be valid UTF-8; captured preview remains available'); }
}
export function editorBom(filename: string, bytes: Buffer): boolean {
  return !filename.endsWith('.py') && bytes.length >= 3
    && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
}
export function sourceTextFromEditor(saved: { editorUtf8Bom: boolean }, text: string): string {
  // VS Code removes the encoding BOM from getText(). Restore that one verified
  // byte marker only for comparison with saved JS/TS evidence, never as an edit.
  return saved.editorUtf8Bom ? '\ufeff' + text : text;
}
export function sourceTextFromPreview(captured: string, text: string): string {
  // VS Code's text-buffer builder also separates a leading BOM in virtual
  // documents. The content provider still retains the exact captured string.
  return captured.startsWith('\ufeff') ? '\ufeff' + text : text;
}
