# This file is part of sarracenia.
# The sarracenia suite is Free and is proudly provided by the Government of Canada
# Copyright (C) Her Majesty The Queen in Right of Canada, Environment Canada, 2008-2015
#
# more info: https://github.com/MetPX/sarracenia
#
# Code originally contributed by:
#  Michel Grenier - Shared Services Canada
#  first shot     : Wed Jan 10 16:06:16 UTC 2018
#  re-factored beyond recognition by PSilva 2021. Don't blame Michel
#

from _codecs import decode, encode

import jsonpickle, os, os.path, sarracenia, sys, time

import logging

# class sarra/retry

logger = logging.getLogger(__name__)


class DiskQueue():
    """
    Process Persistent Queue...

    Persist messages to a file so that processing can be attempted again later.
    For safety reasons, want to be writing to a file ASAP.
    For performance reasons, all those writes need to be Appends.

    so continuous, but append-only io... with an occasional housekeeping cycle.
    to resolve them
   
    not clear if we need multi-task safety... just one task writes to each queue.

    retry_ttl how long 

    self.retry_cache 

    * a dictionary indexed by some sort of key to prevent duplicate messages being stored in it.

    retry_path = ~/.cache/sr3/<component>/<config>/diskqueue_<name>

    with various suffixes:

    .new -- messages added to the retry list are appended to this file.
            
    whenever a message is added to the retry_cache, it is appended to a 
    cumulative list of entries to add to the retry list.  

    every housekeeping interval, the two files are consolidated.

    note that the *ack_id* of messages retreived from the retry list, is 
    removed. Files must be acked around the time they are placed on the 
    retry_list, as reception from the source should have already been acknowledged.

    FIXME:  would be fun to look at performance of this thing and compare it to
        python persistent queue.  the differences:

        This class does no locking (presumed single threading.) 
        could add locks... and they would be coarser grained than stuff in persistentqueue
        this should be faster than persistent queue, but who knows what magic they did.
        This class doesn't implement in-memory queue... it is entirely on disk...
        saves memory, optimal for large queues.  
        probably good, since retries should be slow...

        not sure what will run better.
   
    """
    def __init__(self, options, name):

        logger.debug(' %s __init__', name)

        self.o = options

        self.name = name

        if not hasattr(self.o, 'retry_ttl'):
            self.o.retry_ttl = None

        #logging.basicConfig(format=self.o.logFormat,
        #                    level=getattr(logging, self.o.logLevel.upper()))
        logger.setLevel(getattr(logging, self.o.logLevel.upper()))

        logger.debug('name=%s logLevel=%s', self.name, self.o.logLevel)

        # initialize all retry path if retry_path is provided
        self.working_dir = os.path.dirname(self.o.pid_filename)

        if not os.path.isdir(self.working_dir):
            os.makedirs(self.working_dir)

        self.queue_file = self.working_dir + os.sep + 'diskqueue_' + name
        self.now = sarracenia.nowflt()

        # retry messages

        self.queue_fp = None

        # newer retries

        self.new_path = self.queue_file + '.new'
        self.new_fp = None

        # working file at housekeeping
        self.housekeeping_path = self.queue_file + '.hk'
        self.housekeeping_fp = None

        # initialize ages and message counts

        # msg_count is the number of messages available for retry in this interval
        self.msg_count = 0

        # inflight_count is the number of messages returned to the caller whose
        # processing outcome has not been recorded yet.
        self.inflight_count = 0

        # A completed queue can remain on disk when unlink fails. Keep that
        # cleanup state separate from messages whose outcome is still unknown.
        self.cleanup_pending = False

        # Stop live retries if an append or reader rollback cannot restore a
        # known record boundary. A later append retries a failed append
        # rollback before writing the retained batch.
        self.append_rollback_failed = False
        self.append_rollback_boundary = None
        self.reader_rollback_failed = False

        # msg_count_new is the number of messages added for retry in this interval
        #   ... new messages will only be available in the next interval.
        self.msg_count_new = 0

        if not os.path.isfile(self.queue_file):
            return

        retry_age = os.path.getmtime(self.queue_file)
        self.msg_count = self._count_msgs(self.queue_file)

        if os.path.isfile(self.new_path):
            new_age = os.path.getmtime(self.new_path)
            if retry_age > new_age:
                os.unlink(self.new_path)
            else:
                self.msg_count_new = self._count_msgs(self.new_path)



    def put(self, message_list):
        """
          add messages to the end of the queue.
        """

        if self.append_rollback_failed:
            if not self._restore_append_boundary():
                raise OSError("retry queue append is blocked after rollback failure")

        if self.new_fp is None:
            self.new_fp = open(self.new_path, 'a')

        # Flush earlier successful puts so the file size is a valid rollback
        # boundary for this call.
        self.new_fp.flush()
        append_start = os.fstat(self.new_fp.fileno()).st_size
        count_start = self.msg_count_new

        try:
            for message in message_list:
                logger.debug('DEBUG add to new file %s %s',
                             os.path.basename(self.new_path), message)
                record = self.msgToJSON(message)
                written = self.new_fp.write(record)
                if written != len(record):
                    raise OSError("short write to retry queue")
                self.msg_count_new += 1
            self.new_fp.flush()
        except BaseException:
            self._rollback_append(append_start, count_start)
            raise

    def _rollback_append(self, append_start, count_start) -> None:
        """Restore `.new` to the last complete append boundary."""
        writer = self.new_fp
        file_descriptor = None
        if writer is not None:
            try:
                file_descriptor = writer.fileno()
            except Exception:
                pass
            try:
                writer.close()
            except Exception:
                if file_descriptor is not None:
                    try:
                        os.close(file_descriptor)
                    except Exception:
                        pass

        self.new_fp = None
        self.msg_count_new = count_start
        self.append_rollback_boundary = (append_start, count_start)
        self._restore_append_boundary()

    def _restore_append_boundary(self) -> bool:
        """Retry restoring `.new` to the last known complete append boundary."""
        if self.append_rollback_boundary is None:
            return not self.append_rollback_failed

        append_start, count_start = self.append_rollback_boundary
        self.msg_count_new = count_start
        try:
            with open(self.new_path, 'r+b') as rollback_fp:
                rollback_fp.truncate(append_start)
        except Exception as err:
            self.append_rollback_failed = True
            logger.error("could not roll back retry append for %s: %s",
                         self.new_path, err)
            return False

        self.append_rollback_failed = False
        self.append_rollback_boundary = None
        return True

    def cleanup(self):
        """
          remove statefiles.
        """
        if os.path.exists(self.queue_file):
            os.unlink(self.queue_file)
        self.msg_count = 0
        self.inflight_count = 0
        self.cleanup_pending = False
        self.append_rollback_failed = False
        self.append_rollback_boundary = None
        self.reader_rollback_failed = False

    def close(self):
        """
           clean shutdown.
        """
        try:
            self.housekeeping_fp.close()
        except Exception as err:
            logger.debug(f"housekeeping_fp close: {err}")
        try:
            self.new_fp.flush()
            os.fsync(self.new_fp.fileno())
            self.new_fp.close()
        except Exception as err:
            logger.debug(f"new_fp close: {err}")
        try:
            self.queue_fp.close()
        except Exception as err:
            logger.debug(f"queue_fp close: {err}")
        self.housekeeping_fp = None
        self.new_fp = None
        self.queue_fp = None
        self.msg_count = 0
        self.msg_count_new = 0
        self.inflight_count = 0
        self.cleanup_pending = False

    def _count_msgs(self, file_path) -> int:
        """Count the number of messages (lines) in the queue file. This should be used only when opening an existing
        file, because :func:`~sarracenia.diskqueue.DiskQueue.get` does not remove messages from the file.

        Args:
            file_path (str): path to the file to be counted.

        Returns:
            int: count of messages in file, -1 if the file could not be read.
        """
        count = -1

        if os.path.isfile(file_path):
            count = 0
            with open(file_path, mode='r') as f:
                for line in f:
                    if "{" in line:
                        count +=1
            logger.debug('counted %s msgs in %s', count, file_path)

        return count

    def __len__(self) -> int:
        """Returns the total number of messages in the DiskQueue.

        Number of messages in the DiskQueue does not necessarily equal the number of messages available to ``get``.
        Messages in the .new file are counted, but can't be retrieved until
        :func:`~sarracenia.diskqueue.DiskQueue.on_housekeeping` has been run.

        Returns:
            int: number of messages in the DiskQueue.
        """
        return max(0,self.msg_count) + self.msg_count_new

    def msgFromJSON(self, line):
        try:
            msg = jsonpickle.decode(line)
        except ValueError:
            logger.error(f"corrupted line in retry file: {line} ")
            logger.debug("Error information: ", exc_info=True)
            return None

        if type(msg) is not sarracenia.Message:
            logger.error(f"invalid line in retry file (not decoded as sarracenia.Message): {line}")
            return None

        return msg

    def msgToJSON(self, message):
        return jsonpickle.encode(message) + '\n'

    def get(self, maximum_messages_to_get=1):
        """
           qty number of messages to retrieve from the queue.

           no housekeeping in get ...
           if no message (and new or state file there)
           we wait for housekeeping to present retry messages
        """
        if self.msg_count == 0 and self.queue_fp is None:
            return []

        if self.reader_rollback_failed:
            raise OSError("retry queue reader is blocked after rollback failure")

        if self.queue_fp is None:
            if not os.path.isfile(self.queue_file):
                return []
            logger.debug('DEBUG %s open read', self.queue_file)
            self.queue_fp = open(self.queue_file, 'r')

        read_start = self.queue_fp.tell()
        count_start = self.msg_count
        inflight_start = self.inflight_count

        ml = []
        count = 0
        try:
            while count < maximum_messages_to_get:
                self.queue_fp, message = self.msg_get_from_file(
                    self.queue_fp, self.queue_file)

                # FIXME MG as discussed with Peter
                # no housekeeping in get ...
                # if no message (and new or state file there)
                # we wait for housekeeping to present retry messages
                if not message:
                    self.msg_count = 0
                    if self.inflight_count == 0:
                        self.cleanup_pending = not self._remove_queue_file()
                    #logger.debug("MG DEBUG retry get return None")
                    break

                if self.is_expired(message):
                    self.msg_count -= 1
                    #logger.error("MG invalid %s" % message)
                    continue

                if 'ack_id' in message:
                    del message['ack_id']
                    message['_deleteOnPost'].remove('ack_id')

                ml.append(message)
                count += 1
                self.msg_count -= 1
                self.inflight_count += 1
        except BaseException:
            self.msg_count = count_start
            self.inflight_count = inflight_start
            self._restore_reader(read_start)
            raise

        # A final batch remains in the queue file until the caller confirms
        # that processing completed or that failures were safely requeued.
        if self.msg_count == 0 and self.inflight_count == 0:
            self.cleanup_pending = not self._remove_queue_file()

        return ml

    def _restore_reader(self, read_start) -> None:
        """Restore the reader to the start of a failed `get()` call."""
        try:
            if self.queue_fp is not None:
                self.queue_fp.seek(read_start)
                return
        except Exception:
            try:
                self.queue_fp.close()
            except Exception:
                pass

        try:
            self.queue_fp = open(self.queue_file, 'r')
            self.queue_fp.seek(read_start)
        except Exception as err:
            self.queue_fp = None
            self.reader_rollback_failed = True
            logger.error("could not restore retry reader for %s: %s",
                         self.queue_file, err)

    def _remove_queue_file(self) -> bool:
        """Close and remove the current queue file when it is safe to retire."""
        try:
            if self.queue_fp is not None:
                self.queue_fp.close()
        except Exception as err:
            logger.debug("queue_fp close: %s", err)
        self.queue_fp = None

        try:
            os.unlink(self.queue_file)
        except FileNotFoundError:
            return True
        except Exception as err:
            logger.warning("could not remove completed retry queue %s: %s",
                           self.queue_file, err)
            return False
        return True

    def complete(self, message_count) -> bool:
        """Record completed messages and retire an empty queue file.

        The caller invokes this only after each returned message has either
        completed or been written back to a retry queue.
        """
        if message_count < 0 or message_count > self.inflight_count:
            raise ValueError("cannot complete %s messages with %s in flight" %
                             (message_count, self.inflight_count))

        self.inflight_count -= message_count
        if self.inflight_count == 0 and self.msg_count == 0:
            self.cleanup_pending = not self._remove_queue_file()
        return True

    def in_cache(self, message) -> bool:
        """
          return whether the entry is message is in the cache or not.
          side effect: adds it.

        """
        urlstr = message['baseUrl'] + '/' + message['relPath']

        if 'noDupe' in message:
            sumstr = jsonpickle.encode(message['noDupe']['key'])
        elif 'fileOp' in message:
            sumstr = jsonpickle.encode(message['fileOp'])
        elif 'identity' in message:
            sumstr = jsonpickle.encode(message['identity'])
        elif 'pubTime' in message:
            sumstr = jsonpickle.encode(message['pubTime'])
        else:
            logger.warning('no key found for message, cannot add')
            return False

        cache_key = urlstr + ' ' + sumstr

        if 'parts' in message:
            cache_key += ' ' + message['parts']

        if cache_key in self.retry_cache: return True
        self.retry_cache[cache_key] = True
        return False

    def is_expired(self, message) -> bool:
        """
          return is the given message expired ?
        """
        # no expiry
        if self.o.retry_ttl is None: return False
        if self.o.retry_ttl <= 0: return False

        # compute message age
        msg_time = sarracenia.timestr2flt(message['pubTime'])
        msg_age = self.now - msg_time

        # expired ?
        return msg_age > self.o.retry_ttl

    def needs_requeuing(self, message) -> bool:
        """
           return 
           * True if message is not expired, and not already in queue. 
           * False otherwise.   
        """
        if self.in_cache(message):
            logger.info( f"discarding duplicate message (in {self.name} cache) {message}" )
            return False

        # log is info... it is good to log a retry message that expires
        if self.is_expired(message):
            logger.info(f"discarding expired message in ({self.name}): {message}")
            return False

        return True

    def msg_get_from_file(self, fp, path):
        """
            read a message from the state file.
        """
        if fp is None:
            if not os.path.isfile(path): return None, None
            logger.debug('DEBUG %s open read', path)
            fp = open(path, 'r')

        while True:
            line = fp.readline()
            if not line:
                try:
                    fp.close()
                except Exception:
                    pass
                return None, None

            msg = self.msgFromJSON(line)
            if msg is not None:
                return fp, msg
            # corrupted line, skip to next

    def on_housekeeping(self):
        """

           read rest of queue_file (from current point of unretried ones.)
                 - check if message is duplicate or expired.
                 - write to .hk

           read .new file, 
                 - check if message is duplicate or expired.
                 - writing to .hk (housekeeping)

           remove .new
           rename housekeeping to queue for next period.
        """
        logger.debug('%s on_housekeeping, %s msgs in queue file, %s in new file', self.name, self.msg_count, self.msg_count_new)

        if self.cleanup_pending:
            if not self._remove_queue_file():
                return
            self.cleanup_pending = False

        if self.append_rollback_failed:
            logger.error("retry housekeeping is blocked after append rollback failure for %s",
                         self.new_path)
            return

        # finish retry before reshuffling all retries entries

        if (os.path.isfile(self.queue_file) and self.queue_fp != None) \
                or self.msg_count != 0 or self.inflight_count != 0:
            logger.info(f"still {self.msg_count} messages in {self.name} list. Resuming retries with {self.queue_file}")
            return

        self.now = sarracenia.nowflt()
        self.retry_cache = {}
        N = 0

        # put this in try/except in case ctrl-c breaks something

        try:
            self.close()
            try:
                os.unlink(self.housekeeping_path)
            except Exception:
                pass
            fp = open(self.housekeeping_path, 'w')
            fp.close()

            i = 0
            last = None

            fp = self.queue_fp
            self.housekeeping_fp = open(self.housekeeping_path, 'a')

            logger.debug('has queue %s', os.path.isfile(self.queue_file))

            # remaining of retry to housekeeping
            while True:
                fp, message = self.msg_get_from_file(fp, self.queue_file)
                if not message: break
                i = i + 1
                if not self.needs_requeuing(message): continue
                self.housekeeping_fp.write(self.msgToJSON(message))
                N = N + 1

            try:
                fp.close()
            except Exception:
                pass

            i = 0
            j = N

            fp = None
            # append new to housekeeping.
            while True:
                fp, message = self.msg_get_from_file(fp, self.new_path)
                if not message: break
                i = i + 1
                logger.debug('DEBUG message %s', message)
                if not self.needs_requeuing(message): continue

                #logger.debug("MG DEBUG flush retry to state %s" % message)
                self.housekeeping_fp.write(self.msgToJSON(message))
                N = N + 1
            try:
                fp.close()
            except Exception:
                pass

            logger.debug('retrieved %d from the %d retry', N - j, i)

            self.housekeeping_fp.close()

        except Exception as Err:
            logger.error("something went wrong")
            logger.debug('Exception details: ', exc_info=True)

        # no more retry

        self.msg_count = N
        if N == 0:
            logger.debug('%s No retry in list', self.name)
            try:
                os.unlink(self.housekeeping_path)
            except Exception:
                pass

        # housekeeping file becomes new retry

        else:
            logger.info( f"{self.name} Number of messages in retry list {N:d}" )
            try:
                os.rename(self.housekeeping_path, self.queue_file)
            except Exception:
                logger.error("Something went wrong with rename")

        # cleanup
        self.msg_count_new = 0
        try:
            os.unlink(self.new_path)
        except Exception:
            pass

        elapse = sarracenia.nowflt() - self.now
        logger.debug('on_housekeeping elapse %f', elapse)
