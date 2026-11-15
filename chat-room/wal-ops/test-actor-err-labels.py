#!/usr/bin/env python3
"""Gate for the `actor_err` label rule in fanotify-close-actor.py.

Why this file exists: the residual it pins (2026-11-15T02:28:04, `cp`,
cmdline="", actor_state=R, actor_err=null) was a row asserting a false fact, and
the shape it came from -- a userspace task whose argv was never read alive -- is
too rare to reproduce on demand against a millisecond-long copier. So the RULE is
tested here as a pure function, and the live arm only has to show the invariant
(`cmdline:""` with `actor_err:null` is a kernel thread).

Runs unprivileged: it imports the module and reads /proc, nothing more.

    python3 test-actor-err-labels.py        # rc=0 on success
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "fanotify-close-actor.py")

spec = importlib.util.spec_from_file_location("fanotify_close_actor", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TestEmptyArgvLabel(unittest.TestCase):

    def test_exit_window_unseen_for_every_live_state(self):
        # comm readable, state known, cgroup readable, not a kernel thread ->
        # the argv is gone, not absent. R is the measured residual; D/S/t are
        # the same window at other scheduler states.
        for st in ("R", "D", "S", "t", "I"):
            with self.subTest(state=st):
                self.assertEqual(mod.empty_argv_label(st, None, False),
                                 "EXIT_WINDOW_UNSEEN")

    def test_gone_race_when_state_was_unreadable(self):
        self.assertEqual(mod.empty_argv_label(None, None, False), "GONE_RACE")

    def test_gone_race_when_cgroup_read_lost_its_subject(self):
        # pass-2 cgroup read: ENOENT = task released between the two passes.
        for st in ("R", "D", None):
            with self.subTest(state=st):
                self.assertEqual(mod.empty_argv_label(st, "ENOENT", False),
                                 "GONE_RACE")

    def test_kernel_thread_keeps_an_empty_cmdline_as_a_fact(self):
        # A kernel thread has no argv for its whole life: "" is TRUE here, so
        # actor_err stays null and the row keeps cmdline "".
        for st in ("R", "S", "D"):
            with self.subTest(state=st):
                self.assertIsNone(mod.empty_argv_label(st, None, True))

    def test_kthread_outranks_a_lost_cgroup(self):
        # Even if the pass-2 read raced, a kernel thread's empty argv is never
        # an exit window; GONE_RACE would be the wrong story about it.
        self.assertIsNone(mod.empty_argv_label("R", "ENOENT", True))

    def test_label_is_never_the_unlabelled_null(self):
        # The whole point: no userspace shape may resolve to None.
        for st in (None, "R", "D", "S", "t", "I"):
            for uerr in (None, "ENOENT"):
                with self.subTest(state=st, uerr=uerr):
                    self.assertIsNotNone(mod.empty_argv_label(st, uerr, False))


class TestPidStatCarriesFlags(unittest.TestCase):

    def test_own_stat_is_a_triple_with_a_live_state(self):
        st, start, flags = mod._pid_stat(os.getpid())
        self.assertIn(st, ("R", "S", "D", "t"))
        self.assertGreater(start, 0)
        self.assertEqual(flags & mod.PF_KTHREAD, 0,
                         "a python test process is not a kernel thread")

    def test_pid_start_still_returns_field_22(self):
        self.assertEqual(mod._pid_start(os.getpid()),
                         mod._pid_stat(os.getpid())[1])

    def test_a_kernel_thread_carries_the_flag(self):
        # pid 2 is [kthreadd] wherever CONFIG_KTHREAD exists; skip rather than
        # fail on a kernel that does not expose it.
        if not os.path.exists("/proc/2/stat"):
            self.skipTest("no /proc/2")
        st, start, flags = mod._pid_stat(2)
        if st is None:
            self.skipTest("/proc/2 unreadable")
        self.assertTrue(flags & mod.PF_KTHREAD,
                        "PF_KTHREAD must be set for [kthreadd] (flags=%#x)" % flags)

    def test_gone_pid_reads_as_three_nones(self):
        self.assertEqual(mod._pid_stat(4194303), (None, None, None))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=2).result
    n = r.testsRun
    bad = len(r.failures) + len(r.errors)
    skipped = len(r.skipped)
    print("actor-err label gate: tests=%d fail=%d skipped=%d src=%s rc=%d"
          % (n, bad, skipped, os.path.basename(SRC), 0 if r.wasSuccessful() else 1))
    sys.exit(0 if r.wasSuccessful() else 1)
