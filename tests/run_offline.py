"""Run unit guards with real outbound connections disabled, no production secrets."""
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
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
