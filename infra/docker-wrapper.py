#!/usr/bin/python3
"""Translate only the Actions well-known Docker socket bind to the isolated daemon."""
import os
import sys


def translate(args):
    result = list(args)
    for index in range(1, len(result)):
        if result[index - 1] in {'-v', '--volume'} and result[index].startswith('/var/run/docker.sock:'):
            result[index] = '/run/kodify-ci/docker.sock:' + result[index].split(':', 1)[1]
    return result


if __name__ == '__main__':
    os.execv('/usr/bin/docker', ['/usr/bin/docker', *translate(sys.argv[1:])])
