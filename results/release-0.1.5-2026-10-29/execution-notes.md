# v0.1.5 qualification execution notes

The first source candidate was committed as `e349e0c6c9627a775c43a7b15956ff0d88eb810e`
after 3,514 core tests and 179 editor/helper checks passed. Before creating any
asset, source inspection of the asset qualifier found that raw README byte
comparison ignored VSCE's intentional relative Markdown link rewriting. The
v0.1.4 artifact confirms that behavior. The qualifier now binds the original
committed README hash and checks the deterministic pinned VSCE 4.0.0 rewrite;
its transform reproduces the historical artifact exactly. This pre-build
qualification-harness correction changes no packaged product source.

No live setup, index, model call or new release asset preceded that correction.
All older release evidence and original candidate code remain in Git history.
