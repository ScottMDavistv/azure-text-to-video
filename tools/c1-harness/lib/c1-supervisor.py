"""C1 harness process supervisor (L-A).

Usage: python3 -I c1-supervisor.py SESSION REAPED_REGISTER NAME -- CMD [ARG...]

One supervisor runs for every c1_spawn. It:

1. marks itself a child subreaper (prctl PR_SET_CHILD_SUBREAPER), so every
   descendant that is orphaned (an intermediate ancestor exits, or a child
   scrubs its environment and calls setsid) is re-parented to this process
   instead of PID 1 and stays attributable to the session's process tree;
2. appends its own "pid starttime" identity to SESSION/supervisors BEFORE it
   forks, so no descendant can exist while its supervisor is unrecorded;
3. forks CMD in a new session (the setsid leader c1_spawn returns), and
   writes "childpid childstart" to SESSION/tmp/spawn.<pid> for c1_spawn;
4. stays alive and reaps until waitpid() reports ECHILD, i.e. until no
   descendant of any depth remains (every orphan is re-parented to it);
5. only then appends its identity to REAPED_REGISTER. That line is the
   positive proof c1_verify_cleanup needs: a recorded supervisor that is gone
   WITHOUT this proof (it was SIGKILLed, or crashed) means descendants may have
   escaped to PID 1, so cleanup verification fails closed.

Signals: SIGTERM/SIGINT/SIGHUP forward SIGTERM to every current descendant.
SIGUSR1 switches to kill mode: every descendant (including any created or
adopted later) is SIGKILLed until none remain. Signals are delivered through
pidfds after re-checking the kernel start time, so a reused PID is never hit.
The supervisor never signals anything outside its own descendant tree.
"""

import ctypes
import fcntl
import os
import signal
import sys
import time

PR_SET_CHILD_SUBREAPER = 36


def stat_fields(pid):
    """Fields after "pid (comm) ": [0]=state [1]=ppid ... [19]=starttime."""
    with open(f"/proc/{pid}/stat", "rb") as handle:
        data = handle.read()
    return data[data.rindex(b")") + 2:].split()


def start_time(pid):
    return int(stat_fields(pid)[19])


def append_line(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        data = line.encode()
        if os.write(fd, data) != len(data):
            raise OSError("short write")
    finally:
        os.close(fd)


def descendants(root):
    children = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            fields = stat_fields(name)
            children.setdefault(int(fields[1]), []).append((int(name), int(fields[19])))
        except (OSError, ValueError, IndexError):
            continue
    found, stack = [], [root]
    while stack:
        for pid, start in children.get(stack.pop(), []):
            found.append((pid, start))
            stack.append(pid)
    return found


def signal_identity(pid, start, sig):
    try:
        pidfd = os.pidfd_open(pid)
    except (AttributeError, OSError):
        return
    try:
        if start_time(pid) != start:          # reused or exited: never signal
            return
        signal.pidfd_send_signal(pidfd, sig)
    except (OSError, ValueError, IndexError):
        pass
    finally:
        os.close(pidfd)


def signal_tree(sig):
    for pid, start in descendants(os.getpid()):
        signal_identity(pid, start, sig)


STATE = {"kill": False}


def on_term(_signum, _frame):
    signal_tree(signal.SIGTERM)


def on_kill(_signum, _frame):
    STATE["kill"] = True
    signal_tree(signal.SIGKILL)


def main(argv):
    if len(argv) < 6 or argv[4] != "--":
        print("usage: c1-supervisor.py SESSION REAPED_REGISTER NAME -- CMD...", file=sys.stderr)
        return 125
    session, reaped, cmd = argv[1], argv[2], argv[5:]
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        print(f"c1-supervisor: prctl(PR_SET_CHILD_SUBREAPER) failed: errno {ctypes.get_errno()}", file=sys.stderr)
        return 125
    me = os.getpid()
    my_start = start_time(me)
    # Serialize registration and the fork with cleanup. If cleanup deleted
    # the directory while we waited, registration fails before any fork.
    gate = os.open(os.path.join(session, ".spawn-lock"),
                   os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    fcntl.flock(gate, fcntl.LOCK_EX)
    try:
        append_line(os.path.join(session, "supervisors"), f"{me} {my_start}\n")
    except OSError as exc:
        print(f"c1-supervisor: cannot record supervisor identity: {exc}", file=sys.stderr)
        return 125
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_term)
    signal.signal(signal.SIGUSR1, on_kill)
    if argv[3] == "websockify" and os.environ.get("C1_GUARD_STARTUP") == "1":
        def tick():
            with open("/proc/uptime", encoding="ascii") as uptime:
                return int(uptime.read().split()[0].replace(".", ""))

        started = tick()
        with open(os.path.join(session, "startup.tmp"), "x", encoding="ascii") as record:
            record.write(f"{me} {my_start} {started}\n")
        os.replace(os.path.join(session, "startup.tmp"), os.path.join(session, "startup"))
        while not os.path.isfile(os.path.join(session, "startup-watched")) and tick() < started + 150:
            time.sleep(0.01)
        if STATE["kill"] or tick() >= started + 150:
            append_line(reaped, f"{me} {my_start}\n")
            return 125

    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1})
    child = os.fork()
    if child == 0:
        try:
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1):
                signal.signal(sig, signal.SIG_DFL)
            signal.pthread_sigmask(signal.SIG_SETMASK, set())
            os.setsid()
            os.execvp(cmd[0], cmd)
        except BaseException as exc:  # noqa: BLE001 - must never return into the supervisor
            try:
                os.write(2, f"c1-supervisor: cannot execute {cmd[0]}: {exc}\n".encode())
            finally:
                os._exit(127)
    rc = 0
    try:
        child_start = start_time(child)       # readable even if the child is already a zombie
        tmp = os.path.join(session, "tmp", f".spawn.{me}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.write(fd, f"{child} {child_start}\n".encode())
        finally:
            os.close(fd)
        os.rename(tmp, os.path.join(session, "tmp", f"spawn.{me}"))
    except OSError as exc:
        print(f"c1-supervisor: cannot publish child identity: {exc}", file=sys.stderr)
    fcntl.flock(gate, fcntl.LOCK_UN)
    os.close(gate)
    signal.pthread_sigmask(signal.SIG_SETMASK, set())
    while True:
        try:
            flags = os.WNOHANG if STATE["kill"] else 0
            pid, status = os.waitpid(-1, flags)
        except ChildProcessError:
            break                              # ECHILD: no descendant of any depth remains
        except InterruptedError:
            continue
        if pid == child:
            rc = os.waitstatus_to_exitcode(status)
            rc = 128 - rc if rc < 0 else rc
        if pid == 0:
            signal_tree(signal.SIGKILL)
            time.sleep(0.05)
    try:
        append_line(reaped, f"{me} {my_start}\n")
    except OSError as exc:
        print(f"c1-supervisor: cannot record reaping proof: {exc}", file=sys.stderr)
        return 125
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
