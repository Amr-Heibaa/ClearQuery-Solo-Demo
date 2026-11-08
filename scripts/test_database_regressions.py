"""Run tests with existing local test credentials, never print credentials."""
from pathlib import Path
import sys
import argparse
import _env
_env.load(Path(__file__).resolve().parents[1]/'.secrets/demo.env')
import pytest
parser=argparse.ArgumentParser()
parser.add_argument('--evidence-directory',type=Path,default=Path('runtime/database_results'))
args,remaining=parser.parse_known_args()
args.evidence_directory.mkdir(parents=True,exist_ok=True)
class EvidenceLocation:
    def pytest_collection_modifyitems(self,items):
        for item in items:
            if item.module.__name__.endswith('test_gold_sql'):
                item.module.OUT=args.evidence_directory/'gold_sql_results.json'
raise SystemExit(pytest.main(['-q','-p','no:cacheprovider',
    '--junitxml='+str(args.evidence_directory/'pytest-results.xml'),*remaining],plugins=[EvidenceLocation()]))
