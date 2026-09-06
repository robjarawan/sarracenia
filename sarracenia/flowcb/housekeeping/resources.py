"""
Default on_housekeeping handler that:
- Logs Memory and CPU usage.
- Restarts components to deal with memory leaks.

If `MemoryMax` is not in the config, it is automatically calculated with the following procedure:

1. The plugin processes the first `MemoryBaseLineFile` items to reach a steady state.
   - Subscribers process messages-in
   - Posting programs process messages-posted
2. Set `MemoryMax` threshold to `MemoryMultiplier` * (memory usage at time steady state)

If memory use ever exceeds the `MemoryMax` threshold, then the plugin triggers a restart, reducing memory consumption.

Parameters:

MemoryMax : size (default: none)
    Hard coded maximum for tolerable memory consumption.
    Must be suffixed with k/m/g for Kilo/Mega/Giga byte values.
    If not set then the following options will have an effect:

MemoryBaseLineFile : int, optional (default: 100)
    How many files to process before measuring to establish the baseline memory usage.
    (how many files are expected to process before a steady state is reached)

MemoryMultiplier : int, optional (default: 3)
    How many times past the steady state memory footprint you want to allow the component to grow before restarting.
    It could be normal for memory usage to grow, especially if plugins store data in memory.


Returns:
    Nothing, restarts components if memory usage is outside of configured thresholds.
"""

import logging

import os, socket, time, random
# resource library only available on Unix systems
try:
    import resource
except ImportError:
    resource = None
from sarracenia.flowcb import FlowCB
from sarracenia import naturalSize, naturalTime, user_cache_dir, nowstr, timestr2flt
from sarracenia.featuredetection import features

if features['process']['present']:
    import psutil

import sys

logger = logging.getLogger(__name__)

