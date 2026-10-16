#!/bin/bash -eu

cd "$SRC/bifrost"

pip3 install --require-hashes --target "$OUT/deps" -r .clusterfuzzlite/requirements.lock
export PYTHONPATH="$OUT/deps:$SRC/bifrost/api"

for fuzzer in api/fuzz/atheris_targets/*_fuzzer.py; do
  compile_python_fuzzer "$fuzzer" --paths "$OUT/deps" --paths "$SRC/bifrost/api"
done

python3 - <<'PY'
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

out_dir = Path(os.environ["OUT"])
corpus_root = Path("api/fuzz/corpora")
target_names = {
    "cron-parser": "cron_parser_fuzzer",
    "editor-search": "editor_search_fuzzer",
    "webhook-request": "webhook_request_fuzzer",
}

for corpus_name, fuzzer_name in target_names.items():
    corpus_dir = corpus_root / corpus_name
    zip_path = out_dir / f"{fuzzer_name}_seed_corpus.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as archive:
        for path in sorted(corpus_dir.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
PY
