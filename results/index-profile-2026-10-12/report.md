# Durable index profile on 10,000 synthetic windows

The actual SQLite FTS5 and sqlite-vec implementation indexed 10,000 generated Python windows with seeded 1,024-dimensional Gaussian CPU vectors. No embedding model ran. This measures storage, persistence, and retrieval operations; it provides no evidence about semantic retrieval quality.

| Operation | Median | P95 | Timed samples |
|---|---:|---:|---:|
| Exact sqlite-vec cosine, top ten | 18.62ms | 19.63ms | 30 |
| FTS5 selective literal, one matching row | 0.473ms | 0.596ms | 30 |
| FTS5 common terms, all 10,000 rows match | 6.393ms | 6.650ms | 30 |

Building the index took 0.594s after fixture/vector preparation. Reopening SQLite took 0.00064s; refreshing the unchanged durable index took 0.390s and reused all 10,000 cached vectors with zero synthetic embedding calls. The SQLite database occupied 142,258,176 bytes (135.67MiB); WAL and shared-memory files were absent after all operation connections closed.

Every dense top-ten result matched an independent NumPy cosine calculation in exact order. The maximum cosine score difference was 1.19e-6. Selective FTS5 searches returned the exact unique literal's source window; broad searches resolved equal-score ties in stable ID order. Cached vectors exactly preserved the original float32 values stored as float64 in the durable cache. Active vectors use normalized float32 for sqlite-vec.

Queries use a new SQLite connection each time, including extension loading. Fixture/vector preparation and NumPy verification are excluded from retrieval timings. Dense and selective FTS5 each had three unmeasured warmups; broad FTS5 had zero extra warmups, so its first request is included. P95 uses NumPy's linear percentile interpolation. These are direct index timings and exclude source scanning, query embedding, HTTP/MCP transport, and result packaging. Dense retrieval performs an exact scan; this is not an approximate index or a 100,000-window scalability result.

The seed is `20261012`. Source fixtures contain 200 files with 50 two-line functions each and are extracted with 160-line windows and 40-line overlap. All saved excerpt bytes and locations were checked before indexing. Raw configuration, runtime versions, samples, expected/actual IDs, and verification errors are in [results.json](results.json).

Reproduce from the project directory in the installed project environment:

```sh
.venv/bin/python scripts/profile_index.py \
  --output results/local/index-profile-reproduction \
  --windows 10000 --dimensions 1024 --searches 30 --seed 20261012
```

Use a fresh output directory. The script retains generated source fixtures and the SQLite database under that ignored directory; it refuses to overwrite an existing result/index. The published JSON redacts the local database path. This run used the M4 Mac's Python 3.12 environment; exact package and platform versions are recorded in the JSON.
