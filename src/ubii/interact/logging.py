from __future__ import annotations

import argparse
import logging.config
import re
import typing as t
from collections import namedtuple
from importlib.resources import read_text

import sys
import yaml

import ubii.proto as ub
from . import util

__config__ = yaml.safe_load(read_text(util, 'logging_config.yaml'))


class _logging_setup:
    log_config = namedtuple('log_config', ['config', 'level', 'warning_filter'])

    def __init__(self, base_config=__config__, log_level=logging.INFO, warning_filter: str = 'default'):
        self.base_config = self.log_config(config=base_config, warning_filter=warning_filter, level=log_level)
        self._configs: t.List[_logging_setup.log_config] = [self.base_config]
        self._apply()

    @property
    def effective_config(self):
        return self.log_config(**self._configs[-1]._asdict())

    def change(self, config: t.Dict | None = None, verbosity: int | None = None):
        if config is None and verbosity is None:
            raise ValueError(f"All arguments are None, can' apply change to logging.")

        verbosity = self.effective_config.level if verbosity is None else verbosity

        config = config or {}

        if config.get('incremental', False):
            config = util.merge_dicts(self.effective_config.config, config)
            config['incremental'] = False  # we do the merging, logging framework should just eat the config

        config = config or self.effective_config.config
        config['version'] = 1  # other versions are not supported by the logging framework

        self._configs.append(self.log_config(
            level=verbosity,
            config=config,
            warning_filter=self.effective_config.warning_filter
        ))
        self._apply()
        return self

    def _apply(self):
        logging.config.dictConfig(self.effective_config.config)
        logging.captureWarnings(True)
        logging.getLogger().setLevel(level=self.effective_config.level)

        if not sys.warnoptions:
            import os, warnings
            warnings.simplefilter(self.effective_config.warning_filter)  # Change the filter in this process
            os.environ["PYTHONWARNINGS"] = self.effective_config.warning_filter  # Also affect subprocesses

    def reset(self):
        self._configs[:] = self._configs[0]
        self._apply()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        if any(exc_info):
            self.reset()
        else:
            _ = self._configs.pop()
            self._apply()


logging_setup = _logging_setup(base_config=__config__, log_level=logging.INFO, warning_filter='default')


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', '-v', action='count', default=0)
    parser.add_argument('--debug', action='store_true', default=False)
    parser.add_argument('--config', action='store', default=None)
    parser.add_argument('--log-config', action='store', default=__config__)
    parser.add_argument('--processing-modules', action='store', default=[])
    args = parser.parse_args()

    logging_setup.change(config=args.log_config, verbosity=logging.ERROR - 10 * args.verbose)
    debug(args.debug)

    return args


def shorten_json(message: str, max_len=50):
    """
    Format json strings (like representations of proto messages) in a nice way.

    :param message:
    :param max_len:
    :return:
    """
    cleaned = message.strip()
    formatted = re.sub(r'{\s+', '{', cleaned)
    formatted = re.sub(r'\n', ' | ', formatted)
    formatted = re.sub(r'\s', '', formatted)
    placeholder = ' ... '

    def _format_final(_result):
        _result = re.sub(r'\|', ', ', _result)
        total = max_len - len(placeholder)
        if placeholder in _result:
            return _result
        else:
            return _result[:total // 2] + placeholder + _result[total // 2:]

    if '|' not in formatted:
        return _format_final(formatted)

    result = ''
    left = 0

    while len(result) < max_len:
        try:
            left = formatted.index('|', left) + 1
        except ValueError:
            break

        result = formatted[:left + 1] + placeholder + formatted[-1]

    return _format_final(result)


class ProtoFormatMixin:
    _MAX_REPR_LEN = 100

    def __str__(self: ub.ProtoMessage):
        contents = shorten_json(super().__str__(), max_len=self._MAX_REPR_LEN)
        return f"<{type(self).__name__}{' ' + contents if contents else ''}>"


__DEBUG__ = False


def debug(enabled: bool | None = None):
    """
    Call without arguments to get current debug state, pass truthy value to set debug mode.

    :param enabled: If passed, turns debug mode on or off
    :return:
    """
    global __DEBUG__
    if enabled is not None:
        __DEBUG__ = bool(enabled)

    return __DEBUG__
