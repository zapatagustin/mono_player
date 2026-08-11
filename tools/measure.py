#!/usr/bin/env python3
"""Sample CPU and PSS of a process tree. Used for the GUIDELINE performance
comparison (mono_player vs Chrome on the same video).

usage: measure.py <root_pid> <seconds> [label]

CPU is the summed jiffies delta of the whole tree per interval, reported in
percent of one core. PSS (not RSS) is summed so multi-process browsers are
not over-counted for shared pages.
"""

import os
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")


def descendants(root: int) -> set[int]:
    children = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(pid))
        except (OSError, IndexError):
            continue
    tree, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid in tree:
            continue
        tree.add(pid)
        todo.extend(children.get(pid, []))
    return tree


def cpu_jiffies(pids) -> int:
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            total += int(fields[11]) + int(fields[12])  # utime + stime
        except (OSError, IndexError, ValueError):
            continue
    return total


def pss_kb(pids) -> int:
    total = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Pss:"):
                        total += int(line.split()[1])
                        break
        except OSError:
            continue
    return total


def main():
    root, seconds = int(sys.argv[1]), int(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else str(root)
    cpu_samples, pss_samples = [], []
    prev = None
    prev_t = None
    for _ in range(seconds):
        pids = descendants(root)
        if not pids or not os.path.exists(f"/proc/{root}"):
            break
        now, jiffies = time.monotonic(), cpu_jiffies(pids)
        if prev is not None:
            pct = (jiffies - prev) / HZ / (now - prev_t) * 100
            cpu_samples.append(pct)
        prev, prev_t = jiffies, now
        pss_samples.append(pss_kb(pids))
        time.sleep(1)
    if not cpu_samples:
        print(f"{label}: process gone before sampling completed")
        return 1
    cpu_samples.sort()
    n = len(cpu_samples)
    print(
        f"{label}: cpu avg {sum(cpu_samples)/n:.0f}% median"
        f" {cpu_samples[n//2]:.0f}% p95 {cpu_samples[int(n*0.95)]:.0f}%"
        f" (of one core), pss avg {sum(pss_samples)/len(pss_samples)/1024:.0f}MB"
        f" peak {max(pss_samples)/1024:.0f}MB, {n} samples"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
