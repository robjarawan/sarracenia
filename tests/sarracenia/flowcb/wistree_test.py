import pytest
from tests.conftest import *
#from unittest.mock import Mock

pytest.importorskip("GTStoWIS2", reason="GTStoWIS2 package not installed")

import sarracenia.config
import sarracenia.flowcb.wistree