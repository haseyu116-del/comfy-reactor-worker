"""Tests of memguard/sitecustomize.py (run: python -m unittest discover -s tests)."""
import collections
import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memguard"))
os.environ["MEMGUARD_DISABLE"] = "1"   # importing the module must not patch the test runner's psutil
mg = importlib.import_module("sitecustomize")
del os.environ["MEMGUARD_DISABLE"]

GB = 1024 ** 3
VM = collections.namedtuple("svmem", "total available percent used free active inactive buffers cached shared slab")


def host(total=251 * GB, available=235 * GB):
    return VM(total, available, 6.0, total - available, available, 0, 0, 0, 0, 0, 0)


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="ascii") as f:
        f.write(text)


class CgroupTest(unittest.TestCase):
    def test_v2_limit_and_used_without_page_cache(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "memory.max", f"{48 * GB}\n")
            write(root, "memory.current", f"{30 * GB}\n")
            write(root, "memory.stat", f"anon {20 * GB}\nfile {9 * GB}\nshmem {1 * GB}\nkernel 1\n")
            limit, used = mg.cgroup_memory(root)
            self.assertEqual(limit, 48 * GB)
            self.assertEqual(used, (30 - 9 + 1) * GB)   # page cache is reclaimable, shared memory is not

    def test_v2_unlimited(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "memory.max", "max\n")
            self.assertIsNone(mg.cgroup_memory(root))

    def test_v1(self):
        with tempfile.TemporaryDirectory() as root:
            write(root, "memory/memory.limit_in_bytes", f"{32 * GB}\n")
            write(root, "memory/memory.usage_in_bytes", f"{10 * GB}\n")
            write(root, "memory/memory.stat", f"total_cache {4 * GB}\ntotal_shmem 0\n")
            self.assertEqual(mg.cgroup_memory(root), (32 * GB, 6 * GB))

    def test_no_cgroup_files(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(mg.cgroup_memory(root))


class AdjustedTest(unittest.TestCase):
    def test_follows_the_container_limit(self):
        vm = mg.adjusted(host(), (48 * GB, 30 * GB), 3 * GB)
        self.assertEqual(vm.total, 45 * GB)                # limit - reserve
        self.assertEqual(vm.available, 15 * GB)            # total - used
        self.assertEqual(vm.used, 30 * GB)
        self.assertEqual(vm.percent, round(100 * 30 / 45, 1))
        self.assertEqual(vm.buffers, 0)                    # other fields are kept

    def test_never_more_than_the_host_says(self):
        vm = mg.adjusted(host(available=5 * GB), (48 * GB, 1 * GB), 3 * GB)
        self.assertEqual(vm.available, 5 * GB)

    def test_never_negative(self):
        vm = mg.adjusted(host(), (48 * GB, 60 * GB), 3 * GB)
        self.assertEqual(vm.available, 0)

    def test_no_limit_or_limit_above_the_host_is_untouched(self):
        h = host()
        self.assertIs(mg.adjusted(h, None, 3 * GB), h)
        self.assertIs(mg.adjusted(h, (2 ** 63 - 4096, 10 * GB), 3 * GB), h)   # v1's "unlimited"

    def test_a_reserve_larger_than_the_limit_is_capped(self):
        vm = mg.adjusted(host(), (4 * GB, 0), 10 * GB)
        self.assertEqual(vm.total, 2 * GB)


class InstallTest(unittest.TestCase):
    def test_installs_into_psutil_and_reads_the_cgroup_each_call(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil is not installed")
        real = psutil.virtual_memory
        env = dict(os.environ)
        limit = real().total // 2   # below this machine's RAM, whatever it is
        try:
            with tempfile.TemporaryDirectory() as root:
                os.environ.update({"MEMGUARD_FORCE": "1", "MEMGUARD_CGROUP_ROOT": root, "MEMGUARD_RESERVE_GB": "0"})
                write(root, "memory.max", f"{limit}\n")
                write(root, "memory.current", "0\n")
                write(root, "memory.stat", "file 0\nshmem 0\n")
                mg.install()
                self.assertEqual(psutil.virtual_memory().total, limit)
                write(root, "memory.current", f"{limit - GB // 4}\n")   # nearly full: 256 MB left
                self.assertLessEqual(psutil.virtual_memory().available, GB // 4)
        finally:
            psutil.virtual_memory = real
            os.environ.clear()
            os.environ.update(env)


if __name__ == "__main__":
    unittest.main()
