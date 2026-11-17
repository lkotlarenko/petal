import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parent.parent/'scripts/release-version.py'
spec=importlib.util.spec_from_file_location('release_version',path)
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


class ReleaseVersionTests(unittest.TestCase):
    def test_initial_release_patch_floor_and_reruns(self):
        self.assertEqual(release.choose((0,3,0),[]),(0,3,0))
        self.assertEqual(release.choose((0,3,0),['v0.3.9','v0.3.2','junk','v9.0.0-beta']),(0,3,10))
        self.assertEqual(release.choose((0,4,0),['v0.3.9']),(0,4,0))
        self.assertEqual(release.choose((0,3,0),['v0.3.9'],['v0.3.9']),(0,3,9))
