"""Make graceful shutdown overlap the replacement in the unfixed sanity path."""

import time


class DelayedStop:
    def __init__(self, options):
        self.options = options

    def on_stop(self) -> None:
        if self.options.no > 0:
            time.sleep(1)
