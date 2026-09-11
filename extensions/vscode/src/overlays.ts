import * as path from 'node:path';
import * as fs from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { TextDecoder } from 'node:util';
import { RepositoryBinding, readSavedEvidence, SavedEvidence, StaleEvidenceError, contained, verifyRepositoryBinding } from './evidence';
import { hash, integer, relativePath, Result, physicalLines, string, Status, ValidationError } from './schema';
import { sourceTextFromEditor, supportedSourcePath } from './source';

export const MAX_OVERLAY_FILES = 16, MAX_OVERLAY_FILE_BYTES = 256 * 1024;
export const MAX_OVERLAY_TOTAL_BYTES = 1024 * 1024, MAX_OVERLAY_BODY_BYTES = 2 * 1024 * 1024;
export interface BufferOverride { path: string; text: string; version: number; content_hash: string; }
export interface BufferBinding { path: string; version: number; content_hash: string; }
export interface EditorBuffer { key: string; path: string; version: number; text: string; dirty: boolean; closed: boolean; }
export type BufferReader = () => readonly EditorBuffer[];
export interface OverlayEvidence extends SavedEvidence { bufferKey: string; editorPath: string; version: number; content_hash: string; }
export interface OverlayBundle { overrides: BufferOverride[]; evidence: Map<string, OverlayEvidence>; excluded: string; }
export type TrackedReader = (root: string, signal?: AbortSignal) => Promise<Set<string>>;
const JS_EXCLUDED = new Set(['node_modules', 'bower_components', 'dist', 'build', 'coverage', '.next', '.nuxt', '.svelte-kit', 'out']);
function check(signal?: AbortSignal): void { if (signal?.aborted) { throw new StaleEvidenceError('Buffer search cancelled'); } }
export function validateOverrides(values: readonly BufferOverride[]): void {
  if (!Array.isArray(values) || values.length > MAX_OVERLAY_FILES) { throw new ValidationError('At most 16 dirty buffers can be searched'); }
  let total = 0; const seen = new Set<string>();
  for (const item of values) {
    relativePath(item.path); string(item.text); integer(item.version, 0);
    const size = Buffer.byteLength(item.text, 'utf8'); total += size;
    if (Buffer.byteLength(item.path, 'utf8') > 512 || size > MAX_OVERLAY_FILE_BYTES || total > MAX_OVERLAY_TOTAL_BYTES) {
      throw new ValidationError('Dirty buffer limit: paths 512 UTF-8 bytes, text 256 KiB each and 1 MiB total');
    }
    if (seen.has(item.path) || item.content_hash !== hash(item.text)) { throw new ValidationError('Invalid dirty buffer hash or duplicate path'); }
    seen.add(item.path);
  }
}
export const trackedFiles: TrackedReader = async (root, signal) => new Promise((resolve, reject) => {
  check(signal);
  execFile('git', ['-C', root, 'ls-files', '-z'], { encoding: 'buffer', maxBuffer: 16 * 1024 * 1024, timeout: 10000, ...(signal ? { signal } : {}) }, (error, stdout) => {
    if (error) { reject(new ValidationError('Cannot verify saved tracked buffer membership in this Git worktree')); return; }
    try { resolve(new Set(new TextDecoder('utf-8', { fatal: true }).decode(stdout).split('\0').filter(Boolean))); }
    catch { reject(new ValidationError('Git tracked paths must use UTF-8')); }
  });
});
/** Python fnmatch-style globs: stars also match slashes; matching is case sensitive. */
export function globMatches(filename: string, pattern: string): boolean {
  const chars = [...pattern], tokens: ('*' | ((char: string) => boolean))[] = [];
  for (let i = 0; i < chars.length; i++) {
    const char = chars[i]!;
    if (char === '*') { if (tokens.at(-1) !== '*') { tokens.push('*'); } }
    else if (char === '?') { tokens.push(() => true); }
    else if (char === '[') {
      let end = i + 1;
      if (chars[end] === '!') { end++; }
      if (chars[end] === ']') { end++; }
      while (end < chars.length && chars[end] !== ']') { end++; }
      if (end === chars.length) { tokens.push(value => value === '['); }
      else {
        let contents = chars.slice(i + 1, end).join(''), negative = '';
        if (contents.startsWith('!')) { negative = '^'; contents = contents.slice(1); }
        try {
          const matcher = new RegExp('^[' + negative + contents.replace(/\\/g, '\\\\').replace(/\]/g, '\\]').replace(/^\^/, '\\^') + ']$', 'u');
          tokens.push(value => matcher.test(value));
        } catch { throw new ValidationError('Source exclusions cannot be safely interpreted; use saved search'); }
        i = end;
      }
    } else { tokens.push(value => value === char); }
  }
  // Dynamic programming bounds wildcard work; repeated stars cannot cause
  // exponential RegExp backtracking on a long excluded path.
  const text = [...filename]; let matched = Array<boolean>(text.length + 1).fill(false); matched[0] = true;
  for (const token of tokens) {
    const next = Array<boolean>(text.length + 1).fill(false);
    if (token === '*') {
      next[0] = matched[0]!;
      for (let j = 1; j <= text.length; j++) { next[j] = matched[j]! || next[j - 1]!; }
    } else {
      for (let j = 1; j <= text.length; j++) { next[j] = matched[j - 1]! && token(text[j - 1]!); }
    }
    matched = next;
  }
  return matched[text.length]!;
}
export function eligiblePath(filename: string, exclusions: readonly string[]): boolean {
  if (!supportedSourcePath(filename) || exclusions.some(pattern => globMatches(filename, pattern))) { return false; }
  return filename.endsWith('.py') || (!/\.d\.(?:ts|mts|cts)$/.test(filename) && !filename.split('/').slice(0, -1).some(part => JS_EXCLUDED.has(part)));
}
function signature(buffers: readonly EditorBuffer[]): string {
  // Identity detects close/reopen even if VS Code restarts a version counter.
  return JSON.stringify(buffers.filter(buffer => buffer.dirty && !buffer.closed).map(buffer =>
    [buffer.key, buffer.path, buffer.version, hash(buffer.text)]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))));
}
export async function captureBundle(binding: RepositoryBinding, status: Status, read: BufferReader, signal?: AbortSignal,
  tracked: TrackedReader = trackedFiles): Promise<OverlayBundle> {
  if (!status.source_exclusions) { throw new ValidationError('Service does not advertise source exclusions; reconnect to an overlay-capable service'); }
  const buffers = read().map(buffer => ({ ...buffer })), initial = signature(buffers);
  check(signal); await verifyRepositoryBinding(binding);
  check(signal); const membership = await tracked(binding.root, signal); check(signal);
  const overrides: BufferOverride[] = [], evidence = new Map<string, OverlayEvidence>(), excluded = new Map<string, number>(), physical = new Set<string>();
  const skip = (reason: string) => excluded.set(reason, (excluded.get(reason) ?? 0) + 1);
  for (const buffer of buffers) {
    check(signal); if (!buffer.dirty || buffer.closed) { continue; }
    if (!path.isAbsolute(buffer.path)) { skip('untitled or non-file'); continue; }
    const workspaceRoot = binding.workspaceRoot ?? binding.root;
    const base = contained(workspaceRoot, buffer.path) ? workspaceRoot : contained(binding.root, buffer.path) ? binding.root : null;
    if (!base) { skip('outside workspace'); continue; }
    const relative = path.relative(base, buffer.path).split(path.sep).join('/');
    if (!eligiblePath(relative, status.source_exclusions)) { skip('unsupported or excluded'); continue; }
    if (!membership.has(relative)) { skip('untracked'); continue; }
    if (!contained(binding.sourceRoot, path.resolve(binding.root, relative))) { skip('outside source root'); continue; }
    let saved: Awaited<ReturnType<typeof readSavedEvidence>>;
    try { saved = await readSavedEvidence(binding, relative); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') { skip('deleted'); continue; }
      throw error;
    }
    if (await fs.realpath(buffer.path) !== saved.evidence.canonicalPath) { throw new StaleEvidenceError('Dirty editor path changed during capture'); }
    if (physical.has(saved.evidence.canonicalPath)) { throw new ValidationError('Multiple dirty paths resolve to the same file; use a single editor path'); }
    physical.add(saved.evidence.canonicalPath);
    const text = sourceTextFromEditor(saved.evidence, buffer.text), content_hash = hash(text);
    overrides.push({ path: relative, text, version: buffer.version, content_hash });
    evidence.set(relative, { ...saved.evidence, bufferKey: buffer.key, editorPath: buffer.path, version: buffer.version, content_hash });
    validateOverrides(overrides);
  }
  check(signal); await verifyRepositoryBinding(binding); check(signal);
  if (signature(read()) !== initial) { throw new StaleEvidenceError('Dirty buffers changed during capture; run a new search'); }
  overrides.sort((a, b) => a.path.localeCompare(b.path));
  return { overrides, evidence, excluded: [...excluded].map(([reason, count]) => `${count} ${reason}`).join(', ') };
}
export async function verifyBundle(binding: RepositoryBinding, status: Status, bundle: OverlayBundle, read: BufferReader,
  signal?: AbortSignal, tracked: TrackedReader = trackedFiles): Promise<void> {
  const current = await captureBundle(binding, status, read, signal, tracked);
  const bindings = (value: OverlayBundle) => value.overrides.map(({ path, version, content_hash }) => ({ path, version, content_hash }));
  if (JSON.stringify(bindings(current)) !== JSON.stringify(bindings(bundle))) { throw new StaleEvidenceError('Dirty buffers were edited, saved, closed or changed scope; run a new search'); }
  for (const [filename, before] of bundle.evidence) {
    const after = current.evidence.get(filename);
    if (!after || after.bufferKey !== before.bufferKey || after.canonicalPath !== before.canonicalPath || after.fileHash !== before.fileHash) {
      throw new StaleEvidenceError('Saved membership, disk bytes or editor identity changed; run a new search');
    }
  }
}
export function verifyOverlayResult(result: Result, override: BufferOverride): void {
  const lines = physicalLines(override.text), excerpt = lines.slice(result.start_line - 1, result.end_line).join('');
  if (result.window_end_line > lines.length || excerpt !== result.source || hash(excerpt) !== result.source_hash
      || hash(lines.slice(result.start_line - 1, result.window_end_line).join('')) !== result.content_hash) {
    throw new ValidationError('Overlay excerpt differs from the captured literal buffer');
  }
}
export function verifyEditorBuffer(evidence: OverlayEvidence, buffer: EditorBuffer): string {
  const text = sourceTextFromEditor(evidence, buffer.text);
  if (buffer.closed || !buffer.dirty || buffer.key !== evidence.bufferKey || buffer.path !== evidence.editorPath
      || buffer.version !== evidence.version || hash(text) !== evidence.content_hash) {
    throw new StaleEvidenceError('Editor buffer version or literal text changed; run a new search');
  }
  return text;
}
