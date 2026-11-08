"""Build only source/configuration fixtures for upload; never runtime or audio."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES=("src","scripts","tests","gold","db","data","contracts","configs","models")
FILES=("pyproject.toml","requirements-models.txt","requirements-colab-t4.txt","PERSON1_SHA256SUMS","ClearQuery_Solo_Demo_Colab.ipynb","LICENSE","THIRD_PARTY_NOTICES.md","README.md")
EXCLUDED_TESTS={'test_live_ui.py','test_database_ui.py','test_safe_database.py'}
EXCLUDED_SCRIPTS={'smoke_live_ui.py','smoke_database_ui.py','recheck_registered_q2.py'}


def build(output):
    output=Path(output)
    paths=[ROOT/name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and
            p.suffix.lower() in {'.py','.js','.json','.sql','.csv','.md','.txt'} and '__pycache__' not in p.parts
            and not (directory=='tests' and p.name in EXCLUDED_TESTS)
            and not (directory=='scripts' and p.name in EXCLUDED_SCRIPTS)
            and not p.relative_to(ROOT).as_posix().startswith('src/clearquery/live_ui/'))
    paths.append(ROOT/'data/SHA256SUMS')
    manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in manifest:archive.write(ROOT/name,name)
        archive.writestr('SOURCE_SHA256.json',json.dumps(manifest,sort_keys=True,indent=2))
    return manifest


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path)
    result=build(parser.parse_args().output)
    print(f'Archived {len(result)} source/configuration files; no caches, runtime, credentials or audio.')
