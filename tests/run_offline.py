"""Run unit guards with real outbound connections disabled, no production secrets."""
import os
import shutil
import subprocess
import tempfile
import pathlib
import socket
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))


def no_network(*args, **kwargs):
    raise AssertionError('Real network access is forbidden in offline guard tests')


if __name__ == '__main__':
    if os.environ.get('SHORTS_ISOLATED_TEST_CHILD') != '1':
        with tempfile.TemporaryDirectory(prefix='shorts-tests-') as directory:
            checkout = pathlib.Path(directory)/'repo'
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns('.git', 'runs', '__pycache__', '.venv'))
            env = {k:v for k,v in os.environ.items() if not any(x in k for x in ('TOKEN', 'API_KEY', 'OAUTH'))}
            env.update(SHORTS_ISOLATED_TEST_CHILD='1', SHORTS_STATE_DIR=str(pathlib.Path(directory)/'state'))
            sys.exit(subprocess.call([sys.executable, str(checkout/'tests/run_offline.py')], cwd=checkout, env=env))
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
