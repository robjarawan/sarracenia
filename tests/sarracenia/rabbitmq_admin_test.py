import pytest
from tests.conftest import *
#from unittest.mock import Mock

import sarracenia.config
import sarracenia.rabbitmq_admin
import urllib.parse


def test_exec_rabbitmqadmin_returns_on_command_build_failure():
    """A broker url with no username makes command building raise TypeError.

       The except handler must report that failure and return the documented
       (0, None), not raise UnboundLocalError on cmdlst before it is assigned.
    """
    url = urllib.parse.urlparse("amqp://host/")

    assert sarracenia.rabbitmq_admin.exec_rabbitmqadmin(url, "list queues", simulate=True) == (0, None)
