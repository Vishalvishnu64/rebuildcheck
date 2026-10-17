import unittest

import rebuildcheck


class SmokeTests(unittest.TestCase):
    def test_package_has_a_version(self) -> None:
        self.assertEqual(rebuildcheck.__version__, "0.1.0")


if __name__ == "__main__":
    unittest.main()
