"""Make ComfyUI see the container's real memory limit instead of the host's.

Why: RunPod containers see the whole host's RAM (e.g. 251 GB) through psutil, but the container is killed by its
cgroup limit, which is far smaller. ComfyUI sizes its pinned memory and its "RAM pressure cache" from
psutil.virtual_memory(), so it never evicts old models, and switching workflows ends in the OOM killer taking
ComfyUI down.

What: ComfyUI 0.34 reads RAM only through psutil.virtual_memory() (total / available). This module wraps that one
function so `total` and `available` follow the cgroup limit. It is loaded by Python at start-up (PYTHONPATH), so it
is in place before comfy.model_management computes its pinned-memory budget at import time. It only touches the
ComfyUI process (main.py), and it never raises: on any problem the host numbers are used as before.

Environment (all optional):
    MEMGUARD_DISABLE=1          do nothing
    MEMGUARD_RESERVE_GB=3       memory ComfyUI must leave free (CUDA context, handler, image buffers ...)
    MEMGUARD_CGROUP_ROOT        where the cgroup files are (tests)
    MEMGUARD_FORCE=1            apply to any python process (tests)
"""
import os
import sys

_GB = 1024 ** 3


def _read(path):
    try:
        with open(path, "r", encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return None


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None   # "max", "", None


def _stat(path):
    out = {}
    text = _read(path)
    for line in (text or "").splitlines():
        key, _, value = line.partition(" ")
        n = _int(value)
        if n is not None:
            out[key] = n
    return out


def cgroup_memory(root):
    """(limit_bytes, used_bytes) of this cgroup, or None when there is no limit. `used` leaves out reclaimable
    page cache (the kernel drops it before it ever kills anything) but counts shared memory."""
    limit = _int(_read(f"{root}/memory.max"))   # cgroup v2
    if limit is not None:
        current = _int(_read(f"{root}/memory.current")) or 0
        stat = _stat(f"{root}/memory.stat")
        return limit, max(0, current - stat.get("file", 0) + stat.get("shmem", 0))
    limit = _int(_read(f"{root}/memory/memory.limit_in_bytes"))   # cgroup v1
    if limit is not None:
        usage = _int(_read(f"{root}/memory/memory.usage_in_bytes")) or 0
        stat = _stat(f"{root}/memory/memory.stat")
        return limit, max(0, usage - stat.get("total_cache", 0) + stat.get("total_shmem", 0))
    return None


def adjusted(vm, cgroup, reserve):
    """vm with total / available / used / free / percent following the cgroup limit. Same object if there is no limit
    below the host's."""
    if cgroup is None:
        return vm
    limit, used = cgroup
    if limit <= 0 or limit >= vm.total:   # unlimited (v1 reports a huge number)
        return vm
    total = max(limit - reserve, limit // 2)
    available = max(0, min(vm.available, total - used))
    used = total - available
    return vm._replace(total=total, available=available, used=used, free=available,
                       percent=round(100.0 * used / total, 1))


def install():
    if os.environ.get("MEMGUARD_DISABLE") == "1":
        return
    if os.environ.get("MEMGUARD_FORCE") != "1":
        script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        if os.path.basename(script) != "main.py" or "comfyui" not in script.lower():
            return
    import psutil

    root = os.environ.get("MEMGUARD_CGROUP_ROOT", "/sys/fs/cgroup")
    reserve = int(float(os.environ.get("MEMGUARD_RESERVE_GB", "3")) * _GB)
    real = psutil.virtual_memory

    def virtual_memory():
        vm = real()
        try:
            return adjusted(vm, cgroup_memory(root), reserve)
        except Exception:   # never break ComfyUI over a bookkeeping problem
            return vm

    psutil.virtual_memory = virtual_memory
    seen = virtual_memory()
    host = real()
    sys.stderr.write(f"[memguard] container memory: ComfyUI sees total {seen.total / _GB:.1f} GB / available "
                     f"{seen.available / _GB:.1f} GB (host reports {host.total / _GB:.1f} GB, reserve {reserve / _GB:.1f} GB)\n")
    sys.stderr.flush()


try:
    install()
except Exception as e:   # noqa: BLE001
    try:
        sys.stderr.write(f"[memguard] not installed: {type(e).__name__}: {e}\n")
    except Exception:   # noqa: BLE001
        pass
