import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import { createHash } from 'node:crypto';
import { hash, physicalLines, relativePath, Result, ValidationError } from './schema';
import { decodeSavedSource, editorBom, SourceEncodingError } from './source';

const MAX_FILE_BYTES = 8 * 1024 * 1024;
export class StaleEvidenceError extends Error {}
export interface SavedEvidence { canonicalPath: string; fileHash: string; editorUtf8Bom: boolean; }
export interface RepositoryBinding { root: string; sourceRoot: string; }
export function contained(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative));
}
export async function bindRepository(workspace: string, reportedRepo: string, sourceRoot: string): Promise<RepositoryBinding> {
  if (!path.isAbsolute(workspace) || !path.isAbsolute(reportedRepo)) { throw new ValidationError('Repository paths must be absolute'); }
  const [root, repo] = await Promise.all([fs.realpath(workspace), fs.realpath(reportedRepo)]);
  if (root !== repo || !(await fs.stat(root)).isDirectory()) { throw new ValidationError('Service repository does not match the selected workspace folder'); }
  relativePath(sourceRoot, true);
  const scope = await fs.realpath(path.resolve(root, sourceRoot));
  if (!contained(root, scope) || !(await fs.stat(scope)).isDirectory()) { throw new ValidationError('Service source root leaves the selected workspace'); }
  return { root, sourceRoot: scope };
}
export async function verifySavedResult(binding: RepositoryBinding, result: Result, previous?: SavedEvidence): Promise<SavedEvidence> {
  relativePath(result.path);
  const filename = path.resolve(binding.root, result.path);
  const canonical = await fs.realpath(filename);
  if (!contained(binding.root, canonical) || !contained(binding.sourceRoot, canonical)) {
    throw new ValidationError('Result resolves outside the connected source root');
  }
  const stat = await fs.stat(canonical);
  if (!stat.isFile() || stat.size > MAX_FILE_BYTES) { throw new StaleEvidenceError('Saved file cannot be safely verified; captured preview remains available'); }
  const bytes = await fs.readFile(canonical);
  if (bytes.length > MAX_FILE_BYTES || await fs.realpath(filename) !== canonical) { throw new StaleEvidenceError('Source path changed after search'); }
  const after = await fs.stat(canonical);
  if (stat.dev !== after.dev || stat.ino !== after.ino || stat.mtimeMs !== after.mtimeMs || stat.size !== after.size) {
    throw new StaleEvidenceError('Saved source changed during verification');
  }
  const fileHash = createHash('sha256').update(bytes).digest('hex');
  if (previous && (previous.canonicalPath !== canonical || previous.fileHash !== fileHash)) {
    throw new StaleEvidenceError('This file changed after the result was captured; run a new search');
  }
  let decoded: string;
  try { decoded = decodeSavedSource(result.path, bytes); }
  catch (error) { if (error instanceof SourceEncodingError) { throw new StaleEvidenceError(error.message); } throw error; }
  const lines = physicalLines(decoded);
  if (result.window_end_line > lines.length || result.start_line < 1 || result.end_line > result.window_end_line
      || hash(lines.slice(result.start_line - 1, result.end_line).join('')) !== result.source_hash
      || lines.slice(result.start_line - 1, result.end_line).join('') !== result.source
      || hash(lines.slice(result.start_line - 1, result.window_end_line).join('')) !== result.content_hash) {
    throw new StaleEvidenceError('The current saved implementation no longer matches the captured result');
  }
  return { canonicalPath: canonical, fileHash, editorUtf8Bom: editorBom(result.path, bytes) };
}
