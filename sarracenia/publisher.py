"""Publisher helpers that do not depend on configuration initialization."""

import copy


def publisher_identity(publisher):
    """Return the stable fields that identify a publishing destination."""
    identity = {}
    for key in ['broker', 'exchange', 'topicPrefix', 'format']:
        if key not in publisher:
            continue
        if key == 'broker':
            identity[key] = str(publisher[key])
        else:
            identity[key] = copy.deepcopy(publisher[key])
    return identity