class Resources(FlowCB):
    def __init__(self, options):
        super().__init__(options,logger)
        # Set option to neg value to determine if user set in config
        self.o.add_option('MemoryMax', 'size', '0')
        self.o.add_option('MemoryBaseLineFile', 'count', 100)
        self.o.add_option('MemoryMultiplier', 'float', 3)

        self.threshold = None
        ''' Per-process maximum memory footprint that is considered too large, forcing a process restart.'''
        self.transferCount = 0
        self.msgCount = 0
        self.randomSleep = 1 + round(random.random(), 2)

    def _restart_owner_state(self):
        process_start = None
        if features['process']['present']:
            try:
                process_start = psutil.Process().create_time()
            except (psutil.Error, OSError):
                pass

        if process_start is None:
            return f'{os.getpid()} {nowstr()}'
        return f'{os.getpid()} {process_start} {nowstr()}'

    def _restart_owner_is_alive(self, state):
        fields = state.split()
        try:
            owner_pid = int(fields[0])
        except IndexError:
            return None
        except ValueError:
            try:
                timestr2flt(state.strip())
                return None
            except (IndexError, TypeError, ValueError):
                return False
        if owner_pid <= 0:
            return None

        if features['process']['present'] and len(fields) >= 3:
            try:
                owner_start = float(fields[1])
                process_start = psutil.Process(owner_pid).create_time()
                return abs(process_start - owner_start) < 0.01
            except (ValueError, psutil.NoSuchProcess):
                return False
            except psutil.AccessDenied:
                return True
            except (psutil.Error, OSError):
                return True

        if os.name != 'posix':
            return True
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _acquire_restart_state_file(self):
        while True:
            try:
                with open(self.state_file, 'x') as state_file:
                    state_file.write(self._restart_owner_state())
                return
            except FileExistsError:
                logger.info(
                    f'State file already exists: {self.state_file}. '
                    f'Waiting {self.randomSleep} seconds.'
                )
                time.sleep(self.randomSleep)

            try:
                with open(self.state_file) as state_file:
                    owner_state = state_file.read()
            except FileNotFoundError:
                continue

            owner_is_alive = self._restart_owner_is_alive(owner_state)
            if owner_is_alive:
                continue
            if owner_is_alive is None:
                try:
                    marker_age = time.time() - os.path.getmtime(self.state_file)
                except FileNotFoundError:
                    continue
                if marker_age < 60:
                    continue

            try:
                with open(self.state_file) as state_file:
                    if state_file.read() != owner_state:
                        continue
                os.unlink(self.state_file)
                logger.warning(f'Removed orphaned restart state file: {self.state_file}')
            except FileNotFoundError:
                pass

    def on_housekeeping(self):
        if features['process']['present']:
            mem = psutil.Process().memory_info().vms
        else:
            mem = 0

        ost = os.times()
        logger.info(f"Current cpu_times: user={ost.user} system={ost.system}")

        # We must set a threshold **after** the config file has been parsed.
        if self.threshold is None:
            # If the config set something, use it.
            if self.o.MemoryMax != 0:
                self.threshold = self.o.MemoryMax

            if self.threshold is None:
                # No user input set, now to figure out what our baseline memory usage is at a steady state
                #   Process MemoryBaseLineFile(s)+ then get a memory reading before setting memory restart threshold.
                if (self.transferCount < self.o.MemoryBaseLineFile) and (
                        self.msgCount < self.o.MemoryBaseLineFile):
                    # Not enough files processed for steady state, continue to wait..
                    logger.info(
                        f"Current mem usage: {naturalSize(mem)}, accumulating count "
                        f"({self.transferCount} or {self.msgCount}/{self.o.MemoryBaseLineFile} so far) "
                        f"before self-setting threshold")
                    return True

                self.threshold = int(self.o.MemoryMultiplier * mem)

            logger.info(f"Memory threshold set to: {naturalSize(self.threshold)}")

        logger.info(
            f"Current Memory usage: {naturalSize(mem)} / "
            f"{naturalSize(self.threshold)} = {(mem/self.threshold):.2%}"
        )

        if mem > self.threshold:
            self.restart()
        # self.restart()

        return True

    def restart(self):
        """
        Do an in-place restart of the current process (keeps pid).
        Gets a new memory stack/heap, keeps all file descriptors but replaces the buffers.
        """
        logger.info(
            f"Memory threshold surpassed! Triggering a restart for '{sys.argv}' via '{sys.executable}'"
        )
        # First arg must be the program to be run (absolute path to program)
        # Second arg has to be python for windows, see how this affects the linux side of things..
        # Third arg is the name of the program you wish to run (should be full path to script) plus all the args.
        #   The star unpacks the sys.argv list into the remaining function args

        # Before triggering a restart, add a state file to prevent other processes to stop/start it at the same time.
        self.state_file = self.o.cfg_run_dir + os.sep + 'resources_restart'

        # If another process is performing an OOM restart, wait until it is complete
        # before creating a new state file to avoid a race condition.
        self._acquire_restart_state_file()


        if sys.platform.startswith(('linux', 'cygwin', 'darwin', 'aix')):
            if resource is not None:
                # Flush buffered output before exec replaces the process.
                sys.stdout.flush()
                sys.stderr.flush()

                # Close inherited file descriptors (sockets, pipes, open files) that
                # os.execl would otherwise leak into the new process image.
                # Keep stdin/stdout/stderr (0-2) open.
                try:
                    max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
                    if max_fd == resource.RLIM_INFINITY:
                        max_fd = 1024
                    os.closerange(3, max_fd)
                except Exception:
                    logger.debug('fd cleanup before execl failed', exc_info=True)

            # Unix* (Linux / Windows/Cygwin / MacOS / AIX) Specific restart
            os.execl(sys.executable, sys.executable, *sys.argv)
        elif sys.platform.startswith('win32'):
            # Windows Specific restart
            os.execl(sys.executable, 'python', *sys.argv)
        else:
            logger.error(
                f'Unknown platform type: "{sys.platform}", attempting default unix process restart..'
            )
            os.execl(sys.executable, sys.executable, *sys.argv)

        # Scream out in agony and die
        logger.critical(
            f'Plugin resources.py:restart() "execl" failed, this should never be logged.'
        )
        exit(1)

    def after_work(self, worklist):
        self.transferCount += len(worklist.ok)
        # if self.threshold is not None:
        #    TODO: Remove this callback when issue #444 is implemented

    def after_accept(self, worklist):
        self.msgCount += len(worklist.incoming)
        # if self.threshold is not None:
        #    TODO: Remove this callback when issue #444 is implemented
